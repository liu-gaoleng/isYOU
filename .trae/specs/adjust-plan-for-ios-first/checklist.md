# Checklist

- [x] 「内容引擎实施计划.md」阶段 1 子项顺序更新为 1.1 → 1.2 → 1.3 → 1.4 摘要分级 → 1.5 FastAPI → 1.6 信源扩展
- [x] 计划文档阶段 1 顶部新增「目标用户：互联网/PM/投资人/创业者；首发端：iOS」
- [x] `events` 表已新增 `card_summary` 与 `detail_summary` 字段，旧 `summary` 列保留
- [x] Alembic 迁移 `0004_card_and_detail_summary.py` 已落地并能 `upgrade head` 通过
- [x] `summarize.py` 同时写入 `card_summary` 与 `detail_summary`，端到端跑一次后两列均非空
- [x] `users` 表已建/扩展，含 `apple_user_id` UNIQUE、`email`、`created_via` 字段
- [x] Alembic 迁移 `0005_users_apple_fields.py` 已落地并能 `upgrade head` 通过
- [x] `pyproject.toml` 依赖 `fastapi` 与 `uvicorn[standard]` 已添加
- [x] `uvicorn content_engine.api.app:app --reload` 可正常启动，`GET /healthz` 返回 200
- [x] `GET /api/v1/daily-brief`、`GET /api/v1/event/{id}`、`GET /api/v1/feed` 三个接口能返回正确 schema
- [x] `tests/test_api_smoke.py` 通过，pytest 全绿（22 旧 + 新增冒烟）
- [x] `seed_sources.py` 中六大画像 category 常量与样例清单已就位
