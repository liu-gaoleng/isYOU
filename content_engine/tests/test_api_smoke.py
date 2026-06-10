"""阶段 1.5 FastAPI 冒烟测试。

只覆盖：
1. `/healthz` 进程存活返回 ok（不走 DB）；
2. `/api/v1/daily-brief` 路由注册成功（用 monkeypatch 把 get_session 替换为空会话，断言 200 + 空数组）；
3. `/api/v1/event/{id}` 不存在时返回 404。

完整 DB 行为依赖 pgvector，留给 PG 端真实跑 collect→summarize 后人工验证。
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """构造一个 FastAPI TestClient，并把 brief router 内的 get_session 替换为
    返回空结果集的假会话，避免依赖真实 PG/pgvector。"""

    class _FakeSession:
        def execute(self, *_args, **_kwargs):
            class _R:
                def scalars(self_inner):
                    class _S:
                        def all(self_):
                            return []
                    return _S()
            return _R()

        def get(self, *_args, **_kwargs):
            return None

    @contextmanager
    def fake_get_session():
        yield _FakeSession()

    # 替换 brief 模块内引用的 get_session
    from content_engine.api.routers import brief as brief_mod

    monkeypatch.setattr(brief_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        yield c


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_daily_brief_empty(client):
    r = client.get("/api/v1/daily-brief")
    assert r.status_code == 200
    assert r.json() == []


def test_daily_brief_invalid_date(client):
    r = client.get("/api/v1/daily-brief?date=not-a-date")
    assert r.status_code == 400


def test_event_detail_not_found(client):
    r = client.get("/api/v1/event/999999")
    assert r.status_code == 404


def test_feed_empty(client):
    r = client.get("/api/v1/feed?limit=20")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["next_cursor"] is None
