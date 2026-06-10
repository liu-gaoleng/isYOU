# Checklist

- [x] `pyproject.toml` 已加入 `sentence-transformers` 依赖且 venv 安装成功
- [x] `Settings.embedding` 子配置可读取（provider/model_name/dim/device/batch_size/semantic_dedup_threshold）
- [x] `services/embedding.py` 提供 `EmbeddingProvider` 抽象 + `LocalBgeProvider` 实现 + `embed_texts()` 工厂，dim=512 与配置一致
- [x] `EMBEDDING_DIM = 512` 与 alembic 0006 迁移一致
- [x] `alembic upgrade head` 成功，PG 中 `raw_articles.embedding`、`events.centroid` 类型为 vector(512)
- [x] `clean.py` 在 SimHash 未命中后调 `embed_texts` 并写入 `art.embedding`
- [x] `clean.py` 加入 cos ≥ 0.92 语义去重，命中标 dropped 并累加 `stats["dropped_semantic"]`
- [x] `clean.py` embed 失败时不阻塞，仍标 cleaned 并写 `last_error`
- [x] `stages/embed.py` 可独立运行，扫描 NULL 并批量回填
- [x] 在 PG 上跑一次回填后 `raw_articles WHERE embedding IS NULL AND status != raw` 计数为 0
- [x] `tests/test_embedding_service.py` 通过
- [x] `tests/test_clean_semantic_dedup.py` 通过（cos≥0.92 / cos<0.92 两条路径均验证）
- [x] `pytest` 全绿不退化
