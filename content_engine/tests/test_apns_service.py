"""阶段 4.2 单测：APNs 推送服务（services/apns）。

不联外网：用 ``httpx.MockTransport`` 注入到 ``ApnsClient`` 内置的 ``httpx.Client``，
覆盖发送链路（headers / payload / 状态码翻译 / token 失效 / 干运行）。
私钥用真实 EC P-256（cryptography 现场生成），保证 JWT 也能验签通过。
"""

from __future__ import annotations

import json

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from content_engine.services.apns import (
    ApnsBadTokenError,
    ApnsClient,
    ApnsConfigError,
    ApnsError,
    build_payload,
)

TOKEN = "a" * 64
TEAM_ID = "TEAM00ABCD"
KEY_ID = "KEY00ABCD"
BUNDLE_ID = "app.redu.ios"


@pytest.fixture
def private_key_pem() -> str:
    """生成测试用 EC P-256 私钥的 PEM 文本（pyjwt 直接用它签 ES256）。"""
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")


def _make_client(private_key_pem: str, handler) -> ApnsClient:
    transport = httpx.MockTransport(handler)
    return ApnsClient(
        team_id=TEAM_ID,
        key_id=KEY_ID,
        bundle_id=BUNDLE_ID,
        private_key=private_key_pem,
        host="api.push.apple.com",
        jwt_ttl_seconds=3000,
        client=httpx.Client(transport=transport),
    )


# ---------------------------------------------------------------------------
# build_payload
# ---------------------------------------------------------------------------
def test_build_payload_includes_aps_and_custom_keys():
    payload = build_payload(
        title="今日早报",
        body="3 条要闻",
        badge=2,
        custom={"event_id": 42, "kind": "daily_brief"},
    )
    assert payload["aps"]["alert"] == {"title": "今日早报", "body": "3 条要闻"}
    assert payload["aps"]["badge"] == 2
    assert payload["aps"]["sound"] == "default"
    assert payload["event_id"] == 42
    assert payload["kind"] == "daily_brief"


def test_build_payload_cannot_override_aps():
    payload = build_payload(title="t", body="b", custom={"aps": "evil"})
    assert payload["aps"]["alert"]["title"] == "t"  # 自定义键里的 aps 被忽略


# ---------------------------------------------------------------------------
# 发送链路
# ---------------------------------------------------------------------------
def test_send_success_sets_headers_and_payload(private_key_pem):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, headers={"apns-id": "srv-apns-id"})

    client = _make_client(private_key_pem, handler)
    result = client.send(
        token=TOKEN,
        payload=build_payload(title="t", body="b", custom={"event_id": 7}),
        collapse_id="daily-2026-06-21",
    )
    assert result.status_code == 200
    assert result.apns_id == "srv-apns-id"
    assert captured["url"].endswith(f"/3/device/{TOKEN}")
    assert captured["headers"]["apns-topic"] == BUNDLE_ID
    assert captured["headers"]["apns-push-type"] == "alert"
    assert captured["headers"]["apns-priority"] == "10"
    assert captured["headers"]["apns-collapse-id"] == "daily-2026-06-21"
    # authorization: bearer <jwt>
    auth = captured["headers"]["authorization"]
    assert auth.startswith("bearer ")
    jwt_token = auth.split(" ", 1)[1]
    # 用同密钥校验 JWT 头部 / 载荷
    header = jwt.get_unverified_header(jwt_token)
    assert header["kid"] == KEY_ID
    assert header["alg"] == "ES256"
    payload_decoded = jwt.decode(
        jwt_token,
        private_key_pem,  # 仅作为占位；下面禁掉签名校验
        algorithms=["ES256"],
        options={"verify_signature": False},
    )
    assert payload_decoded["iss"] == TEAM_ID
    # 自定义键平铺
    assert captured["body"]["event_id"] == 7


def test_send_410_raises_bad_token(private_key_pem):
    def handler(request):
        return httpx.Response(
            410,
            headers={"apns-id": "x"},
            content=json.dumps({"reason": "Unregistered"}).encode(),
        )

    client = _make_client(private_key_pem, handler)
    with pytest.raises(ApnsBadTokenError) as exc:
        client.send(token=TOKEN, payload=build_payload(title="t", body="b"))
    assert exc.value.status_code == 410
    assert exc.value.reason == "Unregistered"


def test_send_bad_device_token_raises_bad_token(private_key_pem):
    def handler(request):
        return httpx.Response(
            400,
            headers={"apns-id": "x"},
            content=json.dumps({"reason": "BadDeviceToken"}).encode(),
        )

    client = _make_client(private_key_pem, handler)
    with pytest.raises(ApnsBadTokenError):
        client.send(token=TOKEN, payload=build_payload(title="t", body="b"))


def test_send_other_error_raises_apns_error(private_key_pem):
    def handler(request):
        return httpx.Response(
            503,
            headers={"apns-id": "x"},
            content=json.dumps({"reason": "ServiceUnavailable"}).encode(),
        )

    client = _make_client(private_key_pem, handler)
    with pytest.raises(ApnsError) as exc:
        client.send(token=TOKEN, payload=build_payload(title="t", body="b"))
    assert exc.value.status_code == 503
    assert exc.value.reason == "ServiceUnavailable"
    assert not isinstance(exc.value, ApnsBadTokenError)


# ---------------------------------------------------------------------------
# JWT 复用 / 配置缺失
# ---------------------------------------------------------------------------
def test_jwt_reused_within_ttl(private_key_pem):
    auths: list[str] = []

    def handler(request):
        auths.append(request.headers["authorization"])
        return httpx.Response(200, headers={"apns-id": "x"})

    client = _make_client(private_key_pem, handler)
    client.send(token=TOKEN, payload=build_payload(title="t", body="b"))
    client.send(token=TOKEN, payload=build_payload(title="t2", body="b2"))
    assert auths[0] == auths[1]  # 同 JWT 复用


def test_from_settings_raises_when_unconfigured(monkeypatch):
    from content_engine.config import settings

    monkeypatch.setattr(settings.apns, "team_id", "")
    monkeypatch.setattr(settings.apns, "key_id", "")
    monkeypatch.setattr(settings.apns, "bundle_id", "")
    monkeypatch.setattr(settings.apns, "private_key_path", "")
    with pytest.raises(ApnsConfigError):
        ApnsClient.from_settings()


# ---------------------------------------------------------------------------
# 传输层异常包装（冒烟修复 #5：单 token 网络抖动不得炸掉整轮派发）
# ---------------------------------------------------------------------------
def test_transport_error_wrapped_as_apns_error(private_key_pem):
    def handler(request):
        raise httpx.ConnectError("boom")

    client = _make_client(private_key_pem, handler)
    with pytest.raises(ApnsError) as exc:
        client.send(token=TOKEN, payload=build_payload(title="t", body="b"))
    assert exc.value.status_code == 0
    assert exc.value.reason == "transport_error"
    assert not isinstance(exc.value, ApnsBadTokenError)  # 不得误软删 token


def test_timeout_wrapped_as_apns_error(private_key_pem):
    def handler(request):
        raise httpx.ReadTimeout("slow")

    client = _make_client(private_key_pem, handler)
    with pytest.raises(ApnsError) as exc:
        client.send(token=TOKEN, payload=build_payload(title="t", body="b"))
    assert exc.value.reason == "transport_error"


# ---------------------------------------------------------------------------
# ApnsClientPool：按 environment 分流（冒烟修复 #4）
# ---------------------------------------------------------------------------
@pytest.fixture
def configured_apns_settings(monkeypatch, tmp_path, private_key_pem):
    """把全局 settings.apns 配齐（私钥写临时文件），返回 settings.apns。"""
    from content_engine.config import settings

    key_file = tmp_path / "AuthKey_TEST.p8"
    key_file.write_text(private_key_pem, encoding="utf-8")
    monkeypatch.setattr(settings.apns, "team_id", TEAM_ID)
    monkeypatch.setattr(settings.apns, "key_id", KEY_ID)
    monkeypatch.setattr(settings.apns, "bundle_id", BUNDLE_ID)
    monkeypatch.setattr(settings.apns, "private_key_path", str(key_file))
    monkeypatch.setattr(settings.apns, "environment", "production")
    return settings.apns


def test_pool_routes_by_environment(configured_apns_settings):
    from content_engine.services.apns import ApnsClientPool

    pool = ApnsClientPool.from_settings()
    try:
        sandbox = pool.client_for("sandbox")
        prod = pool.client_for("production")
        assert sandbox._host == "api.sandbox.push.apple.com"
        assert prod._host == "api.push.apple.com"
        # 同环境复用同一实例（连接池语义）
        assert pool.client_for("sandbox") is sandbox
    finally:
        pool.close()


def test_pool_unknown_environment_falls_back_to_default(configured_apns_settings):
    from content_engine.services.apns import ApnsClientPool

    pool = ApnsClientPool.from_settings()
    try:
        # 非法/缺失 environment → 回落到配置默认（production）
        assert pool.client_for("weird")._host == "api.push.apple.com"
        assert pool.client_for(None)._host == "api.push.apple.com"
    finally:
        pool.close()


def test_pool_raises_when_unconfigured(monkeypatch):
    from content_engine.config import settings
    from content_engine.services.apns import ApnsClientPool

    monkeypatch.setattr(settings.apns, "team_id", "")
    monkeypatch.setattr(settings.apns, "key_id", "")
    monkeypatch.setattr(settings.apns, "bundle_id", "")
    monkeypatch.setattr(settings.apns, "private_key_path", "")
    with pytest.raises(ApnsConfigError):
        ApnsClientPool.from_settings()
