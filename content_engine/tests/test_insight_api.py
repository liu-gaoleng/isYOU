"""热点透视端点单测（api/routers/insight.py）。

- SQLite in-memory 建 users + event_analyses 两张表；
- ``_get_visible_event`` monkeypatch 成内存集合（Event 含 pgvector 列不能建 SQLite 表）；
- ``_enqueue`` monkeypatch 成调用记录器（不发真实 Celery 任务）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.api.routers import insight as insight_mod
from content_engine.config import settings
from content_engine.models import EventAnalysis, User
from content_engine.services import auth as auth_service
from content_engine.services.insight import STALE_SECONDS

SECRET = "test-jwt-secret-please-rotate-0123456789"
NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)

# 可见事件集合：7=published；8 存在但未发布；9 不存在
VISIBLE_IDS = {7}
HIDDEN_IDS = {8}


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch):
    monkeypatch.setattr(settings.auth, "jwt_secret", SECRET)
    monkeypatch.setattr(settings.auth, "jwt_expire_minutes", 60)
    monkeypatch.setattr(settings.auth, "jwt_issuer", "redu-test")
    monkeypatch.setattr(settings.llm, "api_key", "sk-test")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (User, EventAnalysis):
        model.__table__.create(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)

    with SessionLocal() as s:
        s.add(User(id=1, apple_user_id="sub-free", created_via="test"))
        s.add(
            User(
                id=2,
                apple_user_id="sub-member",
                created_via="test",
                member_tier="member",
                member_expire_at=NOW + timedelta(days=30),
            )
        )
        s.commit()

    @contextmanager
    def fake_get_session():
        s = SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def fake_visible(session, event_id):
        if event_id in VISIBLE_IDS:
            return SimpleNamespace(id=event_id)
        raise HTTPException(status_code=404, detail="event not found")

    enqueued: list[int] = []

    from content_engine.api import deps as deps_mod

    monkeypatch.setattr(deps_mod, "get_session", fake_get_session)
    monkeypatch.setattr(insight_mod, "get_session", fake_get_session)
    monkeypatch.setattr(insight_mod, "_get_visible_event", fake_visible)
    monkeypatch.setattr(insight_mod, "_enqueue", enqueued.append)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._SessionLocal = SessionLocal
        c._enqueued = enqueued
        member_token, _ = auth_service.issue_access_token(2)
        free_token, _ = auth_service.issue_access_token(1)
        c.member_headers = {"Authorization": f"Bearer {member_token}"}
        c.free_headers = {"Authorization": f"Bearer {free_token}"}
        yield c


def _seed_analysis(client, status: str, **kw):
    with client._SessionLocal() as s:
        s.add(EventAnalysis(event_id=7, status=status, **kw))
        s.commit()


def _analysis_rows(client):
    with client._SessionLocal() as s:
        return s.execute(select(EventAnalysis)).scalars().all()


# ---------------------------------------------------------------------------
# 门禁
# ---------------------------------------------------------------------------
def test_post_no_token_401(client):
    assert client.post("/api/v1/event/7/insight").status_code == 401


def test_post_non_member_403(client):
    r = client.post("/api/v1/event/7/insight", headers=client.free_headers)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "member_required"


def test_post_event_not_found_404(client):
    assert client.post("/api/v1/event/9/insight", headers=client.member_headers).status_code == 404


def test_post_event_not_published_404(client):
    assert client.post("/api/v1/event/8/insight", headers=client.member_headers).status_code == 404


# ---------------------------------------------------------------------------
# POST 触发与幂等
# ---------------------------------------------------------------------------
def test_post_first_trigger_enqueues(client):
    r = client.post("/api/v1/event/7/insight", headers=client.member_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending"
    assert client._enqueued == [7]
    assert len(_analysis_rows(client)) == 1


def test_post_inflight_not_enqueued_again(client):
    _seed_analysis(client, "pending")
    r = client.post("/api/v1/event/7/insight", headers=client.member_headers)
    assert r.json()["status"] == "pending"
    assert client._enqueued == []
    assert len(_analysis_rows(client)) == 1


def test_post_ready_returns_sections_without_enqueue(client):
    _seed_analysis(
        client,
        "ready",
        sections={"history": "史", "current": "现", "forecast": "预"},
        generated_at=NOW,
    )
    r = client.post("/api/v1/event/7/insight", headers=client.member_headers)
    body = r.json()
    assert body["status"] == "ready"
    assert body["sections"] == {"history": "史", "current": "现", "forecast": "预"}
    assert body["disclaimer"]
    assert body["generated_at"]
    assert client._enqueued == []


def test_post_failed_requeues(client):
    _seed_analysis(client, "failed", error="boom")
    r = client.post("/api/v1/event/7/insight", headers=client.member_headers)
    assert r.json()["status"] == "pending"
    assert client._enqueued == [7]


def test_post_stale_inflight_requeues(client):
    stale_at = datetime.now(timezone.utc) - timedelta(seconds=STALE_SECONDS + 60)
    _seed_analysis(client, "generating", updated_at=stale_at)
    client.post("/api/v1/event/7/insight", headers=client.member_headers)
    assert client._enqueued == [7]


def test_post_llm_disabled_503_and_rollback(client, monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "")
    r = client.post("/api/v1/event/7/insight", headers=client.member_headers)
    assert r.status_code == 503
    assert _analysis_rows(client) == []  # claim 建的行被回滚


# ---------------------------------------------------------------------------
# GET 查询
# ---------------------------------------------------------------------------
def test_get_none_when_no_row(client):
    r = client.get("/api/v1/event/7/insight", headers=client.member_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "none"
    assert r.json()["sections"] is None


def test_get_non_member_403(client):
    assert client.get("/api/v1/event/7/insight", headers=client.free_headers).status_code == 403


def test_get_ready_full_fields(client):
    _seed_analysis(
        client,
        "ready",
        sections={"history": "史", "current": "现", "forecast": "预"},
        generated_at=NOW,
    )
    body = client.get("/api/v1/event/7/insight", headers=client.member_headers).json()
    assert body["status"] == "ready"
    assert body["sections"]["forecast"] == "预"
    assert "投资建议" in body["disclaimer"]
    assert body["generated_at"] is not None
