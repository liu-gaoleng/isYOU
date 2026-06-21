"""阶段 4.2 单测：APNs daily dispatcher（tasks/push_tasks）。

不依赖真实 PG / APNs：
- ORM 用 SQLite in-memory，仅建 user-domain 表（User / PushSetting / DeviceToken / PushRecord）；
- Event 表含 pgvector → 不在 SQLite 里建，改 monkeypatch ``_today_top_event``
  返回 stub，绕开真实 events 查询；
- APNs 客户端被 monkeypatch 成 fake，记录 send 调用并按预设抛错。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.models import (
    DeviceToken,
    PushRecord,
    PushSetting,
    User,
)
from content_engine.services.apns import ApnsBadTokenError, ApnsConfigError, ApnsError
from content_engine.tasks import push_tasks


NOW_0800 = datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc)
NOW_0901 = datetime(2026, 6, 21, 9, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (User, PushSetting, DeviceToken, PushRecord):
        model.__table__.create(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)

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

    return SessionLocal, fake_get_session


@pytest.fixture
def patched(monkeypatch, session_factory):
    """注入 fake get_session + stub _today_top_event + fake ApnsClient.from_settings。"""
    SessionLocal, fake_get_session = session_factory

    # 1) 用 fake session 替代真实 PG
    monkeypatch.setattr(push_tasks, "get_session", fake_get_session)

    # 2) stub 当日 top 事件（绕开 Event 表）
    stub_event = SimpleNamespace(id=4242)
    monkeypatch.setattr(
        push_tasks, "_today_top_event", lambda s, now: (stub_event, 3)
    )

    return SessionLocal


def _seed(SessionLocal, *, user_id: int, push_time: str, tokens: list[str] | None = None):
    with SessionLocal() as s:
        s.add(User(id=user_id, apple_user_id=f"sub-{user_id}", created_via="test"))
        s.add(PushSetting(user_id=user_id, daily_push=True, push_time=push_time))
        for t in tokens or []:
            s.add(DeviceToken(user_id=user_id, token=t, environment="production"))
        s.commit()


class FakeApnsClient:
    """记录 send 调用并按预设抛错的假客户端。"""

    def __init__(self, *, fail_token: str | None = None, error: Exception | None = None):
        self.sent: list[str] = []
        self._fail_token = fail_token
        self._error = error
        self.closed = False

    def send(self, *, token: str, payload, collapse_id=None):
        if self._fail_token and token == self._fail_token:
            assert self._error is not None
            raise self._error
        self.sent.append(token)

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# 时间匹配
# ---------------------------------------------------------------------------
def test_dispatches_at_matching_hhmm(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    fake = FakeApnsClient()
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["matched_users"] == 1
    assert summary["sent"] == 1
    assert summary["configured"] is True
    assert fake.sent == ["tok-A"]
    assert fake.closed is True


def test_skips_unmatched_hhmm(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    fake = FakeApnsClient()
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0901)
    assert summary["matched_users"] == 0
    assert summary["sent"] == 0
    assert fake.sent == []


def test_skips_users_with_daily_push_off(patched, monkeypatch):
    SessionLocal = patched
    with SessionLocal() as s:
        s.add(User(id=2, apple_user_id="sub-2", created_via="test"))
        s.add(PushSetting(user_id=2, daily_push=False, push_time="08:00"))
        s.add(DeviceToken(user_id=2, token="tok-off", environment="production"))
        s.commit()

    fake = FakeApnsClient()
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )
    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["matched_users"] == 0
    assert fake.sent == []


# ---------------------------------------------------------------------------
# token 失效软删 + 其它错误吞掉
# ---------------------------------------------------------------------------
def test_bad_token_marked_invalid(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-bad", "tok-ok"])

    fake = FakeApnsClient(fail_token="tok-bad", error=ApnsBadTokenError(410, "Unregistered"))
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["sent"] == 1
    assert summary["invalidated"] == 1
    with SessionLocal() as s:
        bad = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-bad")).scalar_one()
        ok = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-ok")).scalar_one()
    assert bad.invalid_at is not None
    assert ok.invalid_at is None


def test_other_apns_error_is_swallowed(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-bad", "tok-ok"])

    fake = FakeApnsClient(fail_token="tok-bad", error=ApnsError(503, "ServiceUnavailable"))
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["sent"] == 1  # 仅 tok-ok 成功
    assert summary["invalidated"] == 0
    with SessionLocal() as s:
        bad = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-bad")).scalar_one()
    assert bad.invalid_at is None  # 非 token 失效不软删


# ---------------------------------------------------------------------------
# 干运行（凭据未配置）
# ---------------------------------------------------------------------------
def test_dry_run_when_apns_unconfigured(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    def raise_unconfigured(*a, **kw):
        raise ApnsConfigError("not configured")

    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: raise_unconfigured())
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["configured"] is False
    assert summary["sent"] == 0
    assert summary["matched_users"] == 1
    # PushRecord 仍记账
    with SessionLocal() as s:
        rec = s.execute(select(PushRecord)).scalar_one()
        assert rec.type == "daily"
        assert rec.sent == 0


# ---------------------------------------------------------------------------
# 幂等：同 biz_id 重跑直接返回
# ---------------------------------------------------------------------------
def test_idempotent_on_same_minute(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    fake = FakeApnsClient()
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    first = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    second = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert first["sent"] == 1
    assert second.get("already_done") is True
    assert second["sent"] == 1  # 复用首跑数据
    assert len(fake.sent) == 1  # 第二次没再 send


# ---------------------------------------------------------------------------
# 当日无可见事件 → 跳过但记账
# ---------------------------------------------------------------------------
def test_skips_when_no_brief(patched, monkeypatch):
    SessionLocal = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    monkeypatch.setattr(push_tasks, "_today_top_event", lambda s, now: (None, 0))
    fake = FakeApnsClient()
    monkeypatch.setattr(
        push_tasks.ApnsClient, "from_settings", classmethod(lambda cls, *a, **kw: fake)
    )

    summary = push_tasks.dispatch_daily_briefs(now=NOW_0800)
    assert summary["skipped_no_brief"] is True
    assert summary["sent"] == 0
    assert fake.sent == []
    with SessionLocal() as s:
        rec = s.execute(select(PushRecord)).scalar_one()
        assert rec.sent == 0
        assert rec.event_ids == []
