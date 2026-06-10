# Tasks

- [x] Task 1: 更新「内容引擎实施计划.md」阶段 1 章节
  - [x] SubTask 1.1: 把阶段 1 子项顺序改为 1.1 SimHash（done）→ 1.2 健康监控（done）→ 1.3 Embedding → 1.4 摘要分级 → 1.5 FastAPI 层 → 1.6 信源扩展（六大画像）
  - [x] SubTask 1.2: 在阶段 1 章节顶部加一段「目标用户与端选型」说明（互联网/PM/投资人/创业者，iOS-first）

- [x] Task 2: 摘要分级（仅文档与 schema 落位，先不改 LLM 调用）
  - [x] SubTask 2.1: 在 `events` 表加 `card_summary VARCHAR(180)`、`detail_summary TEXT`；保留旧 `summary` 列作为兼容
  - [x] SubTask 2.2: 写 Alembic 迁移 `0004_card_and_detail_summary.py`
  - [x] SubTask 2.3: 修改 `summarize.py` 同时产出双摘要（占位实现：detail = 旧 summary；card = 前 120 字截断 + 句号收尾），先保证管线不退化

- [x] Task 3: 用户表预留 Apple 字段
  - [x] SubTask 3.1: 在 `schema.py` 新增 `users` 表（`id` BIGINT PK / `apple_user_id` VARCHAR(64) UNIQUE NULL / `email` VARCHAR(256) NULL / `created_via` VARCHAR(16) NOT NULL DEFAULT 'apple' / 时间戳）
  - [x] SubTask 3.2: 写 Alembic 迁移 `0005_users_apple_fields.py`
  - [x] SubTask 3.3: 暂不实现登录端点，仅完成表结构与 ORM 映射

- [x] Task 4: 引入 FastAPI 服务层（最小可用版）
  - [x] SubTask 4.1: `pyproject.toml` 加依赖 `fastapi` + `uvicorn[standard]`
  - [x] SubTask 4.2: 新建 `content_engine/api/__init__.py`、`app.py`（创建 FastAPI 实例，注册健康检查 `/healthz`）
  - [x] SubTask 4.3: 新建 `content_engine/api/routers/brief.py`，实现 `GET /api/v1/daily-brief?date=...` 与 `GET /api/v1/event/{id}` 与 `GET /api/v1/feed`
  - [x] SubTask 4.4: 新建 `content_engine/api/schemas.py`，定义响应 pydantic 模型（`EventCard` / `EventDetail` / `FeedPage`）
  - [x] SubTask 4.5: 加一个最小冒烟测试 `tests/test_api_smoke.py`（用 `TestClient` 调 `/healthz` 与 `/api/v1/daily-brief`，SQLite 内存 DB）

- [x] Task 5: 信源画像扩展（独立任务，可后置到 1.6 实操）
  - [x] SubTask 5.1: 在 `seed_sources.py` 中新增 6 个 `category` 常量与样例信源清单
  - [x] SubTask 5.2: 把现有 8 个信源按新画像归类，差额留待人工补到 ≥40

# Task Dependencies
- Task 4 depends on Task 2 与 Task 3（需要新 schema 字段才能正确返回）
- Task 5 与 Task 1-4 并行可做，但建议放最后实操阶段
- Task 1 仅文档调整，可独立先行
