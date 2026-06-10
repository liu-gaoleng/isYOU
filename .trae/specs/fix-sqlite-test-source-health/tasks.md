# Tasks

- [x] Task 1: 修复 IdMixin 主键在 SQLite 上的自增行为
  - [x] SubTask 1.1: 修改 `content_engine/models/base.py`，把 `IdMixin.id` 改为 `BigInteger().with_variant(Integer, "sqlite")`，并保留 `primary_key=True, autoincrement=True`
  - [x] SubTask 1.2: 不动 Alembic 迁移；不动业务 schema.py；不动单测
- [x] Task 2: 验证修复有效
  - [x] SubTask 2.1: 在仓库根目录跑 `pytest`，确认 `test_source_health.py` 3 个用例转绿，原有 19 个用例仍全部通过（共 22 个 passed）
  - [x] SubTask 2.2: 跑一次端到端 `python -m content_engine.stages.collect`，确认 PG 上的真实采集仍能正常写入 `raw_articles` 与 `source_health`（PG 行为不退化）

# Task Dependencies
- Task 2 depends on Task 1
