"""阶段 3.1：统一 LLM 调用客户端（限流 + 重试 + 指数退避 + 用量留痕）。

为什么要有这一层：
classify / summarize 之前各自裸调 urllib，没有限流/重试/退避，一旦 429 或网络
抖动就直接失败回退到抽取式，浪费了本可恢复的调用。这里把 OpenAI 兼容
``POST /chat/completions`` 收敛成一个进程内单例客户端：

- **令牌桶限流**：``settings.llm.rate_per_sec`` QPS，线程安全（threading.Lock）；
- **重试 + 指数退避**：可重试错误（HTTP 429/5xx、URLError、TimeoutError）最多
  ``max_retries`` 次，退避 = ``backoff_base * 2**attempt`` + 抖动；
- **非可重试错误**（4xx 非 429 / JSON 解析失败）直接抛 ``LLMError``，调用方兜底；
- **未配置**（无 api_key）：``enabled=False``，调用方据此走规则/抽取式兜底。

用法：
    from content_engine.services.llm_client import get_llm_client
    client = get_llm_client()
    if client.enabled:
        resp = client.chat_json(prompt, temperature=0.0)
        data = json.loads(resp.content)   # resp.usage / resp.model 已带回
"""

from __future__ import annotations

import json
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from functools import lru_cache

from content_engine.config import settings

# 触发重试的 HTTP 状态码（429 限流 + 5xx 服务端临时故障）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """LLM 调用最终失败（重试耗尽或不可重试错误）。"""


@dataclass
class LLMResponse:
    content: str
    model: str
    usage: dict | None = field(default=None)


class _TokenBucket:
    """简单令牌桶：每秒注入 ``rate`` 个令牌，容量 = max(rate, 1)。

    acquire() 在令牌不足时阻塞等待，线程安全。
    """

    def __init__(self, rate: float):
        self._rate = max(rate, 0.1)
        self._capacity = max(rate, 1.0)
        self._tokens = self._capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                # 按经过时间补充令牌
                self._tokens = min(
                    self._capacity, self._tokens + (now - self._last) * self._rate
                )
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # 计算需等待的时间（拿到 1 个令牌）
                wait = (1.0 - self._tokens) / self._rate
                time.sleep(wait)


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        rate_per_sec: float,
        max_retries: int,
        backoff_base: float,
        timeout: int,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._timeout = timeout
        self._bucket = _TokenBucket(rate_per_sec)

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    def chat_json(self, prompt: str, temperature: float = 0.0) -> LLMResponse:
        """发起一次 JSON-mode 对话补全，返回 LLMResponse。

        失败（重试耗尽或不可重试）抛 LLMError。
        """
        if not self.enabled:
            raise LLMError("LLM not configured (empty api_key)")

        payload = json.dumps(
            {
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            self._bucket.acquire()
            try:
                return self._do_request(payload)
            except _RetryableError as e:
                last_exc = e
                if attempt >= self._max_retries:
                    break
                self._sleep_backoff(attempt)
            except LLMError:
                raise
        raise LLMError(f"LLM call failed after {self._max_retries} retries: {last_exc}")

    def _do_request(self, payload: bytes) -> LLMResponse:
        req = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in _RETRYABLE_STATUS:
                raise _RetryableError(f"HTTP {e.code}") from e
            raise LLMError(f"HTTP {e.code}: {e.reason}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise _RetryableError(str(e)) from e

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"malformed LLM response: {e}") from e
        return LLMResponse(content=content, model=self._model, usage=data.get("usage"))

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self._backoff_base * (2**attempt) + random.uniform(0, 0.5)
        time.sleep(delay)


class _RetryableError(Exception):
    """内部使用：标记可重试的临时故障。"""


@lru_cache(maxsize=1)
def get_llm_client() -> LLMClient:
    """进程内单例（令牌桶状态需跨调用共享）。"""
    return LLMClient(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
        model=settings.llm.model,
        rate_per_sec=settings.llm.rate_per_sec,
        max_retries=settings.llm.max_retries,
        backoff_base=settings.llm.backoff_base,
        timeout=settings.llm.timeout,
    )


__all__ = ["LLMClient", "LLMResponse", "LLMError", "get_llm_client"]
