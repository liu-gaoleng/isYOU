"""阶段 3.1 单测：C 端账号鉴权（本地 JWT + dev 登录 + /me）与付费墙截断。

不依赖真实 PG：
- JWT 签发/校验走真实 PyJWT（HS256），仅 monkeypatch settings.auth；
- 路由用假会话模拟 users / events 表行为；
- 付费墙：会员见全文、非会员见截断 preview + paywall。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from content_engine.config import settings
from content_engine.models import EventStatus, Module
from content_engine.services import auth as auth_service

NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "test-jwt-secret-please-rotate-0123456789"


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch):
    """统一配置 auth：注入 JWT secret、开启 dev 登录、设 bundle id。"""
    monkeypatch.setattr(settings.auth, "jwt_secret", SECRET)
    monkeypatch.setattr(settings.auth, "jwt_expire_minutes", 60)
    monkeypatch.setattr(settings.auth, "jwt_issuer", "redu-test")
    monkeypatch.setattr(settings.auth, "dev_login_enabled", True)
    monkeypatch.setattr(settings.auth, "apple_bundle_id", "app.redu.ios")


# ---------------------------------------------------------------------------
# 本地 JWT 签发/校验
# ---------------------------------------------------------------------------
def test_jwt_roundtrip():
    token, expires_in = auth_service.issue_access_token(42)
    assert expires_in == 60 * 60
    assert auth_service.decode_access_token(token) == 42


def test_jwt_tampered_rejected():
    token, _ = auth_service.issue_access_token(1)
    with pytest.raises(auth_service.AuthError):
        auth_service.decode_access_token(token + "x")


def test_jwt_missing_secret(monkeypatch):
    monkeypatch.setattr(settings.auth, "jwt_secret", "")
    with pytest.raises(auth_service.AuthConfigError):
        auth_service.issue_access_token(1)


def test_apple_missing_bundle_id(monkeypatch):
    monkeypatch.setattr(settings.auth, "apple_bundle_id", "")
    with pytest.raises(auth_service.AuthConfigError):
        auth_service.verify_apple_identity_token("whatever")


# ---------------------------------------------------------------------------
# 假用户表 + 路由
# ---------------------------------------------------------------------------
class _User:
    def __init__(self, uid, apple_user_id="apple-sub-1"):
        self.id = uid
        self.apple_user_id = apple_user_id
        self.email = None
        self.display_name = None
        self.created_via = "test"
        self.member_tier = "free"
        self.member_expire_at = None


class _FakeUserSession:
    """模拟 users 表：按 id / apple_user_id 查询、新增自增 id。"""

    def __init__(self, store):
        self.store = store  # dict[int, _User]
        self._next = max(store.keys(), default=0) + 1

    def execute(self, *_args, **_kwargs):
        users = list(self.store.values())
        # 简化：dev-login/apple 仅按 apple_user_id 过滤，这里返回全部由调用方 scalar_one_or_none 处理
        outer = self

        class _R:
            def scalar_one_or_none(self_inner):
                # 取测试中预置的"匹配用户"（store 里 apple_user_id == _match）
                want = getattr(outer, "_match", None)
                for u in users:
                    if u.apple_user_id == want:
                        return u
                return None

        return _R()

    def get(self, _model, pk):
        return self.store.get(pk)

    def add(self, obj):
        obj.id = self._next
        self._next += 1
        # 真实 DB 在 flush 时应用列默认值；假会话手动补齐被测代码读取的默认
        if getattr(obj, "member_tier", None) is None:
            obj.member_tier = "free"
        if getattr(obj, "created_via", None) is None:
            obj.created_via = "apple"
        self.store[obj.id] = obj

    def flush(self):
        pass

    def refresh(self, _obj):
        pass

    def expunge(self, _obj):
        pass


@pytest.fixture
def client(monkeypatch):
    user_store: dict = {}

    @contextmanager
    def fake_get_session():
        sess = _FakeUserSession(user_store)
        # dev-login 用 apple_user_id 查重：测试里通过设置 _match 控制是否命中
        sess._match = next(iter([u.apple_user_id for u in user_store.values()]), "__none__")
        yield sess

    from content_engine.api.routers import auth as auth_router
    from content_engine.api import deps as deps_mod

    monkeypatch.setattr(auth_router, "get_session", fake_get_session)
    monkeypatch.setattr(deps_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._user_store = user_store
        yield c


def test_dev_login_creates_user_and_me(client):
    r = client.post("/api/v1/auth/dev-login", json={"apple_user_id": "sub-A"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 3600
    assert body["user"]["is_member"] is False
    token = body["access_token"]

    r2 = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["id"] == body["user"]["id"]


def test_dev_login_as_member(client):
    r = client.post(
        "/api/v1/auth/dev-login",
        json={"apple_user_id": "sub-M", "as_member": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user"]["is_member"] is True
    assert r.json()["user"]["member_tier"] == "member"


def test_dev_login_disabled(client, monkeypatch):
    monkeypatch.setattr(settings.auth, "dev_login_enabled", False)
    r = client.post("/api/v1/auth/dev-login", json={"apple_user_id": "x"})
    assert r.status_code == 403


def test_me_requires_auth(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_me_bad_token(client):
    r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 付费墙：event detail 按会员态裁剪 deep_content
# ---------------------------------------------------------------------------
class _Content:
    def __init__(self, deep):
        self.version = 1
        self.title = "标题"
        self.sources = []
        self.deep_content = deep


class _Event:
    def __init__(self, deep):
        self.id = 1
        self.module = Module.tech
        self.status = EventStatus.published
        self.card_summary = "卡片"
        self.detail_summary = "详情"
        self.tags = []
        self.importance = 1.0
        self.hotness = 0.5
        self.source_count = 1
        self.first_seen = NOW
        self.last_update = NOW
        self.contents = [_Content(deep)]


@pytest.fixture
def paywall_client(monkeypatch):
    user_store: dict = {1: _User(1)}
    deep_text = "深" * 200
    event = _Event(deep_text)

    @contextmanager
    def fake_user_session():
        sess = _FakeUserSession(user_store)
        sess._match = "__none__"
        yield sess

    class _EventSession:
        def get(self, _model, pk):
            return event if pk == 1 else None

    @contextmanager
    def fake_event_session():
        yield _EventSession()

    from content_engine.api.routers import brief as brief_mod
    from content_engine.api import deps as deps_mod

    monkeypatch.setattr(brief_mod, "get_session", fake_event_session)
    monkeypatch.setattr(deps_mod, "get_session", fake_user_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._deep_text = deep_text
        c._user_store = user_store
        yield c


def test_paywall_locked_for_anonymous(paywall_client):
    r = paywall_client.get("/api/v1/event/1")
    assert r.status_code == 200, r.text
    dc = r.json()["deep_content"]
    assert dc["is_locked"] is True
    assert dc["content"] is None
    assert dc["preview"].endswith("……")
    assert dc["paywall"]["required_tier"] == "member"
    # 非会员永远拿不到全文
    assert len(dc["preview"]) < len(paywall_client._deep_text)


def test_paywall_unlocked_for_member(paywall_client):
    user = paywall_client._user_store[1]
    user.member_tier = "member"
    user.member_expire_at = NOW + timedelta(days=30)
    token, _ = auth_service.issue_access_token(1)

    r = paywall_client.get(
        "/api/v1/event/1", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200, r.text
    dc = r.json()["deep_content"]
    assert dc["is_locked"] is False
    assert dc["content"] == paywall_client._deep_text


def test_paywall_expired_member_locked(paywall_client):
    user = paywall_client._user_store[1]
    user.member_tier = "member"
    user.member_expire_at = NOW - timedelta(days=1)  # 已过期
    token, _ = auth_service.issue_access_token(1)

    r = paywall_client.get(
        "/api/v1/event/1", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json()["deep_content"]["is_locked"] is True
