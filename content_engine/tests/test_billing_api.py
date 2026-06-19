"""阶段 3.2 单测：会员订阅核销路由 + 会员态 + 到期巡检任务。

不依赖真实 PG / 真实 Apple 证书：
- subscriptions / iap_transactions / users 用真实 SQLite in-memory；
- StoreKit JWS 验签 monkeypatch 为返回构造好的 VerifiedTransaction（验签链已由
  test_storekit.py 端到端覆盖，这里聚焦核销/会员态/巡检的业务逻辑）。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.config import settings
from content_engine.models import IapTransaction, Subscription, User
from content_engine.models.enums import SubscriptionPlan
from content_engine.services import auth as auth_service
from content_engine.services import storekit

SECRET = "test-jwt-secret-please-rotate-0123456789"
NOW = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _auth_config(monkeypatch):
    monkeypatch.setattr(settings.auth, "jwt_secret", SECRET)
    monkeypatch.setattr(settings.auth, "jwt_expire_minutes", 60)
    monkeypatch.setattr(settings.auth, "jwt_issuer", "redu-test")


def _vt(**overrides) -> storekit.VerifiedTransaction:
    base = dict(
        transaction_id="txn-1",
        original_transaction_id="orig-1",
        product_id="com.redu.app.member.monthly",
        plan=SubscriptionPlan.monthly,
        environment="Sandbox",
        purchase_date=datetime.now(timezone.utc),
        expires_date=datetime.now(timezone.utc) + timedelta(days=30),
        bundle_id="app.redu.ios",
        is_revoked=False,
        raw_payload={"transactionId": "txn-1"},
    )
    base.update(overrides)
    return storekit.VerifiedTransaction(**base)


@pytest.fixture
def client(monkeypatch):
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for model in (User, Subscription, IapTransaction):
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
    from content_engine.api.routers import billing as billing_mod
    from content_engine.api.routers import me as me_mod

    monkeypatch.setattr(billing_mod, "get_session", fake_get_session)
    monkeypatch.setattr(me_mod, "get_session", fake_get_session)
    monkeypatch.setattr(deps_mod, "get_session", fake_get_session)

    from content_engine.api.app import app

    with TestClient(app) as c:
        c._SessionLocal = SessionLocal
        token, _ = auth_service.issue_access_token(1)
        c.auth_headers = {"Authorization": f"Bearer {token}"}
        yield c


# ---------------------------------------------------------------------------
# plans / 鉴权
# ---------------------------------------------------------------------------
def test_plans_public(client):
    r = client.get("/api/v1/billing/plans")
    assert r.status_code == 200
    plans = {p["plan"]: p for p in r.json()}
    assert set(plans) == {"monthly", "quarterly", "yearly"}
    assert plans["yearly"]["period_days"] == 365


def test_verify_requires_auth(client):
    r = client.post("/api/v1/billing/verify", json={"signed_transaction": "x"})
    assert r.status_code == 401


def test_membership_requires_auth(client):
    assert client.get("/api/v1/me/membership").status_code == 401


# ---------------------------------------------------------------------------
# 核销升级
# ---------------------------------------------------------------------------
def test_verify_upgrades_member(client, monkeypatch):
    monkeypatch.setattr(storekit, "verify_signed_transaction", lambda _t: _vt())
    r = client.post(
        "/api/v1/billing/verify",
        headers=client.auth_headers,
        json={"signed_transaction": "jws"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_member"] is True
    assert body["member_tier"] == "member"
    assert body["plan"] == "monthly"
    assert body["subscription_status"] == "active"

    # 会员态可经 /me/membership 复读
    m = client.get("/api/v1/me/membership", headers=client.auth_headers).json()
    assert m["is_member"] is True
    assert m["plan"] == "monthly"


def test_verify_idempotent_same_transaction(client, monkeypatch):
    monkeypatch.setattr(storekit, "verify_signed_transaction", lambda _t: _vt())
    client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    with client._SessionLocal() as s:
        txns = s.query(IapTransaction).all()
        subs = s.query(Subscription).all()
    assert len(txns) == 1  # 同 transaction_id 不重复记账
    assert len(subs) == 1  # 每用户一行


def test_verify_renewal_extends_expiry(client, monkeypatch):
    monkeypatch.setattr(storekit, "verify_signed_transaction", lambda _t: _vt())
    client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})

    later = datetime.now(timezone.utc) + timedelta(days=60)
    monkeypatch.setattr(
        storekit,
        "verify_signed_transaction",
        lambda _t: _vt(transaction_id="txn-2", expires_date=later),
    )
    r = client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws2"})
    body = r.json()
    assert body["is_member"] is True
    with client._SessionLocal() as s:
        sub = s.query(Subscription).filter_by(user_id=1).one()
        assert sub.last_transaction_id == "txn-2"
        assert len(s.query(IapTransaction).all()) == 2


def test_verify_revoked_does_not_grant(client, monkeypatch):
    monkeypatch.setattr(
        storekit, "verify_signed_transaction", lambda _t: _vt(is_revoked=True)
    )
    r = client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    body = r.json()
    assert body["is_member"] is False
    assert body["subscription_status"] == "refunded"


def test_verify_expired_transaction_not_member(client, monkeypatch):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    monkeypatch.setattr(
        storekit, "verify_signed_transaction", lambda _t: _vt(expires_date=past)
    )
    r = client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    assert r.json()["is_member"] is False


def test_verify_receipt_error_400(client, monkeypatch):
    def _raise(_t):
        raise storekit.ReceiptError("bad sig")

    monkeypatch.setattr(storekit, "verify_signed_transaction", _raise)
    r = client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    assert r.status_code == 400


def test_verify_config_error_503(client, monkeypatch):
    def _raise(_t):
        raise storekit.BillingConfigError("no root ca")

    monkeypatch.setattr(storekit, "verify_signed_transaction", _raise)
    r = client.post("/api/v1/billing/verify", headers=client.auth_headers, json={"signed_transaction": "jws"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# 恢复购买
# ---------------------------------------------------------------------------
def test_restore_picks_latest_expiry(client, monkeypatch):
    soon = datetime.now(timezone.utc) + timedelta(days=10)
    far = datetime.now(timezone.utc) + timedelta(days=300)
    mapping = {
        "a": _vt(transaction_id="t-a", expires_date=soon),
        "b": _vt(transaction_id="t-b", plan=SubscriptionPlan.yearly, expires_date=far),
    }
    monkeypatch.setattr(storekit, "verify_signed_transaction", lambda t: mapping[t])
    r = client.post(
        "/api/v1/billing/restore",
        headers=client.auth_headers,
        json=[{"signed_transaction": "a"}, {"signed_transaction": "b"}],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_member"] is True
    assert body["plan"] == "yearly"  # 取到期最晚的一笔


def test_restore_empty_400(client):
    r = client.post("/api/v1/billing/restore", headers=client.auth_headers, json=[])
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 到期巡检任务
# ---------------------------------------------------------------------------
def test_downgrade_expired_sweep(client, monkeypatch):
    from content_engine.tasks import billing_tasks

    SessionLocal = client._SessionLocal

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

    monkeypatch.setattr(billing_tasks, "get_session", fake_get_session)

    past = datetime.now(timezone.utc) - timedelta(days=1)
    future = datetime.now(timezone.utc) + timedelta(days=10)
    with SessionLocal() as s:
        # 过期会员
        s.add(User(id=2, apple_user_id="sub-2", created_via="test",
                   member_tier="member", member_expire_at=past))
        s.add(Subscription(user_id=2, status="active", expires_at=past, plan="monthly"))
        # 有效会员
        s.add(User(id=3, apple_user_id="sub-3", created_via="test",
                   member_tier="member", member_expire_at=future))
        s.add(Subscription(user_id=3, status="active", expires_at=future, plan="yearly"))
        s.commit()

    result = billing_tasks.downgrade_expired()
    assert result["downgraded_users"] == 1
    assert result["expired_subscriptions"] == 1

    with SessionLocal() as s:
        u2 = s.get(User, 2)
        u3 = s.get(User, 3)
        sub2 = s.query(Subscription).filter_by(user_id=2).one()
        sub3 = s.query(Subscription).filter_by(user_id=3).one()
    assert u2.member_tier == "free"
    assert sub2.status == "expired"
    assert u3.member_tier == "member"  # 未过期不动
    assert sub3.status == "active"
