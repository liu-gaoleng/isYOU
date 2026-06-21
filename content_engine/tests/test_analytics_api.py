"""阶段 4.3 单测：自建埋点上报端点 /api/v1/analytics/events。

- 用 SQLite in-memory 建 ``analytics_events`` + ``users`` 两张表，覆盖匿名 / 登录两种入库；
- 监 patch ``analytics.get_session`` 与 ``deps.get_session`` 指向同一个内存 SessionLocal。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.config import settings
from content_engine.models import AnalyticsEvent, User
from content_engine.services import auth as auth_service

SECRET = "test-jwt-secret-please-rotate-0123456789"


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch):
    monkeypatch.setattr(settings.auth, "jwt_secret", SECRET)
    monkeypatch.setattr(settings.auth, "jwt_expire_minutes", 60)
    monkeypatch.setattr(settings.auth, "jwt_issuer", "redu-test")


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (User, AnalyticsEvent):
        model.__table__.create(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)

    with SessionLocal() as s:
        s.add(User(id=1, apple_user_id="sub-1", created_via="test"))
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

    from content_engine.api import deps as deps_mod
    from content_engine.api.routers import analytics as analytics_mod

    monkeypatch.setattr(analytics_mod, "get_session", fake_get_session)
    monkeypatch.setattr(deps_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._SessionLocal = SessionLocal
        token, _ = auth_service.issue_access_token(1)
        c.auth_headers = {"Authorization": f"Bearer {token}"}
        yield c


def _event(name="app_open", device_id="dev-uuid-1", **extra):
    base = {
        "name": name,
        "device_id": device_id,
        "app_version": "0.1.0",
        "os_version": "17.4",
        "platform": "ios",
        "ts_client": 1_700_000_000_000,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def test_anonymous_batch_persisted(client):
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [_event(), _event(name="event_view", props={"event_id": 123})]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"accepted": 2}

    with client._SessionLocal() as s:
        rows = s.execute(select(AnalyticsEvent).order_by(AnalyticsEvent.id)).scalars().all()
        assert [r.name for r in rows] == ["app_open", "event_view"]
        assert rows[0].user_id is None
        assert rows[1].props == {"event_id": 123}


def test_authenticated_batch_attaches_user_id(client):
    r = client.post(
        "/api/v1/analytics/events",
        headers=client.auth_headers,
        json={"events": [_event(name="purchase_success", props={"plan": "monthly"})]},
    )
    assert r.status_code == 200
    assert r.json() == {"accepted": 1}

    with client._SessionLocal() as s:
        row = s.execute(select(AnalyticsEvent)).scalar_one()
        assert row.user_id == 1
        assert row.name == "purchase_success"
        assert row.props == {"plan": "monthly"}


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def test_rejects_unknown_event_name(client):
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [_event(name="hack_event")]},
    )
    assert r.status_code == 422
    assert "hack_event" in r.text

    with client._SessionLocal() as s:
        assert s.execute(select(AnalyticsEvent)).first() is None  # 整批拒收


def test_rejects_empty_batch(client):
    r = client.post("/api/v1/analytics/events", json={"events": []})
    assert r.status_code == 422


def test_rejects_oversized_batch(client):
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [_event() for _ in range(101)]},
    )
    assert r.status_code == 422


def test_rejects_bad_platform(client):
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [_event(platform="symbian")]},
    )
    assert r.status_code == 422


def test_rejects_missing_device_id(client):
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "app_open", "device_id": ""}]},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------
def test_default_optional_fields(client):
    # 只传必填项：name + device_id；其余走默认。
    r = client.post(
        "/api/v1/analytics/events",
        json={"events": [{"name": "share", "device_id": "dev-x"}]},
    )
    assert r.status_code == 200
    with client._SessionLocal() as s:
        row = s.execute(select(AnalyticsEvent)).scalar_one()
        assert row.platform == "ios"
        assert row.ts_client == 0
        assert row.app_version == ""
        assert row.props is None
