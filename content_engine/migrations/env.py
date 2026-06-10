"""Alembic 迁移环境。

要点：
- DATABASE_URL 从环境变量读取（与 content_engine.models.db 保持一致），不写死在 ini；
- target_metadata 指向 ORM 的 Base.metadata，支持 --autogenerate 基于模型 diff 生成迁移；
- 注册 pgvector 类型，autogenerate 才能识别 Vector 列；
- include_object 跳过 pgvector 扩展自带对象，避免误生成 DROP。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# 把仓库根目录加入 sys.path，使 `import content_engine` 在 alembic 进程里可用
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from content_engine.models import Base  # noqa: E402  (after sys.path tweak)
from content_engine.models import schema as _schema  # noqa: E402,F401  确保所有表被注册

config = context.config

# 优先用环境变量 DATABASE_URL；否则保留 ini 中的占位
db_url = os.getenv("DATABASE_URL")
if db_url:
    config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """过滤 autogenerate 不应处理的对象（如 pgvector 扩展自带类型）。"""
    if type_ == "table" and name in {"vector"}:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
