"""阶段 3.1：C 端账号鉴权接口（Sign in with Apple + 本地 JWT）。

端点（挂在 /api/v1/auth 前缀下）：
- POST /auth/apple      Sign in with Apple：验签 identityToken → upsert User → 签发本地 JWT
- POST /auth/dev-login  dev 测试登录（仅本地联调，受 RD_AUTH_DEV_LOGIN_ENABLED 开关）
- GET  /auth/me         返回当前登录用户信息

登录幂等：以 Apple sub（apple_user_id）做唯一键 upsert，重复登录返回同一用户。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import User, get_session
from content_engine.services import auth as auth_service

from ..deps import get_current_user, is_member
from ..schemas import (
    AppleLoginRequest,
    DevLoginRequest,
    LoginResponse,
    UserProfile,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        created_via=user.created_via,
        member_tier=user.member_tier,
        is_member=is_member(user),
        member_expire_at=user.member_expire_at,
    )


def _upsert_apple_user(
    apple_user_id: str,
    email: str | None,
    display_name: str | None,
    created_via: str = "apple",
) -> User:
    """按 apple_user_id upsert 用户：存在则按需补全 email/昵称，不存在则新建。"""
    with get_session() as s:
        user = s.execute(
            select(User).where(User.apple_user_id == apple_user_id)
        ).scalar_one_or_none()
        if user is None:
            user = User(
                apple_user_id=apple_user_id,
                email=email,
                display_name=display_name,
                created_via=created_via,
            )
            s.add(user)
            s.flush()
        else:
            # Apple 仅首次登录返回邮箱/昵称，后续为空；已有值不覆盖，缺失则补
            if email and not user.email:
                user.email = email
            if display_name and not user.display_name:
                user.display_name = display_name
        s.refresh(user)
        s.expunge(user)
        return user


def _login_response(user: User) -> LoginResponse:
    try:
        token, expires_in = auth_service.issue_access_token(user.id)
    except auth_service.AuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return LoginResponse(access_token=token, expires_in=expires_in, user=_profile(user))


@router.post("/apple", response_model=LoginResponse)
def login_with_apple(req: AppleLoginRequest) -> LoginResponse:
    """Sign in with Apple：验签 identityToken，登录或注册，签发本地 access token。"""
    try:
        identity = auth_service.verify_apple_identity_token(req.identity_token)
    except auth_service.AuthConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    user = _upsert_apple_user(
        apple_user_id=identity.apple_user_id,
        email=identity.email,
        display_name=req.display_name,
        created_via="apple",
    )
    return _login_response(user)


@router.post("/dev-login", response_model=LoginResponse)
def dev_login(req: DevLoginRequest) -> LoginResponse:
    """dev 测试登录：不经 Apple 直接签发 JWT。仅本地联调，生产务必关闭开关。"""
    if not settings.auth.dev_login_enabled:
        raise HTTPException(status_code=403, detail="dev login disabled")

    user = _upsert_apple_user(
        apple_user_id=req.apple_user_id,
        email=req.email,
        display_name=req.display_name,
        created_via="test",
    )
    if req.as_member:
        # 联调便利：置为远期到期的会员，方便验证付费墙解锁态
        from datetime import datetime, timedelta, timezone

        with get_session() as s:
            db_user = s.get(User, user.id)
            db_user.member_tier = "member"
            db_user.member_expire_at = datetime.now(timezone.utc) + timedelta(days=365)
            s.flush()
            s.refresh(db_user)
            s.expunge(db_user)
            user = db_user
    return _login_response(user)


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(get_current_user)) -> UserProfile:
    """返回当前登录用户信息。"""
    return _profile(user)
