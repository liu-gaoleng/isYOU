# 调整阶段 1 实施计划以匹配 iOS-first 产品定位

## Why
产品目标用户为互联网从业者、产品经理、投资人、创业者，iPhone 占比高，故首发端定为 iOS。当前 [内容引擎实施计划.md](file:///Users/bytedance/liu/isYOU/内容引擎实施计划.md) 是「服务端 CLI + 通用四赛道」视角，需要调整四处以避免后期返工：信源画像、API 层、摘要分级、用户表 Apple 字段。

## What Changes
- **MODIFIED 信源画像**：阶段 1.4「信源扩展至 ≥40」从「科技/金融/AI/宏观」调整为「互联网产品 / 大厂战略 / 一级市场融资 / AI 应用层 / 出海 / 创业者实操」六大画像
- **ADDED 阶段 1.5 FastAPI 服务层**：在阶段 1.3 之后、阶段 1.4 之前，新增 HTTP API 模块 `content_engine/api/`，给后续 iOS 客户端消费数据
- **ADDED 摘要分级**：摘要阶段同时产出 `card_summary` (≤120 字) 与 `detail_summary` (300–500 字)，schema 与 `summarize.py` 同步改造
- **ADDED 用户表 Apple 字段**：新增 `users` 表（如未有）或在已有用户表加 `apple_user_id` / `email` / `created_via` 字段，落 0004 迁移
- **不破坏既有内容管线**：collect/clean/classify/cluster/score 不动；仅 summarize 的输出 schema 扩展

## Impact
- Affected specs: 阶段 1（[内容引擎实施计划.md](file:///Users/bytedance/liu/isYOU/内容引擎实施计划.md) 阶段 1 章节）
- Affected code:
  - [seed_sources.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/seed_sources.py) — 信源 seed 重做
  - [summarize.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/summarize.py) — 输出双摘要
  - [schema.py](file:///Users/bytedance/liu/isYOU/content_engine/models/schema.py) — `events` 表加 `card_summary`/`detail_summary`，新增/扩展 `users` 表
  - 新增 `content_engine/api/` 模块（FastAPI app + routers + schemas）
  - 新增 Alembic 迁移 `0004_card_summary_and_users.py`
  - 新增 `pyproject.toml` 依赖：`fastapi`、`uvicorn[standard]`

## ADDED Requirements

### Requirement: 摘要分级输出
The system SHALL 在事件摘要阶段同时产出短卡片摘要与长详情摘要，便于 iOS 卡片流与详情页消费同一份数据。

#### Scenario: 一篇事件同时具备双摘要
- **WHEN** `summarize.run()` 处理一个 `clustered` 事件
- **THEN** 写入 `events.card_summary`（≤120 中文字符）与 `events.detail_summary`（300–500 中文字符），状态置为 `summarized`

### Requirement: HTTP API 层
The system SHALL 提供 FastAPI HTTP 服务，对外暴露每日简报、事件详情、信息流三个核心只读接口。

#### Scenario: 拉取当日简报
- **WHEN** iOS 客户端 GET `/api/v1/daily-brief?date=YYYY-MM-DD`
- **THEN** 返回当日 `published` 状态事件按 score 降序的 JSON 列表，每条含 `id`、`title`、`card_summary`、`score`、`tags`

#### Scenario: 拉取事件详情
- **WHEN** iOS 客户端 GET `/api/v1/event/{id}`
- **THEN** 返回该事件的 `detail_summary`、信源列表、原文链接、相关事件等完整字段

#### Scenario: 信息流分页
- **WHEN** iOS 客户端 GET `/api/v1/feed?cursor=...&limit=20&category=...`
- **THEN** 返回按时间倒序的事件分页结果，含 `next_cursor`

### Requirement: 用户表预留 Apple 登录
The system SHALL 在用户表中预留 Sign in with Apple 所需字段，避免后续登录上线时迁移用户体系。

#### Scenario: 通过 Apple 登录创建用户
- **WHEN** iOS 客户端走 Sign in with Apple 首次登录
- **THEN** 后端按 `apple_user_id` 唯一定位用户，并写入 `email`（可空）、`created_via='apple'`

### Requirement: 信源画像重做
The system SHALL 在阶段 1.4 信源扩展时，按互联网从业者人群画像选取 ≥40 个信源，覆盖六大子画像。

#### Scenario: 信源覆盖六大子画像
- **WHEN** `seed_sources.run()` 完成
- **THEN** `sources` 表中 `enabled=true` 行数 ≥40，且每个子画像（互联网产品 / 大厂战略 / 一级市场融资 / AI 应用层 / 出海 / 创业者实操）至少 5 个信源

## MODIFIED Requirements

### Requirement: 阶段 1 顺序
阶段 1 子项顺序调整为：1.1 SimHash（已完成）→ 1.2 健康监控（已完成）→ 1.3 Embedding 接入 → **1.4 摘要分级（新插入）** → **1.5 FastAPI 层（新插入）** → 1.6 信源扩展至 ≥40（原 1.4）。

## REMOVED Requirements
（无）
