"""阶段 4.1 单测：发布前防幻觉护栏（纯函数，不连 DB/网络）。

覆盖：
- check_structure：空/过短 → 拦截；正常 → 放行；
- check_numbers：数字全在信源 → 放行；超阈值 → 拦截；无数字 → 放行；
- check_sensitive：命中（内置 + 自定义）→ 拦截；未命中 → 放行；
- check_disclaimer：finance/macro 缺失 → 补全；tech → 不补；已有 → 不重复；
- check_event：enabled=False 直接放行；综合通过/拦截。
"""

from __future__ import annotations

from content_engine.config import settings
from content_engine.services import guard


def test_structure_empty_blocked():
    v = guard.check_structure("", "")
    assert any("card_summary" in x for x in v)
    assert any("detail_summary" in x for x in v)


def test_structure_too_short_blocked():
    v = guard.check_structure("短", "短")
    assert len(v) == 2


def test_structure_ok_passes():
    long_text = "这是一段足够长的合规摘要文本内容用于通过结构校验"
    assert guard.check_structure(long_text, long_text) == []


def test_numbers_all_in_source_passes():
    detail = "营收增长 25% 达到 100 亿元，同比 2025 年提升"
    sources = ["公司财报显示营收 100 亿元，增长 25%，对比 2025 年数据"]
    assert guard.check_numbers(detail, sources) == []


def test_numbers_no_numbers_passes():
    assert guard.check_numbers("纯文本没有任何数字内容", ["信源原文"]) == []


def test_numbers_unverified_over_ratio_blocked():
    # detail 三个数字全部查无 → ratio=1.0 > 0.5 → 拦截
    detail = "暴涨 999% 至 888 亿元，预计 2099 年"
    sources = ["信源里没有这些数字"]
    v = guard.check_numbers(detail, sources)
    assert len(v) == 1
    assert "数字一致性存疑" in v[0]


def test_sensitive_builtin_hit_blocked():
    v = guard.check_sensitive("正常标题", "涉及赌博网站的内容")
    assert v and "命中敏感词" in v[0]


def test_sensitive_none_passes():
    assert guard.check_sensitive("正常科技新闻", "公司发布新产品") == []


def test_sensitive_custom_word(monkeypatch):
    monkeypatch.setattr(settings.guard, "sensitive_words", "内幕交易,违规词")
    v = guard.check_sensitive("标题", "涉嫌内幕交易")
    assert v and "命中敏感词" in v[0]


def test_disclaimer_finance_patched():
    patched = guard.check_disclaimer("finance", "这是重要性说明")
    assert patched is not None
    assert guard.DISCLAIMER in patched


def test_disclaimer_macro_patched_from_empty():
    patched = guard.check_disclaimer("macro", None)
    assert patched == guard.DISCLAIMER


def test_disclaimer_tech_not_patched():
    assert guard.check_disclaimer("tech", "科技说明") is None


def test_disclaimer_already_present_not_duplicated():
    text = f"重要性说明\n{guard.DISCLAIMER}"
    assert guard.check_disclaimer("finance", text) is None


def test_check_event_disabled_passes(monkeypatch):
    monkeypatch.setattr(settings.guard, "enabled", False)
    result = guard.check_event(
        module="finance",
        card_summary="",
        detail_summary="",
        why_matters=None,
        source_texts=[],
    )
    assert result.passed is True
    assert result.violations == []


def test_check_event_pass_with_disclaimer_patch():
    long_text = "这是一段足够长的合规卡片摘要文本内容用于通过结构校验"
    detail = "营收 100 亿元增长 25%"
    result = guard.check_event(
        module="finance",
        card_summary=long_text,
        detail_summary=detail,
        why_matters="重要性说明",
        source_texts=["财报营收 100 亿元增长 25%"],
    )
    assert result.passed is True
    assert result.violations == []
    assert result.patched_why_matters is not None
    assert guard.DISCLAIMER in result.patched_why_matters


def test_check_event_blocked_collects_violations():
    result = guard.check_event(
        module="tech",
        card_summary="",
        detail_summary="涨幅 999% 涉及毒品交易",
        why_matters=None,
        source_texts=["无关信源"],
    )
    assert result.passed is False
    assert len(result.violations) >= 2
