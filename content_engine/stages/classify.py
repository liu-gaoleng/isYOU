"""阶段 3：分类（classify）。

阶段 0：纯关键词规则（命中分数最高的模块即为分类结果，全 0 时回退到信源默认 module）。
阶段 2.1：在规则层之上叠加 LLM 兜底——当规则置信度 <
``settings.threshold.cls_llm_threshold``（默认 0.6）时调 LLM 重判。
阶段 3.1（本次）：LLM 调用改走统一的 ``services.llm_client``（限流 + 重试 + 退避）。

LLM 协议（OpenAI 兼容 ``POST /v1/chat/completions``）：
- 输入：title + content（最多 800 字）+ 当前规则候选 module（仅作 hint）；
- 输出严格 JSON：``{"module":"tech|finance|ai|macro","tags":[...],"confidence":0.0-1.0}``；
- 失败兜底：网络/解析异常 → 沿用规则结果（不改 confidence），并记 ``last_error``。

为什么 confidence 阈值放在 ThresholdSettings 而不是 LLMSettings：
分类逻辑里 LLM 只是"低置信兜底"，与 LLM 自身配置（key/base_url/model）不同维度。

状态推进 cleaned → classified（命中 LLM 也维持同一状态，置信度刷新）。
"""

from __future__ import annotations

import json

from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import ArticleStatus, Module, RawArticle, get_session
from content_engine.services.llm_client import LLMError, get_llm_client

from .seed_data import CLASSIFY_RULES

# LLM Prompt 中传给模型的正文最大字符数（避免长文导致 token 暴涨）
_LLM_INPUT_MAX_CHARS = 800
_VALID_MODULES = {m.value for m in Module}


def classify_one(title: str, content: str, fallback: Module) -> tuple[Module, float]:
    """关键词打分，返回 (模块, 置信度 0-1)。"""
    text = f"{title} {content}".lower()
    scores: dict[Module, int] = {}
    for module, kws in CLASSIFY_RULES.items():
        scores[module] = sum(1 for kw in kws if kw.lower() in text)
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return fallback, 0.5
    total = sum(scores.values())
    return best, round(scores[best] / total, 2) if total else 0.5


def _classify_llm(title: str, content: str, hint: Module) -> tuple[Module, float, list[str]] | None:
    """调用 LLM 给低置信文章重判。

    返回 (module, confidence, tags) 或 None（任何异常都视作失败、由调用方回退规则）。
    """
    client = get_llm_client()
    if not client.enabled:
        return None

    excerpt = (content or "")[:_LLM_INPUT_MAX_CHARS]
    prompt = (
        "你是「热读」的资深编辑，需要把一篇文章归到四个模块之一：\n"
        "  - tech    : 互联网产品 / 大厂战略 / 数码硬件\n"
        "  - finance : 资本市场 / 一级市场融资 / 公司财报\n"
        "  - ai      : 大模型 / 算力 / AI 应用层 / Agent\n"
        "  - macro   : 宏观经济 / 政策 / 货币 / 出海贸易\n"
        f"规则层初判候选: {hint.value}（仅作参考，不一定正确）。\n"
        "严格规则：\n"
        "  1. 仅基于以下原文判断，不引入外部知识。\n"
        "  2. 输出严格 JSON，不要 Markdown / 解释 / 多余字段。\n"
        '  3. confidence ∈ [0, 1]，对自己判断的把握度。\n'
        '  4. tags 给 1-3 个中文短词（如「大模型」「财报」「降息」）。\n'
        "输出格式：\n"
        '{"module":"tech|finance|ai|macro","tags":["..."],"confidence":0.0}\n\n'
        f"【标题】{title}\n【正文】{excerpt}"
    )
    try:
        resp = client.chat_json(prompt, temperature=0.0)
        parsed = json.loads(resp.content)
    except (LLMError, json.JSONDecodeError, ValueError):
        return None

    module_val = (parsed.get("module") or "").strip().lower()
    if module_val not in _VALID_MODULES:
        return None
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    conf = max(0.0, min(1.0, conf))
    tags = parsed.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    tags = [str(t).strip() for t in tags if str(t).strip()][:3]
    return Module(module_val), round(conf, 2), tags


def run() -> dict:
    threshold = settings.threshold.cls_llm_threshold
    stats = {
        "classified": 0,
        "rule_only": 0,
        "llm_called": 0,
        "llm_overrode": 0,
        "llm_failed": 0,
        "by_module": {},
    }
    with get_session() as s:
        rows = (
            s.execute(
                select(RawArticle).where(RawArticle.status == ArticleStatus.cleaned)
            )
            .scalars()
            .all()
        )
        for art in rows:
            fallback = art.source.module if art.source else Module.tech
            module, conf = classify_one(art.title, art.content, fallback)
            tags: list[str] = []

            # 规则置信度低 → LLM 兜底
            if conf < threshold and settings.llm.enabled:
                stats["llm_called"] += 1
                llm_out = _classify_llm(art.title, art.content, module)
                if llm_out is None:
                    stats["llm_failed"] += 1
                    art.last_error = (art.last_error or "") + " | classify_llm_failed"
                else:
                    new_module, new_conf, new_tags = llm_out
                    if new_module != module or new_conf > conf:
                        stats["llm_overrode"] += 1
                    module, conf = new_module, new_conf
                    tags = new_tags
            else:
                stats["rule_only"] += 1

            art.module = module
            art.cls_confidence = conf
            if tags:
                art.tags = tags
            art.status = ArticleStatus.classified
            stats["by_module"][module.value] = stats["by_module"].get(module.value, 0) + 1
            stats["classified"] += 1
    print(
        f"  [classify] {stats['classified']} 条  分布={stats['by_module']}  "
        f"rule_only={stats['rule_only']}  llm_called={stats['llm_called']}  "
        f"llm_overrode={stats['llm_overrode']}  llm_failed={stats['llm_failed']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 3/6] classify 分类")
    print("=" * 60)
    print(run())
