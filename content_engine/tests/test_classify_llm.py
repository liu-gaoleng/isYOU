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
from content_engine.stages.classify import _classify_llm, classify_one


# ---- 规则函数本身（不依赖 LLM）-------------------------------------------------
def test_classify_one_rule_match():
    module, conf = classify_one("OpenAI 发布 GPT-5", "大模型推理能力大幅提升", Module.tech)
    assert module == Module.ai
    assert 0.0 < conf <= 1.0


def test_classify_one_fallback_when_zero_score():
    module, conf = classify_one("一段无关键词的纯文本", "完全空白的内容", Module.macro)
    assert module == Module.macro
    assert conf == 0.5


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
    module, conf, tags = result
    assert module == Module.ai
    assert conf == pytest.approx(0.88)
    assert tags == ["大模型", "推理", "Agent"]


def test_llm_clamps_out_of_range_confidence(monkeypatch):
    """模型返回 confidence=1.5 → 应钳到 1.0。"""
    _patch_client(
        monkeypatch,
        content=json.dumps({"module": "ai", "confidence": 1.5, "tags": []}),
    )
    _, conf, _ = _classify_llm("t", "c", Module.tech)
    assert conf == 1.0
