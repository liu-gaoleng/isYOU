"""阶段 3.1：账号鉴权服务（Sign in with Apple 验签 + 本地 JWT 签发/校验）。

职责边界（不碰 DB，纯密码学/网络）：
- :func:`verify_apple_identity_token`：用 Apple 公钥（JWKS）验签客户端上送的
  identityToken（Apple 签发的 RS256 JWT），校验 iss/aud/exp，返回 (sub, email)；
- :func:`issue_access_token` / :func:`decode_access_token`：本地 HS256 access token
  的签发与校验，sub 存本地用户 id。

铁律：
- JWT secret 来自环境变量；为空时签发/校验直接抛 ``AuthConfigError``（503），
  绝不用空密钥或硬编码默认值裸奔；
- Apple JWKS 进程内缓存（公钥极少轮换），TTL 到期或 kid 未命中时刷新一次；
- 校验严格：算法白名单、aud=bundle_id、iss=appleid.apple.com、exp 必校验。
"""

from __future__ import annotations

import ssl
import threading
import time
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

from content_engine.config import settings


class AuthError(Exception):
    """鉴权失败（token 无效 / 验签不通过 / 过期）。映射 401。"""


class AuthConfigError(Exception):
    """鉴权配置缺失（如 JWT secret 未配置）。映射 503，提示运维补配置。"""


def _ssl_context() -> ssl.SSLContext:
    """与 llm_client 一致：优先 certifi CA bundle，修 macOS 证书缺失。"""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ---------------------------------------------------------------------------
# 本地 JWT（HS256）
# ---------------------------------------------------------------------------
def issue_access_token(user_id: int) -> tuple[str, int]:
    """为本地用户签发 access token。返回 (token, expires_in_seconds)。"""
    secret = settings.auth.jwt_secret
    if not secret:
        raise AuthConfigError("RD_AUTH_JWT_SECRET 未配置，无法签发 access token")
    expire_seconds = settings.auth.jwt_expire_minutes * 60
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "iss": settings.auth.jwt_issuer,
        "iat": now,
        "exp": now + expire_seconds,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, expire_seconds


def decode_access_token(token: str) -> int:
    """校验本地 access token，返回 user_id。失败抛 AuthError。"""
    secret = settings.auth.jwt_secret
    if not secret:
        raise AuthConfigError("RD_AUTH_JWT_SECRET 未配置，无法校验 access token")
    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            issuer=settings.auth.jwt_issuer,
            options={"require": ["sub", "exp", "iss"]},
        )
        return int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise AuthError(f"invalid access token: {exc}") from exc


# ---------------------------------------------------------------------------
# Sign in with Apple：identityToken 验签
# ---------------------------------------------------------------------------
@dataclass
class AppleIdentity:
    """Apple identityToken 验签结果。"""

    apple_user_id: str  # token 的 sub，稳定唯一，作为 users.apple_user_id
    email: str | None = None


_jwk_lock = threading.Lock()
_jwk_client: PyJWKClient | None = None
_jwk_client_at: float = 0.0


def _get_apple_jwk_client() -> PyJWKClient:
    """进程内缓存的 PyJWKClient，TTL 到期后重建（拉取最新公钥）。"""
    global _jwk_client, _jwk_client_at
    with _jwk_lock:
        now = time.time()
        ttl = settings.auth.apple_jwks_cache_ttl
        if _jwk_client is None or (now - _jwk_client_at) > ttl:
            _jwk_client = PyJWKClient(
                settings.auth.apple_jwks_url,
                ssl_context=_ssl_context(),
                cache_keys=True,
            )
            _jwk_client_at = now
        return _jwk_client


def verify_apple_identity_token(identity_token: str) -> AppleIdentity:
    """验签 Apple identityToken 并返回身份。失败抛 AuthError。

    校验项：RS256 签名（Apple JWKS 公钥）+ iss + aud(bundle_id) + exp。
    """
    bundle_id = settings.auth.apple_bundle_id
    if not bundle_id:
        raise AuthConfigError("RD_AUTH_APPLE_BUNDLE_ID 未配置，无法校验 Apple token")
    try:
        signing_key = _get_apple_jwk_client().get_signing_key_from_jwt(identity_token)
        payload = jwt.decode(
            identity_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=bundle_id,
            issuer=settings.auth.apple_issuer,
            options={"require": ["sub", "exp", "iss", "aud"]},
        )
    except jwt.PyJWKClientError as exc:
        raise AuthError(f"apple jwks error: {exc}") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid apple identity token: {exc}") from exc

    sub = payload.get("sub")
    if not sub:
        raise AuthError("apple identity token missing sub")
    email = payload.get("email")
    # email_verified 可能是字符串 "true"；仅作展示用途，不强校验
    return AppleIdentity(apple_user_id=str(sub), email=email)


__all__ = [
    "AuthError",
    "AuthConfigError",
    "AppleIdentity",
    "issue_access_token",
    "decode_access_token",
    "verify_apple_identity_token",
]
