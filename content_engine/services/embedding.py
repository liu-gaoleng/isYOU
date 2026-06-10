"""Embedding 服务（阶段 1.3）。

Provider 抽象 + 多种实现：
- ``LocalBgeProvider``：本地 sentence-transformers 加载 bge-small-zh-v1.5（默认 512 维），
  norm=True 让向量自带单位长度，cos 相似度等价点积；
- ``RemoteOpenAIProvider``：OpenAI 兼容 ``POST /v1/embeddings``（豆包/阿里/智谱/OpenAI 等通用），
  通过 ``RD_EMBEDDING_PROVIDER=remote`` + ``RD_EMBEDDING_REMOTE_*`` 环境变量启用，
  当前为预研形态，默认仍走 local。

外部使用：

    from content_engine.services.embedding import embed_texts
    vecs = embed_texts(["hello", "world"])

工厂带 ``lru_cache``，整个进程只 lazy-init 一次模型，避免重复加载耗时。
"""

from __future__ import annotations

import json
import urllib.request
from functools import lru_cache
from typing import Protocol, runtime_checkable

from content_engine.config import settings


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding 提供方协议。

    实现方需满足：
    - ``dim``：向量维度（必须与 schema/EMBEDDING_DIM 对齐）；
    - ``encode(texts)``：批量编码文本列表，返回 ``list[list[float]]``，每条向量长度 == ``dim``；
    - 默认 L2 归一化（norm=True），方便下游直接 cos 相似度。
    """

    dim: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class LocalBgeProvider:
    """本地 sentence-transformers 加载 BAAI/bge-small-zh-v1.5。

    模型 lazy 加载：第一次 ``encode`` 才真正初始化（避免 import 期阻塞 + 测试可注入 mock）。
    """

    def __init__(
        self,
        model_name: str,
        device: str = "cpu",
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._device = device
        self._batch = batch_size
        self._model = None
        # dim 在 lazy-init 后从模型反查；先按配置值占位，避免 import 期暴露。
        self.dim = settings.embedding.dim

    def _ensure_loaded(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name, device=self._device)
            self.dim = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self._ensure_loaded()
        # normalize_embeddings=True：向量单位化，cos = dot product
        vecs = self._model.encode(
            texts,
            batch_size=self._batch,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [v.tolist() for v in vecs]


class RemoteOpenAIProvider:
    """OpenAI 兼容 ``POST /v1/embeddings`` 远程 provider（预研形态）。

    支持平台：豆包、阿里百炼、智谱、OpenAI 等大多数兼容协议的服务。
    协议契约（请求 / 响应）：

        POST {base_url}/embeddings
        Authorization: Bearer {api_key}
        body  = {"model": "...", "input": ["text1", "text2"]}
        resp  = {"data": [{"embedding": [...], "index": 0}, ...], "usage": {...}}

    设计要点：
    - 维度 fail-fast：首次响应若与 ``settings.embedding.dim`` 不匹配，立即抛 ``ValueError``，
      防止脏数据写入 pgvector(512) 列；
    - 响应按 ``index`` 还原顺序：少数实现不保证保序；
    - 当前未做重试退避，留到阶段 3.1 LLM 调用队列时统一处理；
    - 与 ``LLMSettings`` 解耦：用 ``RD_EMBEDDING_REMOTE_*`` 独立环境变量。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        dim: int,
        timeout: int = 30,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model
        self._timeout = timeout
        self._expected_dim = dim
        self.dim = dim

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload = json.dumps(
            {"model": self._model, "input": texts},
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(self._url, data=payload, headers=self._headers)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        items = data.get("data") or []
        # 按 index 还原顺序（OpenAI 协议保证有 index 字段；兜底用列表序）
        sorted_items = sorted(items, key=lambda x: x.get("index", 0))
        vecs = [item["embedding"] for item in sorted_items]

        # 维度 fail-fast（pgvector 列已锁 512，不允许混用维度）
        if vecs and len(vecs[0]) != self._expected_dim:
            raise ValueError(
                f"remote embedding dim={len(vecs[0])} != configured dim={self._expected_dim}; "
                f"请调整 RD_EMBEDDING_DIM 或更换 RD_EMBEDDING_REMOTE_MODEL"
            )
        return vecs


def _build_provider() -> EmbeddingProvider:
    """根据配置选 provider；未来加新协议在此分支即可。"""
    cfg = settings.embedding
    if cfg.provider == "local":
        return LocalBgeProvider(
            model_name=cfg.model_name,
            device=cfg.device,
            batch_size=cfg.batch_size,
        )
    if cfg.provider == "remote":
        if not cfg.remote_base_url or not cfg.remote_api_key:
            raise ValueError(
                "provider=remote 需要 RD_EMBEDDING_REMOTE_BASE_URL 与 RD_EMBEDDING_REMOTE_API_KEY"
            )
        return RemoteOpenAIProvider(
            base_url=cfg.remote_base_url,
            api_key=cfg.remote_api_key,
            model=cfg.remote_model,
            dim=cfg.dim,
            timeout=cfg.remote_timeout,
        )
    raise ValueError(f"未知 embedding provider: {cfg.provider!r}")


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    """单例工厂：进程内只构造一次（首次调用 encode 才 lazy-load 模型权重）。"""
    return _build_provider()


def embed_texts(texts: list[str]) -> list[list[float]]:
    """模块级便捷函数：直接拿编码后的向量。"""
    return get_embedding_provider().encode(texts)


__all__ = [
    "EmbeddingProvider",
    "LocalBgeProvider",
    "RemoteOpenAIProvider",
    "get_embedding_provider",
    "embed_texts",
]
