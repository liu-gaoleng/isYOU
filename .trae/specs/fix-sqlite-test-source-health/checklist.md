# Checklist

- [x] `IdMixin.id` 已改为 `BigInteger().with_variant(Integer, "sqlite")`，仍带 `primary_key=True, autoincrement=True`
- [x] 未新增任何 Alembic 迁移文件，未修改既有迁移
- [x] `pytest` 全部通过（22 个用例：19 旧 + 3 新）
- [x] `test_source_health.py::test_consecutive_failures_accumulate_then_reset` 转绿
- [x] `test_source_health.py::test_partial_status_resets_failures` 转绿
- [x] `test_source_health.py::test_error_text_truncated` 转绿
- [x] PG 端真实跑一次 `collect.run()`，`source_health` 表正常新增记录、`raw_articles` 主键仍由 BIGSERIAL 提供
