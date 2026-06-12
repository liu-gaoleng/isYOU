"""阶段 5：摘要（summarize）。

阶段 1.4（iOS-first 调整后）：
- 对 status=clustered 的事件生成摘要（含未生成 EventContent 的事件）；
- 优先调 LLM（settings.llm.enabled）：让 LLM 一次性产出 iOS 卡片摘要 (≤120 字)
  与详情摘要 (300–500 字) 双字段；
- LLM 失败 / 未配置则回退抽取式兜底，同样产出 card+detail 双字段；
- 双摘要直接写到 events 表的 card_summary / detail_summary，供 FastAPI 直读；
- 同时把双摘要写入 event_contents.summary（兼容既有结构）；
- 推进事件状态 clustered → summarized。

阶段 3.1（本次）：LLM 调用改走统一 ``services.llm_client``（限流 + 重试 + 退避）。
阶段 3.2（本次）：同事件缓存 + 调用留痕：
- 内容指纹 = sha1(排序后的成员 article_id 列表 + 主文章标题)；
- 若该事件最新版 EventContent 的 ``llm_meta.fingerprint`` 与当前一致，跳过重算（不烧 token）；
- 每次产出都把 fingerprint + prompt_version + usage 写进 llm_meta，便于成本核算与回放。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import (
    Event,
    EventContent,
    EventStatus,
    RawArticle,
    SourceLevel,
    get_session,
)
from content_engine.services.llm_client import get_llm_client

from .seed_data import LEVEL_WEIGHT
from .utils import extractive_summary

_DISCLAIMER = "本内容仅作信息聚合，不构成任何投资建议。"

# Prompt 版本号：改 Prompt 时 +1，便于回放与 A/B（写进 llm_meta.prompt_version）
SUMMARY_PROMPT_VERSION = "v1"

# iOS 卡片流字段长度上限（中文字符数）
CARD_SUMMARY_MAX_CHARS = 120
# iOS 详情页推荐区间（中文字符数）
DETAIL_SUMMARY_MIN_CHARS = 150
DETAIL_SUMMARY_MAX_CHARS = 600


def content_fingerprint(members: list[RawArticle], main: RawArticle) -> str:
    """事件内容指纹：成员集合 + 主文章标题变化即触发重算。"""
    ids = sorted(m.id for m in members if m.id is not None)
    raw = json.dumps({"ids": ids, "title": main.title or ""}, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _to_card_summary(detail_text: str) -> str:
    """从 detail_summary 截出 ≤120 字的卡片摘要。

    简单实现：取前 N 字，遇到第一个句末标点（。！？.!?）截断；
    若没找到则硬截断到 N 字。
    """
    if not detail_text:
        return ""
    text = detail_text.strip().replace("\n", " ")
    if len(text) <= CARD_SUMMARY_MAX_CHARS:
        return text
    cutoff = text[:CARD_SUMMARY_MAX_CHARS]
    for punct in ("。", "！", "？", ".", "!", "?"):
        idx = cutoff.rfind(punct)
        if idx >= CARD_SUMMARY_MAX_CHARS // 2:
            return cutoff[: idx + 1]
    # 兜底：保留 N-1 字 + 省略号，确保总长 ≤ N
    return text[: CARD_SUMMARY_MAX_CHARS - 1].rstrip() + "…"


def _pick_main(members: list[RawArticle]) -> RawArticle:
    return max(members, key=lambda m: LEVEL_WEIGHT.get(m.source.level if m.source else SourceLevel.B, 0.3))


def _summary_extractive(main: RawArticle) -> dict[str, Any]:
    """抽取式兜底：detail = 取 main 文章前 3 句拼接；card = 用 _to_card_summary 截断。"""
    sentences = extractive_summary(main.content or main.title, max_sentences=3)
    detail_text = " ".join(s.strip() for s in sentences if s).strip()
    if not detail_text:
        detail_text = (main.title or "").strip()
    card_text = _to_card_summary(detail_text)
    return {
        "title": main.title,
        "card_summary": card_text,
        "detail_summary": detail_text,
        "why_matters": "（抽取式兜底未生成解读；接入 LLM 后输出「为何重要」）",
        "method": "extractive",
        "llm_meta": None,
    }


def _summary_llm(members: list[RawArticle], main: RawArticle) -> dict[str, Any]:
    """调用兼容 OpenAI 的 LLM，一次性产出 iOS 卡片摘要 + 详情摘要。

    失败抛异常由调用方兜底。
    """
    sources_text = "\n".join(
        f"[{i + 1}]（{(m.source.level.value if m.source else 'B')}）"
        f"{(m.source.name if m.source else 'unknown')}：{m.title}。{m.content}"
        for i, m in enumerate(members)
    )
    prompt = (
        "你是「热读」的资深财经科技编辑。\n"
        "目标读者：互联网从业者、产品经理、投资人、创业者；他们使用 iPhone 早晚通勤碎片阅读。\n"
        "请基于【仅以下多源原文】生成结构化摘要。\n"
        "严格规则：\n"
        "  1. 只用原文出现的事实与数字，禁止编造；缺失就留空。\n"
        "  2. 数字必须与原文一致；引用必须来自原文。\n"
        "  3. 事实与解读分区：detail_summary 仅描述事实与上下文，why_matters 是面向目标读者的解读，"
        "但不得引入新事实。\n"
        "  4. 标题可设问以增强吸引力但不得夸大。\n"
        "  5. 文风：专业但口语化，避免空话/套话；适合手机一屏速读。\n"
        "字段长度规范（按中文字符数）：\n"
        "  - card_summary：≤120 字，一句到两句，必须能独立读懂；用于 iOS 卡片首屏。\n"
        "  - detail_summary：300–500 字，3–5 句，覆盖关键事实/数字/主体方/时间地点；用于 iOS 详情页。\n"
        "  - why_matters：50–120 字，面向目标读者解释「为何这件事值得关注」。\n"
        "输出**严格 JSON**（不要 Markdown，不要解释，不要多余字段）：\n"
        "{\"title\":\"\",\"card_summary\":\"\",\"detail_summary\":\"\",\"why_matters\":\"\"}\n\n"
        f"【多源原文】\n{sources_text}"
    )
    client = get_llm_client()
    resp = client.chat_json(prompt, temperature=0.3)
    parsed = json.loads(resp.content)
    card_raw = (parsed.get("card_summary") or "").strip()
    detail_raw = (parsed.get("detail_summary") or "").strip()

    # 长度护栏：card 超长强制截断
    card_safe = card_raw if len(card_raw) <= CARD_SUMMARY_MAX_CHARS else _to_card_summary(card_raw)
    # detail 极少数模型会超长；硬上限 600 字截断（不破坏可读性）
    detail_safe = detail_raw[:DETAIL_SUMMARY_MAX_CHARS]

    return {
        "title": parsed.get("title") or main.title,
        "card_summary": card_safe,
        "detail_summary": detail_safe,
        "why_matters": (parsed.get("why_matters") or "").strip(),
        "method": "llm",
        "llm_meta": {
            "model": resp.model,
            "usage": resp.usage,
            "temperature": 0.3,
            "prompt_version": SUMMARY_PROMPT_VERSION,
        },
    }


def _build_sources(members: list[RawArticle]) -> list[dict]:
    return [
        {
            "name": m.source.name if m.source else "unknown",
            "level": m.source.level.value if m.source else "B",
            "url": m.url,
        }
        for m in members
    ]


def _needs_disclaimer(event: Event) -> bool:
    return event.module.value in {"finance", "macro"}


def _latest_content(ev: Event) -> EventContent | None:
    return max(ev.contents, key=lambda c: c.version) if ev.contents else None


def run() -> dict:
    stats = {"summarized": 0, "llm": 0, "extractive": 0, "skipped": 0}
    with get_session() as s:
        events = (
            s.execute(
                select(Event).where(Event.status == EventStatus.clustered)
            )
            .scalars()
            .all()
        )
        for ev in events:
            members = [link.article for link in ev.article_links]
            if not members:
                continue
            main = _pick_main(members)

            # 阶段 3.2 同事件缓存：内容指纹未变则跳过重新生成（不烧 token）
            fingerprint = content_fingerprint(members, main)
            prev = _latest_content(ev)
            if prev is not None and (prev.llm_meta or {}).get("fingerprint") == fingerprint:
                ev.status = EventStatus.summarized
                stats["skipped"] += 1
                continue

            if settings.llm.enabled:
                try:
                    summary = _summary_llm(members, main)
                    stats["llm"] += 1
                except Exception as e:
                    print(f"  [summarize] LLM 失败回退抽取式：{type(e).__name__}: {e}")
                    summary = _summary_extractive(main)
                    stats["extractive"] += 1
            else:
                summary = _summary_extractive(main)
                stats["extractive"] += 1

            # 阶段 3.2 留痕：指纹 + prompt 版本写进 llm_meta（抽取式 path 也记，usage 留空）
            llm_meta = dict(summary["llm_meta"] or {})
            llm_meta["fingerprint"] = fingerprint
            llm_meta.setdefault("prompt_version", SUMMARY_PROMPT_VERSION)

            # event_contents.summary 列保留（兼容旧消费方）：
            # - LLM 路径：[card, detail] 两段，便于人工质检直接看长短两版；
            # - 抽取式路径：拆 detail 为 3 句句子列表（与旧版结构一致）。
            if summary["method"] == "llm":
                summary_list = [summary["card_summary"], summary["detail_summary"]]
            else:
                summary_list = extractive_summary(
                    main.content or main.title, max_sentences=3
                ) or [summary["detail_summary"]]

            # 版本号递增：保留历史 EventContent 便于回放
            next_version = (prev.version + 1) if prev else 1
            content = EventContent(
                event_id=ev.id,
                version=next_version,
                title=(summary["title"] or "")[:256],
                summary=summary_list,
                why_matters=summary["why_matters"]
                + (("\n" + _DISCLAIMER) if _needs_disclaimer(ev) else ""),
                facts=[],
                sources=_build_sources(members),
                method=summary["method"],
                llm_meta=llm_meta,
            )
            s.add(content)

            # iOS 直读字段：直接来自 LLM/抽取式产出，不再走 Python 拼接 + 截断
            ev.card_summary = summary["card_summary"] or None
            ev.detail_summary = summary["detail_summary"] or None

            ev.status = EventStatus.summarized
            stats["summarized"] += 1
    print(
        f"  [summarize] 共 {stats['summarized']} 个事件  llm={stats['llm']}  "
        f"extractive={stats['extractive']}  skipped={stats['skipped']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 5/6] summarize 摘要")
    print("=" * 60)
    print(run())
