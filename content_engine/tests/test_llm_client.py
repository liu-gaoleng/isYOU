"""阶段 3.1 单测：统一 LLM 客户端（限流 / 重试 / 退避 / 未配置）。

不连真实网络：通过 monkeypatch 替换 ``client._do_request`` 注入受控行为，
避免真实 HTTP，测试重点放在重试策略与降级语义上。
"""

from __future__ import annotations

import pytest

from content_engine.services import llm_client as llm_mod
from content_engine.services.llm_client import (
    LLMClient,
    LLMError,
    LLMResponse,
    _RetryableError,
    _TokenBucket,
)


def _client(rate=100.0, max_retries=3, backoff_base=0.0):
    return LLMClient(
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        model="gpt-test",
        rate_per_sec=rate,
        max_retries=max_retries,
        backoff_base=backoff_base,
        timeout=5,
    )


def test_disabled_when_no_api_key():
    client = LLMClient(
        api_key="",
        base_url="https://x/v1",
        model="m",
        rate_per_sec=1.0,
        max_retries=1,
        backoff_base=0.0,
        timeout=1,
    )
    assert client.enabled is False
    with pytest.raises(LLMError):
        client.chat_json("hi")


def test_success_first_try(monkeypatch):
    client = _client()
    resp = LLMResponse(content='{"ok":1}', model="gpt-test", usage={"total_tokens": 5})
    monkeypatch.setattr(client, "_do_request", lambda payload: resp)
    out = client.chat_json("hi")
    assert out.content == '{"ok":1}'
    assert out.usage == {"total_tokens": 5}


def test_retry_then_success(monkeypatch):
    client = _client(max_retries=3)
    calls = {"n": 0}

    def _flaky(payload):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _RetryableError("HTTP 503")
        return LLMResponse(content="{}", model="gpt-test")

    sleeps: list[int] = []
    monkeypatch.setattr(client, "_do_request", _flaky)
    monkeypatch.setattr(client, "_sleep_backoff", lambda attempt: sleeps.append(attempt))

    out = client.chat_json("hi")
    assert out.content == "{}"
    assert calls["n"] == 3
    # 第 1、2 次失败各退避一次（attempt=0,1）
    assert sleeps == [0, 1]


def test_retry_exhausted_raises(monkeypatch):
    client = _client(max_retries=2)
    calls = {"n": 0}

    def _always_fail(payload):
        calls["n"] += 1
        raise _RetryableError("HTTP 429")

    monkeypatch.setattr(client, "_do_request", _always_fail)
    monkeypatch.setattr(client, "_sleep_backoff", lambda attempt: None)

    with pytest.raises(LLMError):
        client.chat_json("hi")
    # max_retries=2 → 共 3 次尝试（attempt 0/1/2）
    assert calls["n"] == 3


def test_non_retryable_raises_immediately(monkeypatch):
    client = _client(max_retries=3)
    calls = {"n": 0}

    def _fatal(payload):
        calls["n"] += 1
        raise LLMError("HTTP 400: bad request")

    monkeypatch.setattr(client, "_do_request", _fatal)

    with pytest.raises(LLMError):
        client.chat_json("hi")
    # 不可重试 → 只尝试 1 次
    assert calls["n"] == 1


def test_token_bucket_blocks_when_empty():
    """容量耗尽后 acquire 需等待补充令牌。"""
    bucket = _TokenBucket(rate=2.0)  # 容量 2
    import time

    bucket.acquire()
    bucket.acquire()
    start = time.monotonic()
    bucket.acquire()  # 第 3 次需等约 0.5s（1/rate）
    elapsed = time.monotonic() - start
    assert elapsed >= 0.3


def test_get_llm_client_is_singleton():
    llm_mod.get_llm_client.cache_clear()
    a = llm_mod.get_llm_client()
    b = llm_mod.get_llm_client()
    assert a is b
