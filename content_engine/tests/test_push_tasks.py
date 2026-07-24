"""阶段 4.2 单测：APNs daily dispatcher（tasks/push_tasks）。

不依赖真实 PG / APNs：
- ORM 用 SQLite in-memory，仅建 user-domain 表（User / PushSetting / DeviceToken / PushRecord）；
- Event 表含 pgvector → 不在 SQLite 里建，改 monkeypatch ``_today_top_event``
  返回 stub，绕开真实 events 查询；
- APNs 客户端池被 monkeypatch 成 fake，按 environment 记录 send 调用并按预设抛错。

时区语义（冒烟修复 #3）：
- ``push_time`` 是「用户 tz 时区下的本地 HH:MM」；tz 为 NULL 按 Asia/Shanghai 兜底；
- 故 push_time="08:00"（上海）在 UTC 00:00 命中，而非 UTC 08:00；
- 简报窗口是该时区本地自然日（上海 08:00 派发时窗口起点是 UTC 前一日 16:00）。
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


# 上海 08:00 = UTC 00:00；上海 09:01 = UTC 01:01
NOW_SH_0800 = datetime(2026, 6, 21, 0, 0, 0, tzinfo=timezone.utc)
NOW_SH_0901 = datetime(2026, 6, 21, 1, 1, 0, tzinfo=timezone.utc)
NOW_UTC_0800 = datetime(2026, 6, 21, 8, 0, 0, tzinfo=timezone.utc)


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
    """注入 fake get_session + stub _today_top_event（记录窗口参数）。"""
    SessionLocal, fake_get_session = session_factory

    # 1) 用 fake session 替代真实 PG
    monkeypatch.setattr(push_tasks, "get_session", fake_get_session)

    # 2) stub 窗口 top 事件（绕开 Event 表），并记录传入窗口供断言
    stub_event = SimpleNamespace(id=4242)
    windows: list[tuple[datetime, datetime]] = []

    def fake_top(session, window_start, window_end):
        windows.append((window_start, window_end))
        return stub_event, 3

    monkeypatch.setattr(push_tasks, "_today_top_event", fake_top)

    return SessionLocal, windows


def _seed(
    SessionLocal,
    *,
    user_id: int,
    push_time: str,
    tz: str | None = None,
    tokens: list[str] | None = None,
    token_env: str = "production",
):
    with SessionLocal() as s:
        s.add(User(id=user_id, apple_user_id=f"sub-{user_id}", created_via="test"))
        s.add(PushSetting(user_id=user_id, daily_push=True, push_time=push_time, tz=tz))
        for t in tokens or []:
            s.add(DeviceToken(user_id=user_id, token=t, environment=token_env))
        s.commit()


class FakeApnsClient:
    """记录 send 调用并按预设抛错的假客户端。"""

    def __init__(self, *, fail_token: str | None = None, error: Exception | None = None):
        self.sent: list[str] = []
        self._fail_token = fail_token
        self._error = error

    def send(self, *, token: str, payload, collapse_id=None):
        if self._fail_token and token == self._fail_token:
            assert self._error is not None
            raise self._error
        self.sent.append(token)


class FakeApnsPool:
    """按 environment 分 client's 假连接池（验证分流）。"""

    def __init__(self, *, fail_token: str | None = None, error: Exception | None = None):
        self.clients: dict[str, FakeApnsClient] = {}
        self.closed = False
        self._fail_token = fail_token
        self._error = error

    def client_for(self, environment: str | None) -> FakeApnsClient:
        env = environment or "default"
        if env not in self.clients:
            self.clients[env] = FakeApnsClient(
                fail_token=self._fail_token, error=self._error
            )
        return self.clients[env]

    def close(self):
        self.closed = True

    @property
    def all_sent(self) -> list[str]:
        return [t for c in self.clients.values() for t in c.sent]


def _patch_pool(monkeypatch, pool_or_exc):
    """把 ApnsClientPool.from_settings 替换成 fake（或抛 ApnsConfigError）。"""
    if isinstance(pool_or_exc, Exception):
        def raise_exc(cls, *a, **kw):
            raise pool_or_exc

        monkeypatch.setattr(
            push_tasks.ApnsClientPool, "from_settings", classmethod(raise_exc)
        )
    else:
        monkeypatch.setattr(
            push_tasks.ApnsClientPool,
            "from_settings",
            classmethod(lambda cls, *a, **kw: pool_or_exc),
        )


# ---------------------------------------------------------------------------
# 时间匹配（时区感知）
# ---------------------------------------------------------------------------
def test_dispatches_at_matching_local_hhmm(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])  # tz NULL → 上海

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["matched_users"] == 1
    assert summary["sent"] == 1
    assert summary["configured"] is True
    assert pool.all_sent == ["tok-A"]
    assert pool.closed is True


def test_shanghai_user_not_matched_at_utc_0800(patched, monkeypatch):
    """核心回归：上海用户 08:00 不得在 UTC 08:00（= 北京 16:00）命中。"""
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_UTC_0800)
    assert summary["matched_users"] == 0
    assert pool.all_sent == []


def test_utc_user_matched_at_utc_0800(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tz="UTC", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_UTC_0800)
    assert summary["matched_users"] == 1
    assert pool.all_sent == ["tok-A"]


def test_invalid_tz_falls_back_to_default(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tz="Mars/Olympus", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    # 非法时区按 Asia/Shanghai 兜底 → UTC 00:00 命中
    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["matched_users"] == 1


def test_skips_unmatched_hhmm(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0901)
    assert summary["matched_users"] == 0
    assert summary["sent"] == 0
    assert pool.all_sent == []


def test_skips_users_with_daily_push_off(patched, monkeypatch):
    SessionLocal, _ = patched
    with SessionLocal() as s:
        s.add(User(id=2, apple_user_id="sub-2", created_via="test"))
        s.add(PushSetting(user_id=2, daily_push=False, push_time="08:00"))
        s.add(DeviceToken(user_id=2, token="tok-off", environment="production"))
        s.commit()

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)
    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["matched_users"] == 0
    assert pool.all_sent == []


# ---------------------------------------------------------------------------
# 简报窗口按用户本地自然日
# ---------------------------------------------------------------------------
def test_brief_window_uses_local_day(patched, monkeypatch):
    """上海 08:00（UTC 00:00）派发时，窗口应是上海的今天：UTC 前日 16:00 → 当日 15:59:59。"""
    SessionLocal, windows = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert len(windows) == 1
    start, end = windows[0]
    assert start == datetime(2026, 6, 20, 16, 0, 0, tzinfo=timezone.utc)
    assert end > start
    # 窗口终点仍在 UTC 当日 16:00 前（即上海当日 24:00 前）
    assert end == datetime(2026, 6, 21, 15, 59, 59, 999999, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# APNs 环境分流（冒烟修复 #4）
# ---------------------------------------------------------------------------
def test_tokens_routed_by_environment(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-prod"], token_env="production")
    _seed(SessionLocal, user_id=2, push_time="08:00", tokens=["tok-sandbox"], token_env="sandbox")

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["sent"] == 2
    assert pool.clients["sandbox"].sent == ["tok-sandbox"]
    assert pool.clients["production"].sent == ["tok-prod"]


# ---------------------------------------------------------------------------
# token 失效软删 + 其它错误吞掉（含传输层）
# ---------------------------------------------------------------------------
def test_bad_token_marked_invalid(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-bad", "tok-ok"])

    pool = FakeApnsPool(fail_token="tok-bad", error=ApnsBadTokenError(410, "Unregistered"))
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["sent"] == 1
    assert summary["invalidated"] == 1
    with SessionLocal() as s:
        bad = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-bad")).scalar_one()
        ok = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-ok")).scalar_one()
    assert bad.invalid_at is not None
    assert ok.invalid_at is None


def test_other_apns_error_is_swallowed(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-bad", "tok-ok"])

    pool = FakeApnsPool(fail_token="tok-bad", error=ApnsError(503, "ServiceUnavailable"))
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["sent"] == 1  # 仅 tok-ok 成功
    assert summary["invalidated"] == 0
    with SessionLocal() as s:
        bad = s.execute(select(DeviceToken).where(DeviceToken.token == "tok-bad")).scalar_one()
    assert bad.invalid_at is None  # 非 token 失效不软删


def test_transport_error_does_not_break_batch(patched, monkeypatch):
    """冒烟修复 #5：单 token 传输层错误不得中断整轮，也不得误软删 token。"""
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-flaky", "tok-ok"])

    pool = FakeApnsPool(fail_token="tok-flaky", error=ApnsError(0, "transport_error"))
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["sent"] == 1  # tok-ok 仍送达
    assert summary["invalidated"] == 0
    with SessionLocal() as s:
        flaky = s.execute(
            select(DeviceToken).where(DeviceToken.token == "tok-flaky")
        ).scalar_one()
    assert flaky.invalid_at is None
    # PushRecord 正常落库（整轮未中断）
    with SessionLocal() as s:
        rec = s.execute(select(PushRecord)).scalar_one()
        assert rec.sent == 1


# ---------------------------------------------------------------------------
# 干运行（凭据未配置）
# ---------------------------------------------------------------------------
def test_dry_run_when_apns_unconfigured(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    _patch_pool(monkeypatch, ApnsConfigError("not configured"))

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
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
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    first = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    second = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert first["sent"] == 1
    assert second.get("already_done") is True
    assert second["sent"] == 1  # 复用首跑数据
    assert pool.all_sent == ["tok-A"]  # 第二次没再 send


# ---------------------------------------------------------------------------
# 当日无可见事件 → 跳过但记账
# ---------------------------------------------------------------------------
def test_skips_when_no_brief(patched, monkeypatch):
    SessionLocal, _ = patched
    _seed(SessionLocal, user_id=1, push_time="08:00", tokens=["tok-A"])

    monkeypatch.setattr(push_tasks, "_today_top_event", lambda s, ws, we: (None, 0))
    pool = FakeApnsPool()
    _patch_pool(monkeypatch, pool)

    summary = push_tasks.dispatch_daily_briefs(now=NOW_SH_0800)
    assert summary["skipped_no_brief"] is True
    assert summary["sent"] == 0
    assert pool.all_sent == []
    with SessionLocal() as s:
        rec = s.execute(select(PushRecord)).scalar_one()
        assert rec.sent == 0
        assert rec.event_ids == []
