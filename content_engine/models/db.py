"""SQLAlchemy engine & session 工厂。

`get_session()` 提供上下文管理器，业务代码只需：
    with get_session() as s:
        s.add(obj); s.commit()
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from content_engine.config import settings


def _build_engine() -> Engine:
    # echo=False；如需排障可临时 echo=True
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def get_session() -> Iterator[Session]:
    """业务侧统一会话上下文：自动 commit / rollback / close。"""
    session = _get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
