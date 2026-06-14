"""API 共享依赖：当前登录用户解析（阶段 3.1）。

- :func:`get_current_user`：强制登录，缺/错 token → 401；
- :func:`get_optional_user`：可选登录，无 token 返回 None（付费墙按此判定会员态）。

会员判定唯一依据：``User.member_tier == "member"`` 且 ``member_expire_at`` 未过期。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException
from fastapi.security.utils import get_authorization_scheme_param

from content_engine.models import User, get_session
from content_engine.services import auth as auth_service


def is_member(user: User | None) -> bool:
    """会员态判定：tier=member 且未过期。"""
    if user is None or user.member_tier != "member":
        return False
    expire = user.member_expire_at
    if expire is None:
        return False
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=timezone.utc)
    return expire > datetime.now(timezone.utc)


def _user_id_from_header(authorization: str | None) -> int | None:
    """从 Authorization: Bearer <jwt> 解出 user_id；无 token 返回 None。"""
    if not authorization:
        return None
    scheme, token = get_authorization_scheme_param(authorization)
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    try:
        return auth_service.decode_access_token(token)
    except auth_service.AuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_optional_user(authorization: str | None = Header(default=None)) -> User | None:
    """可选登录：无 token → None；有 token 但无效 → 401。"""
    user_id = _user_id_from_header(authorization)
    if user_id is None:
        return None
    with get_session() as s:
        return s.get(User, user_id)


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    """强制登录：未登录 / 用户不存在 → 401。"""
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


__all__ = ["is_member", "get_optional_user", "get_current_user"]
