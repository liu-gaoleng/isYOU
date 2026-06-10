"""阶段 1.3 单测：EmbeddingProvider 工厂 + 协议契约。

不真实加载 sentence-transformers 模型（避免 CI 下载权重 + 跑 CPU 推理）；
通过 monkeypatch 注入 mock provider，测：
1. ``get_embedding_provider`` 单例缓存命中；
2. ``embed_texts`` 一次性返回与输入等长、维度一致的向量列表；
3. ``EmbeddingProvider`` Protocol 的 isinstance 校验通过（runtime_checkable）。
4. ``RemoteOpenAIProvider`` 远程协议契约（成功 / 维度不匹配 / 缺凭证）——monkeypatch urlopen，不真实联调。
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest

from content_engine.services import embedding as embedding_module
from content_engine.services.embedding import (
    EmbeddingProvider,
    RemoteOpenAIProvider,
    embed_texts,
    get_embedding_provider,
)


class _MockProvider:
    """简易固定向量 provider，便于测试 service 层契约。"""

    dim = 4

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts]


@pytest.fixture(autouse=True)
def _reset_provider_cache():
    """每个用例后清掉 lru_cache，避免状态污染。"""
    yield
    get_embedding_provider.cache_clear()


def test_mock_provider_satisfies_protocol():
    p = _MockProvider()
    # Protocol 是 runtime_checkable，可用 isinstance 校验
    assert isinstance(p, EmbeddingProvider)
    assert p.dim == 4


def test_factory_caches_provider(monkeypatch):
    """get_embedding_provider 必须是单例：连续两次返回同一对象。"""
    monkeypatch.setattr(embedding_module, "_build_provider", lambda: _MockProvider())
    p1 = get_embedding_provider()
    p2 = get_embedding_provider()
    assert p1 is p2


def test_embed_texts_returns_vectors_with_consistent_dim(monkeypatch):
    """embed_texts 应返回 dim 与输入一一对应的列表。"""
    mock = _MockProvider()
    monkeypatch.setattr(embedding_module, "_build_provider", lambda: mock)
    out = embed_texts(["a", "b", "c"])
    assert len(out) == 3
    assert all(len(v) == mock.dim for v in out)
    # 第二次调用复用同一 provider（验证 lru_cache）
    embed_texts(["d"])
    assert len(mock.calls) == 2


def test_embed_texts_empty_input(monkeypatch):
    """传空列表时应返回空列表，不应触发 provider 调用。"""
    mock = _MockProvider()
    monkeypatch.setattr(embedding_module, "_build_provider", lambda: mock)
    assert embed_texts([]) == []
    assert mock.calls == [[]]


# ---------------------------------------------------------------------------
# RemoteOpenAIProvider：monkeypatch urlopen 模拟远程 /v1/embeddings 响应
# ---------------------------------------------------------------------------
def _fake_urlopen_factory(payload: dict, captured: dict | None = None):
    """构造一个 fake urlopen：把 fake JSON 包成可被 ``with`` 使用的 response。"""

    @contextmanager
    def _fake(req, timeout=None):
        if captured is not None:
            captured["url"] = req.full_url
            captured["headers"] = dict(req.header_items())
            captured["body"] = json.loads(req.data.decode("utf-8")) if req.data else None
            captured["timeout"] = timeout
        yield io.BytesIO(json.dumps(payload).encode("utf-8"))

    return _fake


def test_remote_provider_success(monkeypatch):
    """正常路径：响应顺序乱序也按 index 还原；维度匹配 → 返回 vec 列表。"""
    captured: dict = {}
    payload = {
        "data": [
            # 故意 index 顺序乱排，验证按 index 还原
            {"index": 1, "embedding": [0.4, 0.5, 0.6, 0.7]},
            {"index": 0, "embedding": [0.0, 0.1, 0.2, 0.3]},
        ],
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }
    monkeypatch.setattr(
        embedding_module.urllib.request,
        "urlopen",
        _fake_urlopen_factory(payload, captured),
    )

    p = RemoteOpenAIProvider(
        base_url="https://api.example.com/v1",
        api_key="sk-test",
        model="text-embedding-3-small",
        dim=4,
        timeout=10,
    )
    out = p.encode(["你好", "world"])

    assert out == [[0.0, 0.1, 0.2, 0.3], [0.4, 0.5, 0.6, 0.7]]
    assert captured["url"] == "https://api.example.com/v1/embeddings"
    assert captured["body"] == {"model": "text-embedding-3-small", "input": ["你好", "world"]}
    # urllib 会把 header 名称 capitalize：Authorization / Content-type
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["timeout"] == 10


def test_remote_provider_empty_input_skips_http(monkeypatch):
    """空输入不应触发 HTTP 调用。"""
    called = {"n": 0}

    @contextmanager
    def _should_not_call(req, timeout=None):  # pragma: no cover
        called["n"] += 1
        yield io.BytesIO(b"{}")

    monkeypatch.setattr(embedding_module.urllib.request, "urlopen", _should_not_call)
    p = RemoteOpenAIProvider(
        base_url="https://api.example.com",
        api_key="sk",
        model="m",
        dim=4,
    )
    assert p.encode([]) == []
    assert called["n"] == 0


def test_remote_provider_dim_mismatch_raises(monkeypatch):
    """响应 dim ≠ 配置 dim → fail-fast 抛 ValueError。"""
    payload = {
        # 配置 dim=4，但响应只给 3 维 → 必须 fail-fast
        "data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}],
    }
    monkeypatch.setattr(
        embedding_module.urllib.request,
        "urlopen",
        _fake_urlopen_factory(payload),
    )
    p = RemoteOpenAIProvider(
        base_url="https://api.example.com",
        api_key="sk",
        model="m",
        dim=4,
    )
    with pytest.raises(ValueError) as excinfo:
        p.encode(["x"])
    msg = str(excinfo.value)
    assert "dim=3" in msg
    assert "dim=4" in msg


def test_remote_factory_missing_credentials(monkeypatch):
    """provider=remote 但 base_url/api_key 任一为空 → 工厂抛清晰 ValueError。"""
    monkeypatch.setattr(embedding_module.settings.embedding, "provider", "remote")
    monkeypatch.setattr(embedding_module.settings.embedding, "remote_base_url", "")
    monkeypatch.setattr(embedding_module.settings.embedding, "remote_api_key", "")

    with pytest.raises(ValueError) as excinfo:
        embedding_module._build_provider()
    assert "RD_EMBEDDING_REMOTE_BASE_URL" in str(excinfo.value)


def test_remote_factory_constructs_provider(monkeypatch):
    """provider=remote 凭证齐全 → 工厂返回 RemoteOpenAIProvider 实例。"""
    monkeypatch.setattr(embedding_module.settings.embedding, "provider", "remote")
    monkeypatch.setattr(
        embedding_module.settings.embedding,
        "remote_base_url",
        "https://api.example.com/v1",
    )
    monkeypatch.setattr(embedding_module.settings.embedding, "remote_api_key", "sk-x")
    monkeypatch.setattr(
        embedding_module.settings.embedding, "remote_model", "doubao-embedding"
    )
    monkeypatch.setattr(embedding_module.settings.embedding, "dim", 4)

    p = embedding_module._build_provider()
    assert isinstance(p, RemoteOpenAIProvider)
    assert p.dim == 4
