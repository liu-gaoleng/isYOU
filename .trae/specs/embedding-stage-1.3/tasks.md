# Tasks

- [x] Task 1: 依赖 + 配置
  - [x] SubTask 1.1: `pyproject.toml` 加 `sentence-transformers>=2.7`，content_engine/.venv 装上
  - [x] SubTask 1.2: `config/settings.py` 加 `EmbeddingSettings` 子配置：`provider=local`、`model_name=BAAI/bge-small-zh-v1.5`、`dim=512`、`device=cpu`、`batch_size=32`、`semantic_dedup_threshold=0.92`、`semantic_dedup_window_hours=72`

- [x] Task 2: EmbeddingProvider 服务层
  - [x] SubTask 2.1: 新增 `content_engine/services/__init__.py`
  - [x] SubTask 2.2: 新增 `content_engine/services/embedding.py`：`EmbeddingProvider` Protocol + `LocalBgeProvider` 实现（lazy load 模型；norm=True 便于直接做 cos）+ `get_embedding_provider()` 工厂（带 lru_cache）+ `embed_texts(texts)` 模块级便捷函数

- [x] Task 3: schema + 迁移
  - [x] SubTask 3.1: 修改 `content_engine/models/schema.py` 中 `EMBEDDING_DIM = 512`
  - [x] SubTask 3.2: 新增 `migrations/versions/0006_embedding_dim_512.py`：drop 现有 1024 维列后重建 512 维列（现有数据均 NULL，无损）
  - [x] SubTask 3.3: 在 PG 上跑 `alembic upgrade head`，确认列类型变更成功

- [x] Task 4: clean 阶段语义去重
  - [x] SubTask 4.1: `clean.py` 在 SimHash 未命中后生成 embedding，写入 `art.embedding`
  - [x] SubTask 4.2: 在已 cleaned/classified/clustered 同 72h 窗口内查 `embedding IS NOT NULL` 的最近邻 cos；cos ≥ 0.92 命中 dropped，否则 cleaned
  - [x] SubTask 4.3: embed 失败异常处理：记 `last_error=embed_failed: ...` 但仍标 cleaned，不阻塞流水线
  - [x] SubTask 4.4: stats 增加 `dropped_semantic` 计数

- [x] Task 5: 历史回填脚本
  - [x] SubTask 5.1: 新增 `content_engine/stages/embed.py`：批量扫描 `embedding IS NULL AND status IN (cleaned, classified, clustered)`，分批 encode 后回填，可独立 `python -m content_engine.stages.embed` 跑
  - [x] SubTask 5.2: 在 PG 跑一次回填，确认 `embedding IS NULL` 计数从 N → 0

- [x] Task 6: 单元测试
  - [x] SubTask 6.1: `tests/test_embedding_service.py`：mock provider 验证 `embed_texts` 返回 dim 一致；`get_embedding_provider` 缓存命中
  - [x] SubTask 6.2: `tests/test_clean_semantic_dedup.py`：monkeypatch `embed_texts` 直接喂构造好的向量；构造一条已 cleaned 的近邻文章 + 当前 raw 文章；断言 cos≥0.92 时新文章 dropped、`stats["dropped_semantic"]==1`；cos<0.92 时仍 cleaned

- [x] Task 7: 验证
  - [x] SubTask 7.1: `pytest` 全部用例绿（含 ≥2 个新增）
  - [x] SubTask 7.2: `python -m content_engine.stages.embed` 无报错且回填条数 = 之前 NULL 条数
  - [x] SubTask 7.3: 在 tasks.md / checklist.md 勾选所有项目

# Task Dependencies
- Task 2 depends on Task 1
- Task 3 depends on Task 2（schema 改了之后 ORM 需要 reload；可并行）
- Task 4 depends on Task 2 + Task 3
- Task 5 depends on Task 2 + Task 3
- Task 6 depends on Task 2 + Task 4
- Task 7 depends on Task 4 + Task 5 + Task 6
