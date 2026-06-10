# Tasks

- [x] Task 1: 配置扩展
  - [x] SubTask 1.1: `EmbeddingSettings` 内追加 `remote_base_url` / `remote_api_key` / `remote_model` / `remote_timeout` 四项，分别绑定 `RD_EMBEDDING_REMOTE_BASE_URL/API_KEY/MODEL/TIMEOUT`，默认值合理（model=text-embedding-3-small，timeout=30）

- [x] Task 2: RemoteOpenAIProvider 实现
  - [x] SubTask 2.1: `services/embedding.py` 新增 `RemoteOpenAIProvider` 类，构造时接 base_url/api_key/model/dim/timeout
  - [x] SubTask 2.2: `encode(texts)`：POST `/v1/embeddings`，按 `data[i].index` 还原顺序；空输入返回空列表
  - [x] SubTask 2.3: 维度 fail-fast：响应向量长度 ≠ `dim` 时抛 `ValueError`
  - [x] SubTask 2.4: 工厂 `_build_provider()` 加 `remote` 分支：缺 base_url/api_key 时抛清晰异常

- [x] Task 3: 单元测试
  - [x] SubTask 3.1: `test_embedding_service.py` 追加 `test_remote_provider_success`：monkeypatch urlopen 返回 OpenAI 格式 JSON，验证 vec 顺序与维度
  - [x] SubTask 3.2: 追加 `test_remote_provider_dim_mismatch`：响应维度故意填 256，断言 `ValueError` 抛出
  - [x] SubTask 3.3: 追加 `test_remote_factory_missing_credentials`：monkeypatch `provider=remote` 且空 base_url/api_key，断言 `_build_provider` 抛出包含「需要」字样的 ValueError

- [x] Task 4: 验证 + 收口
  - [x] SubTask 4.1: `pytest` 全绿（36 + 3 = 39 用例不退化）
  - [x] SubTask 4.2: 在 tasks.md 与 checklist.md 勾选所有项目

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2
- Task 4 depends on Task 3
