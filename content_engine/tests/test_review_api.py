"""阶段 4.2 单测：CMS 质检后台接口。

不依赖真实 PG：用假会话 + 内存假 Event/ReviewLog 模拟 DB 行为。
覆盖：
- require_admin：缺 token / 错 token → 401；正确 token → 通过；
- queue / approve / reject / edit / merge / split / pin 各动作；
- 留痕：每个写动作都产生一条 ReviewLog（log_id 回填）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from content_engine.config import settings
from content_engine.models import EventStatus, Module

TOKEN = "test-admin-token"
NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


class _Content:
    def __init__(self):
        self.version = 1
        self.title = "标题"
        self.why_matters = "说明"
        self.llm_meta = {"guard": {"violations": ["数字一致性存疑"]}}


class _Event:
    def __init__(self, event_id: int):
        self.id = event_id
        self.module = Module.finance
        self.status = EventStatus.reviewing
        self.card_summary = "卡片摘要"
        self.detail_summary = "详情摘要"
        self.importance = 1.0
        self.source_count = 2
        self.needs_split = False
        self.suggested_merge_id = None
        self.last_update = NOW
        self.contents = [_Content()]


class _FakeSession:
    def __init__(self, store):
        self.store = store

    def execute(self, *_args, **_kwargs):
        events = [v for v in self.store.values() if isinstance(v, _Event)]

        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_):
                        return events

                return _S()

        return _R()

    def get(self, _model, pk):
        return self.store.get(pk)

    def add(self, obj):
        self.store.setdefault("_logs", []).append(obj)

    def flush(self):
        for i, obj in enumerate(self.store.get("_logs", []), start=1):
            if getattr(obj, "id", None) is None:
                obj.id = i


@pytest.fixture
def client(monkeypatch):
    store = {1: _Event(1), 2: _Event(2)}

    @contextmanager
    def fake_get_session():
        yield _FakeSession(store)

    from content_engine.api.routers import review as review_mod

    monkeypatch.setattr(review_mod, "get_session", fake_get_session)
    monkeypatch.setattr(settings.admin, "token", TOKEN)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._store = store  # 暴露给断言
        yield c


def _h(token=TOKEN):
    return {"X-Admin-Token": token}


def test_queue_missing_token_401(client):
    assert client.get("/api/v1/admin/review/queue").status_code == 401


def test_queue_wrong_token_401(client):
    r = client.get("/api/v1/admin/review/queue", headers=_h("wrong"))
    assert r.status_code == 401


def test_queue_ok(client):
    r = client.get("/api/v1/admin/review/queue", headers=_h())
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["guard_violations"] == ["数字一致性存疑"]


def test_approve(client):
    r = client.post(
        "/api/v1/admin/review/1/approve", json={"reviewer": "alice"}, headers=_h()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == EventStatus.published.value
    assert body["action"] == "approve"
    assert body["log_id"] >= 1
    assert client._store[1].status == EventStatus.published


def test_reject(client):
    r = client.post(
        "/api/v1/admin/review/1/reject", json={"reviewer": "bob"}, headers=_h()
    )
    assert r.status_code == 200
    assert r.json()["status"] == EventStatus.rejected.value


def test_edit_updates_summary_not_status(client):
    r = client.post(
        "/api/v1/admin/review/1/edit",
        json={"reviewer": "alice", "card_summary": "新卡片", "detail_summary": "新详情"},
        headers=_h(),
    )
    assert r.status_code == 200
    assert client._store[1].card_summary == "新卡片"
    assert client._store[1].status == EventStatus.reviewing


def test_merge_requires_target(client):
    r = client.post(
        "/api/v1/admin/review/1/merge", json={"reviewer": "alice"}, headers=_h()
    )
    assert r.status_code == 400


def test_merge_ok(client):
    r = client.post(
        "/api/v1/admin/review/1/merge",
        json={"reviewer": "alice", "target_event_id": 2},
        headers=_h(),
    )
    assert r.status_code == 200
    assert client._store[1].status == EventStatus.rejected


def test_split_marks_needs_split(client):
    r = client.post(
        "/api/v1/admin/review/1/split", json={"reviewer": "alice"}, headers=_h()
    )
    assert r.status_code == 200
    assert client._store[1].needs_split is True


def test_pin_sets_importance(client):
    r = client.post(
        "/api/v1/admin/review/1/pin", json={"reviewer": "alice"}, headers=_h()
    )
    assert r.status_code == 200
    assert client._store[1].importance == 100.0


def test_action_event_not_found(client):
    r = client.post(
        "/api/v1/admin/review/999/approve", json={"reviewer": "alice"}, headers=_h()
    )
    assert r.status_code == 404


def test_write_action_records_log(client):
    client.post("/api/v1/admin/review/1/pin", json={"reviewer": "alice"}, headers=_h())
    logs = client._store.get("_logs", [])
    assert len(logs) == 1
    assert logs[0].action == "pin"
    assert logs[0].reviewer == "alice"
