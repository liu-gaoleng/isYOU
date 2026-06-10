"""纯函数单测：clean_text / tokenize / jaccard / content_hash / extractive_summary。

阶段 0 出口标准：拆出来的纯函数都有最小覆盖，后续算法升级（embedding / SimHash）
不退化既有行为。
"""

from __future__ import annotations

from content_engine.stages.utils import (
    clean_text,
    content_hash,
    extractive_summary,
    hamming_distance,
    jaccard,
    simhash64,
    tokenize,
)


# ---------- clean_text ----------
def test_clean_text_strips_html_and_entities() -> None:
    raw = "<p>OpenAI &amp; Microsoft  发布<br/>新模型</p>"
    assert clean_text(raw) == "OpenAI & Microsoft 发布 新模型"


def test_clean_text_handles_none_and_empty() -> None:
    assert clean_text(None) == ""
    assert clean_text("") == ""
    assert clean_text("   \t\n  ") == ""


def test_clean_text_collapses_whitespace() -> None:
    assert clean_text("a   b\n\nc") == "a b c"


# ---------- tokenize ----------
def test_tokenize_mixes_en_and_cn_bigrams() -> None:
    tokens = tokenize("OpenAI 发布 GPT-5")
    assert "openai" in tokens  # 小写化
    assert "gpt" in tokens
    assert "5" in tokens
    # 中文 2-gram
    assert "发布" in tokens


def test_tokenize_empty() -> None:
    assert tokenize("") == set()
    assert tokenize(None) == set()  # type: ignore[arg-type]


# ---------- jaccard ----------
def test_jaccard_identical_is_one() -> None:
    s = "OpenAI 发布 GPT-5 模型"
    assert jaccard(s, s) == 1.0


def test_jaccard_disjoint_is_zero() -> None:
    assert jaccard("apple banana", "苹果 香蕉") == 0.0


def test_jaccard_partial_overlap_in_range() -> None:
    sim = jaccard("OpenAI 发布 GPT-5", "OpenAI 发布 Sora 视频模型")
    assert 0.0 < sim < 1.0


# ---------- content_hash ----------
def test_content_hash_stable_and_short() -> None:
    h1 = content_hash("标题 A", "正文 A")
    h2 = content_hash("标题 A", "正文 A")
    assert h1 == h2
    assert len(h1) == 16


def test_content_hash_differs_for_different_input() -> None:
    assert content_hash("a", "b") != content_hash("a", "c")
    assert content_hash("a", "b") != content_hash("b", "a")


# ---------- extractive_summary ----------
def test_extractive_summary_protects_decimals() -> None:
    text = "CPI 同比 2.21%。环比下降 0.3 个百分点。市场关注。结尾。"
    out = extractive_summary(text, max_sentences=3)
    # 第一句必须保留 2.21% 不被错误断成 "2."
    assert any("2.21%" in s for s in out)
    assert len(out) <= 3


def test_extractive_summary_handles_empty() -> None:
    assert extractive_summary("") == []


# ---------- classify 规则 ----------
def test_classify_picks_highest_scoring_module() -> None:
    from content_engine.models import Module
    from content_engine.stages.classify import classify_one

    module, conf = classify_one("OpenAI 发布 GPT-5 大模型", "推理与训练效率提升", Module.tech)
    assert module == Module.ai
    assert 0.0 < conf <= 1.0


def test_classify_falls_back_when_no_match() -> None:
    from content_engine.models import Module
    from content_engine.stages.classify import classify_one

    module, conf = classify_one("foo bar baz", "lorem ipsum", Module.macro)
    assert module == Module.macro
    assert conf == 0.5


# ---------- SimHash ----------
def test_simhash_is_stable_and_16_hex() -> None:
    h1 = simhash64("OpenAI 发布 GPT-5 模型", "推理与训练效率提升 30%")
    h2 = simhash64("OpenAI 发布 GPT-5 模型", "推理与训练效率提升 30%")
    assert h1 == h2
    assert isinstance(h1, str) and len(h1) == 16
    int(h1, 16)  # 必须是合法 hex


def test_simhash_returns_none_on_empty_features() -> None:
    assert simhash64("", "") is None
    assert simhash64("   ", "   ") is None


def test_simhash_near_duplicate_has_small_hamming() -> None:
    """转载场景：标题和正文几乎一致，仅有 1-2 个字符差异，汉明距离应很小。"""
    a = simhash64(
        "OpenAI 发布 GPT-5 模型，推理速度大幅提升",
        "OpenAI 在凌晨发布 GPT-5 模型，主要提升了推理速度与训练效率，业界普遍关注。",
    )
    b = simhash64(
        "OpenAI 发布 GPT-5 模型，推理速度显著提升",
        "OpenAI 在凌晨发布 GPT-5 模型，主要提升了推理速度与训练效率，业界普遍关注度高。",
    )
    assert a is not None and b is not None
    # 实测同一段文本仅替换/追加少量字符，汉明距离通常 ≤8（远小于不同新闻的 ~32）
    assert hamming_distance(a, b) <= 8


def test_simhash_unrelated_has_large_hamming() -> None:
    """完全不同主题的两条新闻，汉明距离应明显较大（统计意义上 ≥ 20）。"""
    a = simhash64("美联储宣布加息 25 基点", "美联储 FOMC 会议决议本次加息 25 个基点")
    b = simhash64("苹果发布新款 iPhone", "苹果公司于秋季发布会推出新款 iPhone 系列")
    assert a is not None and b is not None
    assert hamming_distance(a, b) >= 12


def test_hamming_accepts_int_and_hex() -> None:
    assert hamming_distance("0000000000000000", "0000000000000001") == 1
    assert hamming_distance(0xF, 0x0) == 4
    assert hamming_distance("ffffffffffffffff", "0000000000000000") == 64
