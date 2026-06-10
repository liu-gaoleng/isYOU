-- 「热读」DB 初始化脚本：仅启用 pgvector 扩展
-- 完整表结构由 Alembic 迁移管理（见 content_engine/migrations/versions/）

CREATE EXTENSION IF NOT EXISTS vector;
