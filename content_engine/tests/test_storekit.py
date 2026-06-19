"""阶段 3.2 单测：StoreKit 2 JWS 交易验签（services/storekit）。

用真实 EC P-256 证书链（自建 root→intermediate→leaf）+ 真实 ES256 JWS 签名，
端到端校验验签链路：可信根比对、逐级签名校验、leaf 验签、payload 解码、
bundleId / environment / product 业务校验、退款态识别。
"""

from __future__ import annotations

import base64
import datetime as dt

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from content_engine.config import settings
from content_engine.services import storekit

BUNDLE_ID = "app.redu.ios"
PRODUCT_MONTHLY = "com.redu.app.member.monthly"


# ---------------------------------------------------------------------------
# 测试用证书链工具
# ---------------------------------------------------------------------------
def _mk_cert(subject_cn, issuer_cert, issuer_key, *, is_ca, days_valid=3650, not_before=None):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    issuer_name = issuer_cert.subject if issuer_cert else subject
    nb = not_before or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1))
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(nb + dt.timedelta(days=days_valid))
    )
    if is_ca:
        builder = builder.add_extension(
            x509.BasicConstraints(ca=True, path_length=None), critical=True
        )
    signing_key = issuer_key or key
    cert = builder.sign(signing_key, hashes.SHA256())
    return cert, key


@pytest.fixture
def chain():
    """生成 root→intermediate→leaf 三级 EC 证书链。"""
    root_cert, root_key = _mk_cert("Apple Root CA - G3 (test)", None, None, is_ca=True)
    int_cert, int_key = _mk_cert("Apple WWDR Intermediate (test)", root_cert, root_key, is_ca=True)
    leaf_cert, leaf_key = _mk_cert("StoreKit Leaf (test)", int_cert, int_key, is_ca=False)
    return {
        "root_cert": root_cert,
        "int_cert": int_cert,
        "leaf_cert": leaf_cert,
        "leaf_key": leaf_key,
    }


def _x5c(chain) -> list[str]:
    return [
        base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
        for c in (chain["leaf_cert"], chain["int_cert"], chain["root_cert"])
    ]


def _sign_jws(chain, payload: dict) -> str:
    return jwt.encode(
        payload,
        chain["leaf_key"],
        algorithm="ES256",
        headers={"alg": "ES256", "x5c": _x5c(chain)},
    )


def _ms(days_from_now: int) -> int:
    t = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days_from_now)
    return int(t.timestamp() * 1000)


def _payload(**overrides) -> dict:
    base = {
        "transactionId": "txn-1001",
        "originalTransactionId": "txn-orig-1",
        "productId": PRODUCT_MONTHLY,
        "bundleId": BUNDLE_ID,
        "environment": "Sandbox",
        "purchaseDate": _ms(0),
        "expiresDate": _ms(30),
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _billing_config(tmp_path, chain, monkeypatch):
    """把可信根指向测试 root，并配 bundle/product/environment。"""
    root_pem = tmp_path / "root.pem"
    root_pem.write_bytes(chain["root_cert"].public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(settings.billing, "apple_root_ca_path", str(root_pem))
    monkeypatch.setattr(settings.billing, "bundle_id", BUNDLE_ID)
    monkeypatch.setattr(settings.billing, "product_monthly", PRODUCT_MONTHLY)
    monkeypatch.setattr(settings.billing, "accepted_environments", "Production,Sandbox")
    # _trusted_root_der 有 lru_cache，逐用例清缓存
    storekit._trusted_root_der.cache_clear()
    yield
    storekit._trusted_root_der.cache_clear()


# ---------------------------------------------------------------------------
# 正常验签
# ---------------------------------------------------------------------------
def test_verify_valid_transaction(chain):
    token = _sign_jws(chain, _payload())
    vt = storekit.verify_signed_transaction(token)
    assert vt.transaction_id == "txn-1001"
    assert vt.original_transaction_id == "txn-orig-1"
    assert vt.product_id == PRODUCT_MONTHLY
    assert vt.plan.value == "monthly"
    assert vt.environment == "Sandbox"
    assert vt.is_revoked is False
    assert vt.expires_date is not None and vt.purchase_date is not None


def test_revoked_transaction_flagged(chain):
    token = _sign_jws(chain, _payload(revocationDate=_ms(-1)))
    vt = storekit.verify_signed_transaction(token)
    assert vt.is_revoked is True


# ---------------------------------------------------------------------------
# 篡改 / 伪造防护
# ---------------------------------------------------------------------------
def test_reject_when_root_not_trusted(chain, tmp_path, monkeypatch):
    """x5c 末端 root 与可信根不一致 → 拒绝（核心防伪造）。"""
    other_root, other_key = _mk_cert("Evil Root", None, None, is_ca=True)
    other_pem = tmp_path / "evil.pem"
    other_pem.write_bytes(other_root.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(settings.billing, "apple_root_ca_path", str(other_pem))
    storekit._trusted_root_der.cache_clear()

    token = _sign_jws(chain, _payload())
    with pytest.raises(storekit.ReceiptError, match="trusted Apple Root CA"):
        storekit.verify_signed_transaction(token)


def test_reject_tampered_payload(chain):
    """篡改 payload（替换中段）使 leaf 验签失败。"""
    token = _sign_jws(chain, _payload())
    h, _p, s = token.split(".")
    forged_mid = base64.urlsafe_b64encode(b'{"transactionId":"hacked","productId":"x"}').rstrip(b"=").decode()
    tampered = f"{h}.{forged_mid}.{s}"
    with pytest.raises(storekit.ReceiptError):
        storekit.verify_signed_transaction(tampered)


def test_reject_broken_chain(chain, monkeypatch):
    """中间证书被替换为不相关证书 → 链签名校验失败。"""
    fake_int, _ = _mk_cert("Fake Intermediate", chain["root_cert"], None, is_ca=True)
    bad_x5c = [
        base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
        for c in (chain["leaf_cert"], fake_int, chain["root_cert"])
    ]
    token = jwt.encode(
        _payload(),
        chain["leaf_key"],
        algorithm="ES256",
        headers={"alg": "ES256", "x5c": bad_x5c},
    )
    with pytest.raises(storekit.ReceiptError):
        storekit.verify_signed_transaction(token)


def test_reject_missing_x5c(chain):
    token = jwt.encode(_payload(), chain["leaf_key"], algorithm="ES256")
    with pytest.raises(storekit.ReceiptError, match="x5c"):
        storekit.verify_signed_transaction(token)


# ---------------------------------------------------------------------------
# 业务校验
# ---------------------------------------------------------------------------
def test_reject_bundle_mismatch(chain):
    token = _sign_jws(chain, _payload(bundleId="com.evil.app"))
    with pytest.raises(storekit.ReceiptError, match="bundleId"):
        storekit.verify_signed_transaction(token)


def test_reject_environment_not_accepted(chain, monkeypatch):
    monkeypatch.setattr(settings.billing, "accepted_environments", "Production")
    token = _sign_jws(chain, _payload(environment="Sandbox"))
    with pytest.raises(storekit.ReceiptError, match="environment"):
        storekit.verify_signed_transaction(token)


def test_reject_unknown_product(chain):
    token = _sign_jws(chain, _payload(productId="com.unknown.sku"))
    with pytest.raises(storekit.ReceiptError, match="unknown product"):
        storekit.verify_signed_transaction(token)


def test_config_error_when_root_missing(chain, monkeypatch):
    monkeypatch.setattr(settings.billing, "apple_root_ca_path", "")
    storekit._trusted_root_der.cache_clear()
    token = _sign_jws(chain, _payload())
    with pytest.raises(storekit.BillingConfigError):
        storekit.verify_signed_transaction(token)


def test_product_to_plan_mapping(monkeypatch):
    monkeypatch.setattr(settings.billing, "product_yearly", "com.redu.app.member.yearly")
    assert storekit.product_to_plan("com.redu.app.member.yearly").value == "yearly"
    with pytest.raises(storekit.ReceiptError):
        storekit.product_to_plan("nope")
