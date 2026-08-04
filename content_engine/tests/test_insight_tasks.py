"""热点透视生成任务单测（tasks/insight_tasks.py）。

不依赖真实 PG / LLM：
- SQLite in-memory 仅建 event_analyses 表；
- ``_load_event`` / ``load_materials`` / ``get_llm_client`` 全部 monkeypatch
  （Event 含 pgvector 列不能建 SQLite 表；LLM 用 fake 记录调用）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.config import settings
from content_engine.models import EventAnalysis
from content_engine.services.llm_client import LLMError, LLMResponse
from content_engine.tasks import insight_tasks
from content_engine.tasks.insight_tasks import generate_insight

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)

GOOD_SECTIONS = {"history": "史" * 80, "current": "现" * 100, "forecast": "预" * 60}


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EventAnalysis.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)


class FakeLLM:
    """记录调用次数，按编排返回/抛错。"""

    def __init__(self, *, payload: dict | None = None, error: Exception | None = None):
        self.calls = 0
        self._payload = payload if payload is not None else GOOD_SECTIONS
        self._error = error

    def chat_json(self, prompt: str, temperature: float = 0.0) -> LLMResponse:
        self.calls += 1
        if self._error is not None:
            raise self._error
        import json

        return LLMResponse(
            content=json.dumps(self._payload, ensure_ascii=False),
            model="deepseek-chat",
            usage={"prompt_tokens": 1000, "completion_tokens": 400},
            cost=0.002,
        )


@pytest.fixture
def patched(monkeypatch, session_factory):
    """注入 fake get_session / 事件与素材 stub / fake LLM；返回 (SessionLocal, llm)。"""

    @contextmanager
    def fake_get_session():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    llm = FakeLLM()
    monkeypatch.setattr(insight_tasks, "get_session", fake_get_session)
    monkeypatch.setattr(
        insight_tasks, "_load_event", lambda s, eid: SimpleNamespace(id=eid)
    )
    monkeypatch.setattr(
        insight_tasks,
        "load_materials",
        lambda s, ev, now: {
            "title": "t", "detail_summary": "d", "why_matters": "w",
            "facts": [], "sources": [], "related": [],
            "related_ids": [11, 12], "fingerprint": "fp-1",
        },
    )
    monkeypatch.setattr(insight_tasks, "get_llm_client", lambda: llm)
    monkeypatch.setattr(settings.llm, "api_key", "sk-test")
    return session_factory, llm


def _seed(session_factory, status="pending", event_id=7):
    with session_factory() as s:
        s.add(EventAnalysis(event_id=event_id, status=status))
        s.commit()


def _row(session_factory, event_id=7) -> EventAnalysis | None:
    with session_factory() as s:
        return s.execute(
            select(EventAnalysis).where(EventAnalysis.event_id == event_id)
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
def test_success_full_lifecycle(patched):
    SessionLocal, llm = patched
    _seed(SessionLocal)
    out = generate_insight(7, now=NOW)
    assert out["status"] == "ready"
    assert llm.calls == 1

    row = _row(SessionLocal)
    assert row.status == "ready"
    assert row.sections == GOOD_SECTIONS
    assert row.related_event_ids == [11, 12]
    assert row.generated_at is not None
    meta = row.llm_meta
    assert meta["model"] == "deepseek-chat"
    assert meta["usage"]["prompt_tokens"] == 1000
    assert meta["prompt_version"] == "v1"
    assert meta["fingerprint"] == "fp-1"


def test_ready_row_short_circuits_no_llm(patched):
    """acks_late 重投防线：行已 ready → 任务直接返回，不重调 LLM。"""
    SessionLocal, llm = patched
    _seed(SessionLocal, status="ready")
    out = generate_insight(7, now=NOW)
    assert out["status"] == "skipped"
    assert llm.calls == 0


def test_llm_error_marks_failed(patched, monkeypatch, session_factory):
    SessionLocal, _ = patched
    monkeypatch.setattr(
        insight_tasks, "get_llm_client",
        lambda: FakeLLM(error=LLMError("boom")),
    )
    _seed(SessionLocal)
    out = generate_insight(7, now=NOW)
    assert out["status"] == "failed"
    row = _row(SessionLocal)
    assert row.status == "failed"
    assert row.error == "boom"
    assert len(row.error) <= 512


def test_llm_not_configured_marks_failed(patched, monkeypatch, session_factory):
    SessionLocal, _ = patched
    monkeypatch.setattr(settings.llm, "api_key", "")
    _seed(SessionLocal)
    out = generate_insight(7, now=NOW)
    assert out["status"] == "failed"
    assert "未配置" in _row(SessionLocal).error


def test_invalid_json_marks_failed(patched, monkeypatch, session_factory):
    SessionLocal, _ = patched

    class BadJSON:
        def chat_json(self, prompt, temperature=0.0):
            return LLMResponse("不是json", "m", None, 0.0)

    monkeypatch.setattr(insight_tasks, "get_llm_client", lambda: BadJSON())
    _seed(SessionLocal)
    out = generate_insight(7, now=NOW)
    assert out["status"] == "failed"


def test_event_deleted_midway_skips(patched, monkeypatch, session_factory):
    SessionLocal, llm = patched
    monkeypatch.setattr(insight_tasks, "_load_event", lambda s, eid: None)
    _seed(SessionLocal)
    out = generate_insight(7, now=NOW)
    assert out["status"] == "skipped"
    assert llm.calls == 0


def test_complete_on_deleted_row_is_noop(patched, session_factory):
    """生成期间行被删：条件更新 rowcount=0 → persisted=False，不抛错。"""
    SessionLocal, _llm = patched
    _seed(SessionLocal)

    original_complete = insight_tasks.complete_generation

    def deleting_complete(session, event_id, **kwargs):
        # 模拟行在生成期间被删（PG 端 FK CASCADE）
        session.execute(
            EventAnalysis.__table__.delete().where(EventAnalysis.event_id == event_id)
        )
        return original_complete(session, event_id, **kwargs)

    import content_engine.tasks.insight_tasks as m
    m.complete_generation = deleting_complete
    try:
        out = generate_insight(7, now=NOW)
    finally:
        m.complete_generation = original_complete
    assert out["status"] == "ready"
    assert out["persisted"] is False
    assert _row(SessionLocal) is None
