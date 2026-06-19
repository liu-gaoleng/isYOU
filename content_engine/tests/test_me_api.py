"""阶段 3.4 单测：收藏 / 阅读历史 / 推送设置（按登录用户 user_id）。

不依赖真实 PG：
- favorites / reading_history / push_settings 用真实 SQLite in-memory（无 pgvector）；
- Event 含 Vector/JSONB 列，SQLite 建不了 → monkeypatch me._visible_event 返回 stub；
- 鉴权走真实 JWT（HS256），仅 monkeypatch settings.auth + get_session。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.config import settings
from content_engine.models import Favorite, PushSetting, ReadingHistory, User
from content_engine.models.enums import EventStatus, Module
from content_engine.services import auth as auth_service

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "test-jwt-secret-please-rotate-0123456789"


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch):
    monkeypatch.setattr(settings.auth, "jwt_secret", SECRET)
    monkeypatch.setattr(settings.auth, "jwt_expire_minutes", 60)
    monkeypatch.setattr(settings.auth, "jwt_issuer", "redu-test")


def _stub_event(event_id: int, status=EventStatus.published):
    content = SimpleNamespace(version=1, title=f"事件{event_id}", sources=[], deep_content=None)
    return SimpleNamespace(
        id=event_id,
        module=Module.tech,
        status=status,
        card_summary="卡片",
        detail_summary="详情",
        tags=["标签"],
        importance=1.0,
        hotness=0.5,
        source_count=1,
        first_seen=NOW,
        last_update=NOW,
        contents=[content],
    )


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # 只建 user-domain 表（无 pgvector），外加 users
    for model in (User, Favorite, ReadingHistory, PushSetting):
        model.__table__.create(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)

    # 预置一个登录用户 id=1
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

    # 可见事件集合：测试可增删
    events: dict[int, object] = {i: _stub_event(i) for i in (101, 102, 103)}

    def fake_visible_event(_session, event_id: int):
        return events.get(event_id)

    from content_engine.api import deps as deps_mod
    from content_engine.api.routers import me as me_mod

    monkeypatch.setattr(me_mod, "get_session", fake_get_session)
    monkeypatch.setattr(deps_mod, "get_session", fake_get_session)
    monkeypatch.setattr(me_mod, "_visible_event", fake_visible_event)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._events = events
        token, _ = auth_service.issue_access_token(1)
        c.auth_headers = {"Authorization": f"Bearer {token}"}
        yield c


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
def test_favorites_requires_auth(client):
    assert client.get("/api/v1/me/favorites").status_code == 401
    assert client.get("/api/v1/me/history").status_code == 401
    assert client.get("/api/v1/me/settings").status_code == 401


# ---------------------------------------------------------------------------
# 收藏
# ---------------------------------------------------------------------------
def test_add_and_list_favorite(client):
    r = client.post("/api/v1/me/favorites/101", headers=client.auth_headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"event_id": 101, "is_favorited": True}

    r2 = client.get("/api/v1/me/favorites", headers=client.auth_headers)
    assert r2.status_code == 200
    items = r2.json()
    assert len(items) == 1
    assert items[0]["id"] == 101
    assert "favorited_at" in items[0]


def test_add_favorite_idempotent(client):
    client.post("/api/v1/me/favorites/101", headers=client.auth_headers)
    client.post("/api/v1/me/favorites/101", headers=client.auth_headers)
    items = client.get("/api/v1/me/favorites", headers=client.auth_headers).json()
    assert len(items) == 1  # 不重复


def test_remove_favorite(client):
    client.post("/api/v1/me/favorites/101", headers=client.auth_headers)
    r = client.delete("/api/v1/me/favorites/101", headers=client.auth_headers)
    assert r.status_code == 200
    assert r.json()["is_favorited"] is False
    assert client.get("/api/v1/me/favorites", headers=client.auth_headers).json() == []


def test_remove_favorite_idempotent(client):
    # 未收藏直接删，不报错
    r = client.delete("/api/v1/me/favorites/101", headers=client.auth_headers)
    assert r.status_code == 200
    assert r.json()["is_favorited"] is False


def test_favorite_unknown_event_404(client):
    r = client.post("/api/v1/me/favorites/999", headers=client.auth_headers)
    assert r.status_code == 404


def test_favorite_skips_invisible_event(client):
    client.post("/api/v1/me/favorites/101", headers=client.auth_headers)
    # 事件变为不可见 → 列表跳过
    client._events.pop(101)
    assert client.get("/api/v1/me/favorites", headers=client.auth_headers).json() == []


# ---------------------------------------------------------------------------
# 阅读历史
# ---------------------------------------------------------------------------
def test_record_and_list_history(client):
    r = client.post("/api/v1/me/history/101", headers=client.auth_headers)
    assert r.status_code == 204
    items = client.get("/api/v1/me/history", headers=client.auth_headers).json()
    assert len(items) == 1
    assert items[0]["id"] == 101
    assert "viewed_at" in items[0]


def test_history_dedup_keeps_single_row(client):
    client.post("/api/v1/me/history/101", headers=client.auth_headers)
    client.post("/api/v1/me/history/101", headers=client.auth_headers)
    items = client.get("/api/v1/me/history", headers=client.auth_headers).json()
    assert len(items) == 1


def test_history_recent_first(client):
    client.post("/api/v1/me/history/101", headers=client.auth_headers)
    client.post("/api/v1/me/history/102", headers=client.auth_headers)
    items = client.get("/api/v1/me/history", headers=client.auth_headers).json()
    # 最近浏览（102）在前
    assert [i["id"] for i in items][0] == 102


def test_clear_history(client):
    client.post("/api/v1/me/history/101", headers=client.auth_headers)
    r = client.delete("/api/v1/me/history", headers=client.auth_headers)
    assert r.status_code == 200
    assert r.json() == {"cleared": True}
    assert client.get("/api/v1/me/history", headers=client.auth_headers).json() == []


# ---------------------------------------------------------------------------
# 推送设置
# ---------------------------------------------------------------------------
def test_settings_default_when_unset(client):
    r = client.get("/api/v1/me/settings", headers=client.auth_headers)
    assert r.status_code == 200
    assert r.json() == {"daily_push": True, "push_time": "08:00", "breaking_push": False}


def test_update_settings_partial(client):
    r = client.put(
        "/api/v1/me/settings",
        headers=client.auth_headers,
        json={"breaking_push": True, "push_time": "07:30"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["breaking_push"] is True
    assert body["push_time"] == "07:30"
    assert body["daily_push"] is True  # 未传保持默认

    # 再次读取应持久化
    r2 = client.get("/api/v1/me/settings", headers=client.auth_headers)
    assert r2.json()["breaking_push"] is True
    assert r2.json()["push_time"] == "07:30"


def test_update_settings_only_changes_passed_fields(client):
    client.put("/api/v1/me/settings", headers=client.auth_headers, json={"daily_push": False})
    client.put("/api/v1/me/settings", headers=client.auth_headers, json={"push_time": "09:00"})
    body = client.get("/api/v1/me/settings", headers=client.auth_headers).json()
    assert body["daily_push"] is False  # 第一次的改动保留
    assert body["push_time"] == "09:00"
