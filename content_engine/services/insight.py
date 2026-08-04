"""热点透视（Event Insight）：单事件按需 LLM 深度分析的核心逻辑。

职责边界：
- 本模块只做「素材组装 / prompt 构造 / 响应解析 / 相关事件召回 / 状态幂等」，
  全部是易单测的纯函数或窄接口函数；
- HTTP 端点见 ``api/routers/insight.py``；Celery 编排见 ``tasks/insight_tasks.py``。

并发约定（acks_late 重投安全）：
- 任务入口 ``begin_generation`` 条件更新 ``pending/generating → generating``，
  rowcount=0 即直接返回（防 worker 重投重复烧 token；条件必须含 generating，
  否则重投任务会在行已 generating 时误退出、行永久卡死）；
- 完成 ``complete_generation`` 条件更新 ``WHERE status='generating'``，
  两个并发写者后写覆盖先写，同素材结果等价，无害；
- 生成失败由 ``fail_generation`` 落 failed，任务层不上抛（避免 celery 重试风暴）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    PUBLIC_EVENT_STATUSES,
    Event,
    EventAnalysis,
    EventContent,
    SourceLevel,
)
from ..services.llm_client import LLMResponse

# ---------------------------------------------------------------------------
# 调参常量（仿 summarize 模块级常量先例；需要运维调参时再提升到 settings）
# ---------------------------------------------------------------------------

INSIGHT_PROMPT_VERSION = "v1"
INSIGHT_DISCLAIMER = "趋势推演仅基于公开信息的概率性推演，不构成任何投资建议或决策依据。"

SOURCE_TOP_N = 6            # 多源原文取前 N 篇
SOURCE_EXCERPT_CHARS = 400  # 每篇正文截断
RELATED_TOP_K = 5           # 相关历史事件取前 K 个
RELATED_MIN_COS = 0.5       # 相关事件余弦阈值
RELATED_WINDOW_DAYS = 90    # 相关事件时间窗
RELATED_EXCERPT_CHARS = 150  # 相关事件摘要截断
STALE_SECONDS = 600         # pending/generating 超过该秒数视为卡死，允许重投

# 长度护栏（中文字符数，硬截断）
HISTORY_MAX_CHARS = 500
CURRENT_MAX_CHARS = 600
FORECAST_MAX_CHARS = 400
MIN_SECTION_CHARS = 30

# 信源级别权重（数据抄自 stages/seed_data.LEVEL_WEIGHT，避免 services→stages 反向依赖）
_LEVEL_WEIGHT: dict[SourceLevel, float] = {
    SourceLevel.S: 1.0,
    SourceLevel.A: 0.7,
    SourceLevel.B: 0.3,
}


class InsightParseError(Exception):
    """LLM 响应 JSON 结构/字段不达标（缺 key、非 str、过短）。"""


# ---------------------------------------------------------------------------
# 相关历史事件召回
# ---------------------------------------------------------------------------

def _cos(a: list[float], b: list[float]) -> float:
    """余弦相似度（复制自 stages/cluster.py，避免 services→stages 反向依赖）。"""
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na**0.5 * nb**0.5)


def _as_utc(dt: datetime) -> datetime:
    """SQLite 读回的时间戳丢 tz；naive 一律按 UTC 处理（deps.is_member 同款约定）。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def rank_related(event: Event, candidates: list[Event]) -> list[Event]:
    """纯函数：对候选事件按与本事件 centroid 余弦排序取 top-K（排除自身、阈值过滤）。"""
    if event.centroid is None:
        return []
    scored = [
        (c, _cos(list(event.centroid), list(c.centroid)))
        for c in candidates
        if c.id != event.id and c.centroid is not None
    ]
    scored = [(c, s) for c, s in scored if s >= RELATED_MIN_COS]
    scored.sort(key=lambda t: t[1], reverse=True)
    return [c for c, _ in scored[:RELATED_TOP_K]]


def find_related_events(
    session: Session, event: Event, *, now: datetime
) -> list[Event]:
    """召回相关历史事件：同 module + published + 近 N 天 + centroid 余弦 top-K。

    centroid 缺失（如 embedding 未启用）时直接返回空，prompt 层降级为
    「仅基于本事件事实梳理脉络」。
    """
    if event.centroid is None:
        return []
    cutoff = now - timedelta(days=RELATED_WINDOW_DAYS)
    candidates = (
        session.execute(
            select(Event)
            .where(Event.module == event.module)
            .where(Event.status.in_(PUBLIC_EVENT_STATUSES))
            .where(Event.id != event.id)
            .where(Event.last_update >= cutoff)
            .where(Event.centroid.isnot(None))
        )
        .scalars()
        .all()
    )
    return rank_related(event, list(candidates))


# ---------------------------------------------------------------------------
# 素材组装 + prompt 构造
# ---------------------------------------------------------------------------

def _latest_content(event: Event) -> EventContent | None:
    return max(event.contents, key=lambda c: c.version) if event.contents else None


def load_materials(session: Session, event: Event, *, now: datetime) -> dict[str, Any]:
    """组装生成所需的全部素材（须在持有会话时调用，读取 ORM 关系）。"""
    content = _latest_content(event)

    # 多源原文：信源级别权重降序 + 进簇相似度降序，取 top N
    links = sorted(
        event.article_links,
        key=lambda l: (
            -_LEVEL_WEIGHT.get(l.article.source.level if l.article.source else SourceLevel.B, 0.3),
            -(l.similarity or 0.0),
        ),
    )[:SOURCE_TOP_N]
    sources = [
        {
            "name": l.article.source.name if l.article.source else "unknown",
            "level": l.article.source.level.value if l.article.source else "B",
            "title": l.article.title,
            "excerpt": (l.article.content or "")[:SOURCE_EXCERPT_CHARS],
        }
        for l in links
        if l.article is not None
    ]

    related = find_related_events(session, event, now=now)
    related_out = []
    for r in related:
        rc = _latest_content(r)
        related_out.append(
            {
                "id": r.id,
                "date": _as_utc(r.last_update).date().isoformat(),
                "title": rc.title if rc else f"事件#{r.id}",
                "excerpt": (r.detail_summary or "")[:RELATED_EXCERPT_CHARS],
            }
        )

    member_ids = sorted(l.article_id for l in event.article_links)
    fingerprint = hashlib.sha1(
        f"{_as_utc(event.last_update).isoformat()}|{member_ids}|{sorted(r.id for r in related)}".encode()
    ).hexdigest()

    return {
        "title": content.title if content else f"事件#{event.id}",
        "detail_summary": event.detail_summary or "",
        "why_matters": content.why_matters if content else "",
        "facts": content.facts if content else [],
        "sources": sources,
        "related": related_out,
        "related_ids": [r.id for r in related],
        "fingerprint": fingerprint,
    }


def build_prompt(materials: dict[str, Any]) -> str:
    """构造透视 prompt（纯函数）。

    注意：prompt 必须含「JSON」字样（DeepSeek JSON mode 硬性要求）。
    """
    facts_text = "\n".join(
        f"- {f.get('text', str(f)) if isinstance(f, dict) else f}" for f in materials["facts"]
    ) or "（无）"
    sources_text = "\n".join(
        f"[{i + 1}]（{s['level']}）{s['name']}：{s['title']}。{s['excerpt']}"
        for i, s in enumerate(materials["sources"])
    ) or "（无多源原文）"
    if materials["related"]:
        related_text = "\n".join(
            f"[{i + 1}] {r['date']}《{r['title']}》：{r['excerpt']}"
            for i, r in enumerate(materials["related"])
        )
        related_rule = "可引用【相关历史事件】作脉络"
    else:
        related_text = "（库内无相关历史事件）"
        related_rule = "仅基于本事件事实梳理，禁止虚构历史节点"
    return (
        "你是「热读」的首席分析师，为付费会员撰写单事件深度透视。\n"
        "目标读者：互联网从业者、产品经理、投资人、创业者；iPhone 通勤碎片阅读。\n"
        "严格规则：\n"
        "  1. 事实只能来自【本事件资料】与【相关历史事件】，禁止编造；数字必须与原文一致。\n"
        "  2. 三段分工（按中文字符数）：\n"
        f"     - history（来龙去脉）：按时间线梳理起因与关键节点，{related_rule}；200–350 字。\n"
        "     - current（现状剖析）：拆解当前格局、各方立场与核心原因；250–400 字。\n"
        "     - forecast（趋势推演）：基于现有事实审慎给出 1–2 种可能走向与后续观察信号；"
        "150–250 字；必须用「可能/或将/大概率」等措辞，禁止断言，禁止投资建议，"
        "不要自己写免责声明（系统会统一追加）。\n"
        "  3. 文风：专业、口语化、信息密度高，无空话套话。\n"
        "  4. 某段素材不足时，只写已有事实可确认的内容，禁止编造补齐。\n"
        "输出**严格 JSON**（不要 Markdown，不要解释，不要多余字段）：\n"
        "{\"history\":\"\",\"current\":\"\",\"forecast\":\"\"}\n\n"
        f"【本事件资料】\n标题：{materials['title']}\n详情摘要：{materials['detail_summary']}\n"
        f"为何重要：{materials['why_matters']}\n关键事实：\n{facts_text}\n\n"
        f"多源原文（按信源级别排序）：\n{sources_text}\n\n"
        f"【相关历史事件】（按相关度排序）\n{related_text}"
    )


def parse_insight_response(resp: LLMResponse) -> dict[str, str]:
    """解析 LLM 返回的三段 JSON（纯函数，含长度护栏）。

    结构不达标抛 :class:`InsightParseError`；JSON 非法抛 ``json.JSONDecodeError``。
    """
    parsed = json.loads(resp.content)
    out: dict[str, str] = {}
    caps = {
        "history": HISTORY_MAX_CHARS,
        "current": CURRENT_MAX_CHARS,
        "forecast": FORECAST_MAX_CHARS,
    }
    for key, cap in caps.items():
        value = parsed.get(key)
        if not isinstance(value, str):
            raise InsightParseError(f"字段 {key} 缺失或非字符串")
        value = value.strip()
        if len(value) < MIN_SECTION_CHARS:
            raise InsightParseError(f"字段 {key} 过短（{len(value)} 字）")
        out[key] = value[:cap]
    return out


# ---------------------------------------------------------------------------
# 状态幂等（POST 触发 / 任务入口 / 任务完成）
# ---------------------------------------------------------------------------

def claim_for_generation(
    session: Session, event_id: int, now: datetime
) -> tuple[EventAnalysis, bool]:
    """POST 触发的占位逻辑：返回 (行, 是否需要入队)。

    - 无行 → 建 pending 行并入队（唯一约束 + savepoint 兜底并发双击）；
    - failed / stale（pending·generating 超 TTL）→ 重置 pending 重新入队；
    - 其余（pending/generating 进行中、ready）→ 不入队。
    """
    row = session.execute(
        select(EventAnalysis).where(EventAnalysis.event_id == event_id)
    ).scalar_one_or_none()
    if row is None:
        row = EventAnalysis(event_id=event_id, status="pending")
        session.add(row)
        try:
            with session.begin_nested():
                session.flush()
        except IntegrityError:
            # 并发 POST 的另一请求已建行 → 读回它的行，不重复入队
            row = session.execute(
                select(EventAnalysis).where(EventAnalysis.event_id == event_id)
            ).scalar_one()
            return row, False
        return row, True
    if row.status == "failed":
        row.status = "pending"
        row.error = None
        return row, True
    if row.status in ("pending", "generating"):
        age = (now - _as_utc(row.updated_at)).total_seconds()
        if age >= STALE_SECONDS:
            # worker 崩溃 / broker 丢消息的逃生门
            row.status = "pending"
            row.error = None
            return row, True
    return row, False


def begin_generation(session: Session, event_id: int, now: datetime) -> bool:
    """任务入口条件更新：pending/generating → generating。

    rowcount=0（行已 ready/failed/被删）→ False，任务直接返回。
    """
    result = session.execute(
        update(EventAnalysis)
        .where(EventAnalysis.event_id == event_id)
        .where(EventAnalysis.status.in_(("pending", "generating")))
        .values(status="generating", updated_at=now)
    )
    return result.rowcount > 0


def complete_generation(
    session: Session,
    event_id: int,
    *,
    sections: dict[str, str],
    related_ids: list[int],
    llm_meta: dict[str, Any],
    now: datetime,
) -> bool:
    """任务完成条件更新：仅 generating 行可被翻 ready（并发写后写覆盖先写，无害）。"""
    result = session.execute(
        update(EventAnalysis)
        .where(EventAnalysis.event_id == event_id)
        .where(EventAnalysis.status == "generating")
        .values(
            status="ready",
            sections=sections,
            related_event_ids=related_ids,
            llm_meta=llm_meta,
            error=None,
            generated_at=now,
            updated_at=now,
        )
    )
    return result.rowcount > 0


def fail_generation(session: Session, event_id: int, error: str, now: datetime) -> None:
    """生成失败落库（行已被删时静默 no-op）。"""
    session.execute(
        update(EventAnalysis)
        .where(EventAnalysis.event_id == event_id)
        .values(status="failed", error=error[:512], updated_at=now)
    )
