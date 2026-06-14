"""阶段 2.1 单测：classify 阶段的 LLM 兜底逻辑。

只测纯函数 ``_classify_llm`` 与触发分支判定（不连真实 DB / 真实 LLM）：
1. LLM 未配置 → 返回 None；
2. urllib.urlopen 抛异常 → 返回 None；
3. LLM 返回非法 JSON → 返回 None；
4. LLM 返回非法 module → 返回 None；
5. 正常返回 → 解析为 (Module, confidence, tags)。
"""

from __future__ import annotations

import json

import pytest

from content_engine.models import Module
from content_engine.services.llm_client import LLMError, LLMResponse
from content_engine.stages import classify as classify_module
from content_engine.stages.classify import _classify_llm, _kw_hit, classify_one


# ---- 规则函数本身（不依赖 LLM）-------------------------------------------------
def test_classify_one_rule_match():
    module, conf = classify_one("OpenAI 发布 GPT-5", "大模型推理能力大幅提升", Module.tech)
    assert module == Module.ai
    assert 0.0 < conf <= 1.0


def test_classify_one_fallback_when_zero_score():
    module, conf = classify_one("一段无关键词的纯文本", "完全空白的内容", Module.macro)
    assert module == Module.macro
    assert conf == 0.5


# ---- 英文缩写词边界匹配（修复 "AI" 子串误命中 "AirPods"）------------------------
def test_kw_hit_ascii_word_boundary():
    text = "the airpods pro 3 are $179 at walmart"
    # "AI" 不应命中 "airpods" 内部的 "ai"
    assert _kw_hit("AI", text) is False
    # 独立英文缩写词应命中
    assert _kw_hit("AI", "what is ai today") is True


def test_kw_hit_ascii_adjacent_chinese():
    # 英文缩写紧贴中文（中文非 ASCII 边界），应命中
    assert _kw_hit("AI", "ai产品冷启动从0到1000") is True
    assert _kw_hit("GDP", "gdp增长5%") is True
    assert _kw_hit("IPO", "公司ipo上市") is True


def test_kw_hit_ascii_hyphen_boundary():
    # 连字符 / 数字也是边界
    assert _kw_hit("GPT", "openai gpt-5 发布") is True


def test_kw_hit_chinese_substring():
    # 中文关键词仍走子串匹配
    assert _kw_hit("大模型", "国产大模型再上新") is True
    assert _kw_hit("智能体", "推出面向个人的专属智能体") is True


def test_classify_airpods_not_misclassified_as_ai():
    """回归：AirPods 降价不应被规则判成 ai（曾 conf=1.0 硬错）。"""
    module, conf = classify_one(
        "The AirPods Pro 3 are $179 at Walmart, their best price",
        "苹果耳机降价促销",
        Module.tech,
    )
    assert module != Module.ai or conf < 1.0


# ---- LLM 兜底分支 -------------------------------------------------------------
class _FakeClient:
    """模拟 LLMClient：enabled + chat_json 返回预设 content 或抛异常。"""

    def __init__(self, content=None, exc=None, enabled=True):
        self._content = content
        self._exc = exc
        self.enabled = enabled

    def chat_json(self, prompt, temperature=0.0):
        if self._exc is not None:
            raise self._exc
        return LLMResponse(content=self._content, model="fake", usage=None)


def _patch_client(monkeypatch, **kwargs):
    client = _FakeClient(**kwargs)
    monkeypatch.setattr(classify_module, "get_llm_client", lambda: client)
    return client


def test_llm_disabled_returns_none(monkeypatch):
    _patch_client(monkeypatch, enabled=False)
    assert _classify_llm("t", "c", Module.tech) is None


def test_llm_call_exception(monkeypatch):
    _patch_client(monkeypatch, exc=LLMError("read timeout"))
    assert _classify_llm("t", "c", Module.tech) is None


def test_llm_returns_invalid_json(monkeypatch):
    # content 不是合法 JSON
    _patch_client(monkeypatch, content="not-json")
    assert _classify_llm("t", "c", Module.tech) is None


def test_llm_returns_invalid_module(monkeypatch):
    _patch_client(
        monkeypatch,
        content=json.dumps({"module": "sports", "confidence": 0.9, "tags": ["NBA"]}),
    )
    assert _classify_llm("t", "c", Module.tech) is None


def test_llm_normal_return(monkeypatch):
    _patch_client(
        monkeypatch,
        content=json.dumps(
            {"module": "ai", "confidence": 0.88, "tags": ["大模型", "推理", "Agent"]}
        ),
    )
    result = _classify_llm("某 AI 文章", "正文……", Module.tech)
    assert result is not None
    module, conf, tags, meta = result
    assert module == Module.ai
    assert conf == pytest.approx(0.88)
    assert tags == ["大模型", "推理", "Agent"]
    assert "cost" in meta


def test_llm_clamps_out_of_range_confidence(monkeypatch):
    """模型返回 confidence=1.5 → 应钳到 1.0。"""
    _patch_client(
        monkeypatch,
        content=json.dumps({"module": "ai", "confidence": 1.5, "tags": []}),
    )
    _, conf, _, _ = _classify_llm("t", "c", Module.tech)
    assert conf == 1.0
