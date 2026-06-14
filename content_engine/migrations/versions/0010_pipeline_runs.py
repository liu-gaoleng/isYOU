"""add pipeline_runs table for pipeline observability (stage D)

Revision ID: 0010_pipeline_runs
Revises: 0009_digest_singleton
Create Date: 2026-06-12

记录每次管线运行的逐阶段耗时/条数/成败与 LLM 成本，作为可观测性报表数据源。
"""

from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_pipeline_runs"
down_revision: Union[str, None] = "0009_digest_singleton"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("trigger", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("stages", sa.JSON(), nullable=False),
        sa.Column("llm_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=512), nullable=True),
    )
    op.create_index("ix_pipeline_runs_started_at", "pipeline_runs", ["started_at"])
    op.create_index("ix_pipeline_runs_status", "pipeline_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_status", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_started_at", table_name="pipeline_runs")
    op.drop_table("pipeline_runs")
