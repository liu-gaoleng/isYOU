"""阶段 4.1：发布前防幻觉护栏（机器卡点）。

发布（publish）前对已评分事件做四类自动校验，任一不通过则**不直发**，
打回 ``EventStatus.reviewing`` 进人工质检（铁律：脏内容零直发、可降级、可回溯）：

1. **结构完整**：card_summary / detail_summary 不能为空或过短；
2. **数字一致性**：detail_summary 中出现的数字（百分比/金额/年份等），
   必须能在任一信源原文中找到；查无的数字占比 > 阈值 → 拦截；
3. **合规敏感词**：命中敏感词表即拦截；
4. **金融免责**：finance / macro 模块的内容必须带免责声明，缺失则自动补全
   （此项为「自动修正」而非拦截，返回 patched_why_matters）。

设计为**纯函数**：输入摘要文本 + 信源原文，输出 ``GuardResult``，
不依赖 DB / 网络，便于单测与在任意阶段复用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from content_engine.config import settings

# 金融免责声明（与 summarize._DISCLAIMER 保持一致）
DISCLAIMER = "本内容仅作信息聚合，不构成任何投资建议。"

# 需要强制带免责声明的模块
_DISCLAIMER_MODULES = {"finance", "macro"}

# 内置基础敏感词表（合规底线，可被 settings.guard.sensitive_words 追加/覆盖）
_BUILTIN_SENSITIVE_WORDS = (
    "暴力恐怖",
    "颠覆政权",
    "邪教",
    "赌博网站",
    "毒品交易",
)

# 抽取「数字 token」：整数 / 小数 / 百分比 / 带千分位，允许前后附 %、亿、万、元 等单位由文本判断
_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass
class GuardResult:
    """护栏检查结果。

    passed=False 时 violations 列出拦截原因；patched_why_matters 非空表示需自动补免责。
    """

    passed: bool
    violations: list[str] = field(default_factory=list)
    patched_why_matters: str | None = None


def _sensitive_words() -> list[str]:
    extra = [w.strip() for w in (settings.guard.sensitive_words or "").split(",") if w.strip()]
    return list(_BUILTIN_SENSITIVE_WORDS) + extra


def _extract_numbers(text: str) -> list[str]:
    """抽取文本中的数字 token（去掉千分位逗号后比较）。"""
    return [m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text or "")]


def check_structure(card_summary: str | None, detail_summary: str | None) -> list[str]:
    """结构完整性：card / detail 不能空或过短。"""
    violations: list[str] = []
    min_chars = settings.guard.min_summary_chars
    if not card_summary or len(card_summary.strip()) < min_chars:
        violations.append("card_summary 缺失或过短")
    if not detail_summary or len(detail_summary.strip()) < min_chars:
        violations.append("detail_summary 缺失或过短")
    return violations


def check_numbers(detail_summary: str | None, source_texts: list[str]) -> list[str]:
    """数字一致性：detail 中的数字必须在信源原文里出现。

    查无的数字占比超过 ``max_unverified_number_ratio`` 则视为拦截项。
    """
    nums = _extract_numbers(detail_summary or "")
    if not nums:
        return []
    haystack = " ".join(_extract_numbers(" ".join(source_texts)))
    haystack_set = set(haystack.split())
    unverified = [n for n in nums if n not in haystack_set]
    ratio = len(unverified) / len(nums)
    if ratio > settings.guard.max_unverified_number_ratio:
        return [f"数字一致性存疑：{len(unverified)}/{len(nums)} 个数字未在信源中找到 {unverified[:5]}"]
    return []


def check_sensitive(card_summary: str | None, detail_summary: str | None) -> list[str]:
    """敏感词：命中任一即拦截。"""
    text = f"{card_summary or ''} {detail_summary or ''}"
    hit = [w for w in _sensitive_words() if w and w in text]
    return [f"命中敏感词：{hit}"] if hit else []


def check_disclaimer(module: str, why_matters: str | None) -> str | None:
    """金融免责：finance/macro 缺免责声明则返回补全后的文本（自动修正，不拦截）。"""
    if module not in _DISCLAIMER_MODULES:
        return None
    text = why_matters or ""
    if DISCLAIMER in text:
        return None
    return (text + ("\n" if text else "") + DISCLAIMER).strip()


def check_event(
    *,
    module: str,
    card_summary: str | None,
    detail_summary: str | None,
    why_matters: str | None,
    source_texts: list[str],
) -> GuardResult:
    """对单个事件跑全部护栏，汇总结果。

    Args:
        module: 事件模块 value（tech/finance/ai/macro）
        card_summary / detail_summary / why_matters: 待发布摘要文本
        source_texts: 该事件全部信源原文（标题+正文拼接）
    """
    if not settings.guard.enabled:
        return GuardResult(passed=True)

    violations: list[str] = []
    violations += check_structure(card_summary, detail_summary)
    violations += check_numbers(detail_summary, source_texts)
    violations += check_sensitive(card_summary, detail_summary)

    patched = check_disclaimer(module, why_matters)

    return GuardResult(
        passed=len(violations) == 0,
        violations=violations,
        patched_why_matters=patched,
    )


__all__ = [
    "GuardResult",
    "DISCLAIMER",
    "check_structure",
    "check_numbers",
    "check_sensitive",
    "check_disclaimer",
    "check_event",
]
