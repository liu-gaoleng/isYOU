"""阶段 3.2：Apple StoreKit 2 交易（JWS）验签服务。

职责边界（不碰 DB，纯密码学/网络）：
- :func:`verify_signed_transaction`：校验客户端上送的 StoreKit 2 ``JWSTransaction``
  （Apple 用其私钥 ES256 签名、头部 x5c 携带证书链），返回解码后的交易信息；
- :func:`product_to_plan`：把 App Store Connect 的 product_id 映射到 SubscriptionPlan。

校验链（对齐 Apple 官方 App Store Server Library 的离线验签流程）：
1. 解析 JWS 头部 x5c 证书链：[leaf, intermediate, root]；
2. 校验链：root 公钥验 intermediate 签名、intermediate 公钥验 leaf 签名，
   且 x5c 末端 root 必须与配置的可信 Apple Root CA 逐字节一致（防伪造链）；
3. 校验各证书有效期（not_before/not_after）；
4. 用 leaf 证书公钥验 JWS 签名（ES256），解码 payload；
5. 业务校验：bundleId 一致、environment 在白名单内。

铁律：
- 可信 Apple Root CA 来自配置文件，缺失 → 直接抛 BillingConfigError（503），绝不裸放行；
- 任一环节失败 → ReceiptError（400/401），伪造/篡改交易拿不到会员权益；
- expiresDate 以 Apple payload 为权威，不由客户端或本地推算。
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

import jwt
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from content_engine.config import settings
from content_engine.models.enums import SubscriptionPlan


class ReceiptError(Exception):
    """收据/交易校验失败（签名无效 / 证书链不可信 / payload 非法）。映射 400。"""


class BillingConfigError(Exception):
    """计费配置缺失（如未配置 Apple Root CA）。映射 503，提示运维补配置。"""


@dataclass
class VerifiedTransaction:
    """JWS 交易验签结果（字段取自 Apple JWSTransactionDecodedPayload）。"""

    transaction_id: str
    original_transaction_id: str
    product_id: str
    plan: SubscriptionPlan
    environment: str
    purchase_date: datetime | None
    expires_date: datetime | None
    bundle_id: str | None
    is_revoked: bool
    raw_payload: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# product_id ↔ 订阅档位
# ---------------------------------------------------------------------------
def _product_map() -> dict[str, SubscriptionPlan]:
    b = settings.billing
    return {
        b.product_monthly: SubscriptionPlan.monthly,
        b.product_quarterly: SubscriptionPlan.quarterly,
        b.product_yearly: SubscriptionPlan.yearly,
    }


def product_to_plan(product_id: str) -> SubscriptionPlan:
    """把 product_id 映射到订阅档位；未知商品抛 ReceiptError。"""
    plan = _product_map().get(product_id)
    if plan is None:
        raise ReceiptError(f"unknown product_id: {product_id}")
    return plan


# ---------------------------------------------------------------------------
# 证书链校验
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _trusted_root_der() -> bytes:
    """加载配置的可信 Apple Root CA，返回其 DER 字节（用于逐字节比对）。"""
    path = settings.billing.apple_root_ca_path
    if not path:
        raise BillingConfigError(
            "RD_BILLING_APPLE_ROOT_CA_PATH 未配置，无法校验 StoreKit 交易"
        )
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise BillingConfigError(f"无法读取 Apple Root CA 文件: {exc}") from exc
    try:
        if b"-----BEGIN" in data:
            cert = x509.load_pem_x509_certificate(data)
        else:
            cert = x509.load_der_x509_certificate(data)
    except ValueError as exc:
        raise BillingConfigError(f"Apple Root CA 文件解析失败: {exc}") from exc
    return cert.public_bytes(encoding=serialization.Encoding.DER)


def _b64_header_x5c(token: str) -> list[str]:
    """读 JWS 头部 x5c（不验签，仅取证书链）。"""
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise ReceiptError(f"invalid jws header: {exc}") from exc
    x5c = header.get("x5c")
    if not x5c or not isinstance(x5c, list):
        raise ReceiptError("jws header missing x5c certificate chain")
    return x5c


def _verify_cert_signed_by(child: x509.Certificate, issuer: x509.Certificate) -> None:
    """校验 child 证书由 issuer 私钥签发（ECDSA）。失败抛 ReceiptError。"""
    pub = issuer.public_key()
    if not isinstance(pub, ec.EllipticCurvePublicKey):
        raise ReceiptError("unexpected issuer key type (expected EC)")
    try:
        pub.verify(
            child.signature,
            child.tbs_certificate_bytes,
            ec.ECDSA(child.signature_hash_algorithm),
        )
    except InvalidSignature as exc:
        raise ReceiptError("certificate chain signature invalid") from exc


def _verify_chain_and_leaf(x5c: list[str]) -> x509.Certificate:
    """校验 x5c 证书链并返回 leaf 证书。

    要求链含 leaf→intermediate→root；root 必须与配置可信根逐字节一致。
    """
    if len(x5c) < 2:
        raise ReceiptError("x5c chain too short")
    try:
        certs = [x509.load_der_x509_certificate(base64.b64decode(c)) for c in x5c]
    except (ValueError, base64.binascii.Error) as exc:
        raise ReceiptError(f"invalid certificate in x5c: {exc}") from exc

    leaf, root = certs[0], certs[-1]

    # x5c 末端 root 必须就是我们信任的 Apple Root CA（防伪造整条链）
    if root.public_bytes(encoding=serialization.Encoding.DER) != _trusted_root_der():
        raise ReceiptError("x5c root is not the trusted Apple Root CA")

    # 逐级验签：certs[i] 由 certs[i+1] 签发
    for i in range(len(certs) - 1):
        _verify_cert_signed_by(certs[i], certs[i + 1])

    # 有效期校验（用 UTC，cryptography 41+ 提供 *_utc 属性）
    now = datetime.now(timezone.utc)
    for cert in certs:
        try:
            not_before = cert.not_valid_before_utc
            not_after = cert.not_valid_after_utc
        except AttributeError:  # 兼容老版本 cryptography（naive datetime）
            not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)
            not_after = cert.not_valid_after.replace(tzinfo=timezone.utc)
        if not (not_before <= now <= not_after):
            raise ReceiptError("certificate in chain expired or not yet valid")

    return leaf


# ---------------------------------------------------------------------------
# payload 解码
# ---------------------------------------------------------------------------
def _ms_to_dt(value) -> datetime | None:
    """Apple 时间字段为毫秒 epoch；转 timezone-aware datetime。"""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def verify_signed_transaction(signed_transaction: str) -> VerifiedTransaction:
    """校验 StoreKit 2 JWSTransaction 并返回交易信息。失败抛 ReceiptError。"""
    if not signed_transaction or not isinstance(signed_transaction, str):
        raise ReceiptError("empty signed transaction")

    x5c = _b64_header_x5c(signed_transaction)
    leaf = _verify_chain_and_leaf(x5c)

    # 用 leaf 公钥验 JWS 签名；StoreKit payload 无标准 exp/iss/aud，关闭这些校验
    try:
        payload = jwt.decode(
            signed_transaction,
            leaf.public_key(),
            algorithms=["ES256"],
            options={
                "verify_signature": True,
                "verify_exp": False,
                "verify_aud": False,
                "verify_iss": False,
            },
        )
    except jwt.InvalidTokenError as exc:
        raise ReceiptError(f"jws signature invalid: {exc}") from exc

    product_id = payload.get("productId")
    transaction_id = payload.get("transactionId")
    if not product_id or not transaction_id:
        raise ReceiptError("transaction payload missing productId/transactionId")

    # bundleId 校验（配置了才强校验）
    expected_bundle = settings.billing.bundle_id
    bundle_id = payload.get("bundleId")
    if expected_bundle and bundle_id and bundle_id != expected_bundle:
        raise ReceiptError(
            f"bundleId mismatch: {bundle_id} != {expected_bundle}"
        )

    # environment 白名单
    environment = payload.get("environment", "")
    accepted = {
        e.strip()
        for e in settings.billing.accepted_environments.split(",")
        if e.strip()
    }
    if accepted and environment and environment not in accepted:
        raise ReceiptError(f"environment not accepted: {environment}")

    plan = product_to_plan(product_id)

    # revocationDate 存在即已退款/撤销
    is_revoked = payload.get("revocationDate") is not None

    return VerifiedTransaction(
        transaction_id=str(transaction_id),
        original_transaction_id=str(
            payload.get("originalTransactionId") or transaction_id
        ),
        product_id=str(product_id),
        plan=plan,
        environment=str(environment),
        purchase_date=_ms_to_dt(payload.get("purchaseDate")),
        expires_date=_ms_to_dt(payload.get("expiresDate")),
        bundle_id=bundle_id,
        is_revoked=is_revoked,
        raw_payload=payload,
    )


def _decode_unverified(signed_transaction: str) -> dict:
    """仅解码 JWS payload 不验签（测试/排障辅助，绝不用于生产授信）。"""
    parts = signed_transaction.split(".")
    if len(parts) != 3:
        raise ReceiptError("malformed jws")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


__all__ = [
    "ReceiptError",
    "BillingConfigError",
    "VerifiedTransaction",
    "verify_signed_transaction",
    "product_to_plan",
]
