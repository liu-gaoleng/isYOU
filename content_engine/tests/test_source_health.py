"""阶段 1.2 单测：信源健康记录的计数与告警逻辑。

使用 SQLite in-memory 隔离 DB（不依赖 PG / pgvector）：
- 仅测核心 ORM 字段（source_health 不依赖 vector）；
- 通过 monkeypatch 把 get_session 切到 in-memory engine。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_engine.models.base import Base
from content_engine.models.enums import Module, SourceLevel


@pytest.fixture
def session():
    """SQLite in-memory：仅建 sources / source_health 两张表，避开 pgvector 依赖。"""
    from content_engine.models.schema import Source, SourceHealth  # noqa: F401

    engine = create_engine("sqlite://", future=True)
    # 只建非 pgvector 表（其它表含 Vector 列在 SQLite 上不能 create_all）
    Source.__table__.create(engine)
    SourceHealth.__table__.create(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _make_source(s, name="Test"):
    from content_engine.models import Source

    src = Source(
        name=name, level=SourceLevel.A, type="rss",
        url="https://example.com/rss", module=Module.tech, weight=1.0, enabled=True,
    )
    s.add(src)
    s.commit()
    s.refresh(src)
    return src


def _record(s, source_id, status, *, fetched_at=None, consecutive=0):
    from content_engine.models import SourceHealth

    rec = SourceHealth(
        source_id=source_id,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        status=status,
        item_count=10 if status != "failed" else 0,
        inserted_count=5 if status != "failed" else 0,
        latency_ms=200,
        error=None if status != "failed" else "TimeoutError: read timed out",
        consecutive_failures=consecutive,
    )
    s.add(rec)
    s.commit()
    return rec


# ---------------------------------------------------------------------------
def test_consecutive_failures_accumulate_then_reset(session):
    """失败累加，成功归零；正是 collect.run() 决定是否告警的依据。"""
    from content_engine.models import SourceHealth
    from sqlalchemy import desc, select

    src = _make_source(session)

    base = datetime.now(timezone.utc) - timedelta(minutes=30)
    _record(session, src.id, "failed", fetched_at=base + timedelta(minutes=0), consecutive=1)
    _record(session, src.id, "failed", fetched_at=base + timedelta(minutes=10), consecutive=2)
    _record(session, src.id, "failed", fetched_at=base + timedelta(minutes=20), consecutive=3)

    last = session.execute(
        select(SourceHealth)
        .where(SourceHealth.source_id == src.id)
        .order_by(desc(SourceHealth.fetched_at)).limit(1)
    ).scalar_one()
    assert last.consecutive_failures == 3

    # 成功归零
    _record(session, src.id, "success", fetched_at=base + timedelta(minutes=30), consecutive=0)
    last = session.execute(
        select(SourceHealth)
        .where(SourceHealth.source_id == src.id)
        .order_by(desc(SourceHealth.fetched_at)).limit(1)
    ).scalar_one()
    assert last.consecutive_failures == 0
    assert last.status == "success"


def test_partial_status_resets_failures(session):
    """partial（解析成功但 0 新增）也视为可用，应归零失败计数。"""
    from content_engine.models import SourceHealth
    from sqlalchemy import desc, select

    src = _make_source(session)
    _record(session, src.id, "failed", consecutive=2)
    _record(session, src.id, "partial", consecutive=0)

    last = session.execute(
        select(SourceHealth).where(SourceHealth.source_id == src.id)
        .order_by(desc(SourceHealth.fetched_at)).limit(1)
    ).scalar_one()
    assert last.consecutive_failures == 0
    assert last.status == "partial"


def test_error_text_truncated(session):
    """错误文本上限 1024，避免堆栈撑爆 DB。"""
    from content_engine.models import SourceHealth

    src = _make_source(session)
    rec = SourceHealth(
        source_id=src.id, fetched_at=datetime.now(timezone.utc), status="failed",
        item_count=0, inserted_count=0, latency_ms=100,
        error=("X" * 2000)[:1024], consecutive_failures=1,
    )
    session.add(rec)
    session.commit()
    session.refresh(rec)
    assert len(rec.error) == 1024
