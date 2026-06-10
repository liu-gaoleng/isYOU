# Spec: 阶段 1.3 扩展 — RemoteOpenAIProvider 预研

## Background
阶段 1.3 已落地 [LocalBgeProvider](file:///Users/bytedance/liu/isYOU/content_engine/services/embedding.py)，但用户已明确「后续会全部切到 API 调用」。
现在是预研阶段：先把远程 provider 的代码骨架与单测搭好，需要时改一个环境变量即可切换，不阻塞当前流水线（默认仍 local）。

## Goal
- 新增 `RemoteOpenAIProvider`：调用 OpenAI 兼容 `/v1/embeddings` 接口（豆包/阿里/智谱/OpenAI 通用）
- 工厂支持 `provider=remote` 分支
- 维度 fail-fast：首次响应若与 `settings.embedding.dim` 不一致，立即抛错（避免脏数据写到 pgvector(512) 列）
- 单测用 monkeypatch 模拟 HTTP，无需真实 API key
- 默认仍走 local，**完全不影响**现有功能

## Scope (in / out)
**In Scope**
- `config/settings.py` 新增 `RD_EMBEDDING_REMOTE_*` 子组（base_url/api_key/model/timeout），与 `LLMSettings` 解耦
- `services/embedding.py` 新增 `RemoteOpenAIProvider` 类
- `_build_provider()` 工厂加 `remote` 分支
- `tests/test_embedding_service.py` 追加 ≥3 个用例：成功路径、维度不一致 fail-fast、HTTP 错误向上传播
- 不动 `clean.py` / `embed.py`：它们都通过 `embed_texts()` 间接消费，provider 切换对它们透明

**Out of Scope**
- 真实联调（用户没给 key，今天不做）
- 流式调用 / 重试退避（阶段 3.1 LLM 调用队列时一起做）
- 火山方舟私有协议（如有需要后续单开 spec）

## Approach

### A. 配置（settings.py）
在 `EmbeddingSettings` 内追加：
```python
remote_base_url: str = Field(default="", validation_alias="RD_EMBEDDING_REMOTE_BASE_URL")
remote_api_key: str = Field(default="", validation_alias="RD_EMBEDDING_REMOTE_API_KEY")
remote_model: str = Field(default="text-embedding-3-small", validation_alias="RD_EMBEDDING_REMOTE_MODEL")
remote_timeout: int = Field(default=30, validation_alias="RD_EMBEDDING_REMOTE_TIMEOUT")
```

### B. RemoteOpenAIProvider
```python
class RemoteOpenAIProvider:
    def __init__(self, base_url, api_key, model, dim, timeout=30):
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self._model = model
        self._timeout = timeout
        self._expected_dim = dim
        self.dim = dim

    def encode(self, texts):
        if not texts:
            return []
        payload = json.dumps({"model": self._model, "input": texts}).encode("utf-8")
        req = urllib.request.Request(self._url, data=payload, headers=self._headers)
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # OpenAI compat: data["data"] = [{"embedding": [...], "index": i}, ...]
        sorted_items = sorted(data["data"], key=lambda x: x.get("index", 0))
        vecs = [item["embedding"] for item in sorted_items]
        # 维度 fail-fast
        if vecs and len(vecs[0]) != self._expected_dim:
            raise ValueError(
                f"remote embedding dim={len(vecs[0])} != configured dim={self._expected_dim}; "
                f"调整 RD_EMBEDDING_DIM 或更换 model"
            )
        return vecs
```

### C. 工厂
```python
def _build_provider():
    cfg = settings.embedding
    if cfg.provider == "local":
        return LocalBgeProvider(...)
    if cfg.provider == "remote":
        if not cfg.remote_base_url or not cfg.remote_api_key:
            raise ValueError("provider=remote 需要 RD_EMBEDDING_REMOTE_BASE_URL 与 _API_KEY")
        return RemoteOpenAIProvider(
            base_url=cfg.remote_base_url,
            api_key=cfg.remote_api_key,
            model=cfg.remote_model,
            dim=cfg.dim,
            timeout=cfg.remote_timeout,
        )
    raise ValueError(f"未知 embedding provider: {cfg.provider!r}")
```

### D. 单测策略
不真实打网，用 monkeypatch 替换 `urllib.request.urlopen` 为 fake context manager：
- 用例 1：响应正常 → 返回向量列表，顺序按 index 还原
- 用例 2：响应维度与 `settings.embedding.dim` 不一致 → 抛 `ValueError`，错误信息含 dim 值
- 用例 3：HTTP 错误（urlopen raise URLError）→ 异常向上传播，不被吞

## Acceptance Criteria
- `RD_EMBEDDING_PROVIDER=remote` + `RD_EMBEDDING_REMOTE_BASE_URL/API_KEY/MODEL` 完整时，工厂可构造 `RemoteOpenAIProvider`
- `provider=remote` 但 base_url/api_key 留空时，工厂抛出可读异常
- 维度校验：远程响应维度 ≠ `settings.embedding.dim` 时立即 fail
- 默认 `provider=local` 不变，现有 36 个 pytest 用例不退化
- 新增 ≥3 个 RemoteOpenAIProvider 单测全绿

## Open Questions
（暂无；待用户提供真实平台/key 后做联调时再开新 spec）
