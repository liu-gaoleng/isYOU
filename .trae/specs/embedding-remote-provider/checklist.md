# Checklist

- [x] `EmbeddingSettings` 暴露 `remote_base_url` / `remote_api_key` / `remote_model` / `remote_timeout` 四项配置
- [x] `RemoteOpenAIProvider` 实现 `EmbeddingProvider` 协议，POST `/v1/embeddings` 兼容 OpenAI 响应格式
- [x] 空输入 `encode([])` 不发请求，返回空列表
- [x] 响应 `data[]` 按 `index` 字段还原顺序
- [x] 响应维度 ≠ `settings.embedding.dim` 时抛 `ValueError`，错误信息含具体 dim 值
- [x] 工厂 `provider=remote` 且 base_url/api_key 任一为空时抛可读异常
- [x] 默认 `provider=local` 不变，原有 36 个用例全绿
- [x] 新增 3 个 `RemoteOpenAIProvider` 单测全绿（成功路径 / 维度不匹配 / 缺凭证）
