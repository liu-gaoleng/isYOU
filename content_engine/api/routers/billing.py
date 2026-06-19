"""阶段 3.2：会员订阅 / Apple IAP（StoreKit 2）收据校验接口。

端点（挂在 /api/v1 前缀下）：
- GET  /billing/plans            订阅档位列表（product_id + 周期，供客户端展示/匹配商品）
- POST /billing/verify          校验 StoreKit2 JWSTransaction → 核销 → 升级/续期会员
- POST /billing/restore         恢复购买：批量校验多笔交易，取最新有效者核销

核销（redeem）幂等：按 Apple transaction_id 去重，重复上送同一交易不重复记账；
会员权益落在 User.member_tier / member_expire_at（与 deps.is_member 一致），
订阅汇总态落 subscriptions，交易审计落 iap_transactions。
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from content_engine.config import settings
from content_engine.models import (
    IapTransaction,
    Subscription,
    SubscriptionStatus,
    User,
    get_session,
)
from content_engine.services import storekit

from ..deps import get_current_user, is_member
from ..schemas import (
    MembershipStatus,
    PlanItem,
    VerifyReceiptRequest,
)

router = APIRouter(prefix="/billing", tags=["billing"])

# 各档周期（天）：仅用于展示与到期兜底；权威到期时间以 Apple expiresDate 为准
_PLAN_PERIOD_DAYS = {
    "monthly": 30,
    "quarterly": 90,
    "yearly": 365,
}


def _plans() -> list[PlanItem]:
    b = settings.billing
    product_by_plan = {
        "monthly": b.product_monthly,
        "quarterly": b.product_quarterly,
        "yearly": b.product_yearly,
    }
    return [
        PlanItem(plan=plan, product_id=product_by_plan[plan], period_days=days)
        for plan, days in _PLAN_PERIOD_DAYS.items()
    ]


def _membership_view(user: User, sub: Subscription | None) -> MembershipStatus:
    return MembershipStatus(
        is_member=is_member(user),
        member_tier=user.member_tier,
        member_expire_at=user.member_expire_at,
        plan=sub.plan if sub else None,
        auto_renew=sub.auto_renew if sub else False,
        subscription_status=sub.status if sub else None,
    )


def _redeem(session, user: User, vt: storekit.VerifiedTransaction) -> tuple[User, Subscription]:
    """核销一笔已验签交易：写交易审计 + upsert 订阅 + 升级/续期会员态。

    幂等：同 transaction_id 已记则不重复 insert；会员到期时间取 Apple expiresDate。
    退款/撤销交易（is_revoked）不授予会员。
    """
    status = SubscriptionStatus.refunded if vt.is_revoked else SubscriptionStatus.active
    now = datetime.now(timezone.utc)
    expires = vt.expires_date
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)

    # 1) 交易审计（按 transaction_id 去重）
    existing_tx = session.execute(
        select(IapTransaction).where(
            IapTransaction.transaction_id == vt.transaction_id
        )
    ).scalar_one_or_none()
    if existing_tx is None:
        session.add(
            IapTransaction(
                transaction_id=vt.transaction_id,
                original_transaction_id=vt.original_transaction_id,
                user_id=user.id,
                product_id=vt.product_id,
                plan=vt.plan.value,
                environment=vt.environment,
                purchase_date=vt.purchase_date,
                expires_date=expires,
                status=status.value,
                raw_payload=vt.raw_payload,
            )
        )

    # 2) upsert 订阅汇总态
    sub = session.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    ).scalar_one_or_none()
    if sub is None:
        sub = Subscription(user_id=user.id)
        session.add(sub)
    sub.original_transaction_id = vt.original_transaction_id
    sub.last_transaction_id = vt.transaction_id
    sub.product_id = vt.product_id
    sub.plan = vt.plan.value
    sub.status = status.value
    sub.environment = vt.environment
    sub.purchased_at = vt.purchase_date
    sub.expires_at = expires

    # 3) 会员态：未退款且未过期 → 升级为 member；否则维持/降级
    user_row = session.get(User, user.id)
    is_active = (
        status == SubscriptionStatus.active
        and expires is not None
        and expires > now
    )
    if is_active:
        user_row.member_tier = "member"
        user_row.member_expire_at = expires
    else:
        # 退款或已过期：降级（到期巡检也会兜底处理）
        user_row.member_tier = "free"
        if status == SubscriptionStatus.refunded:
            user_row.member_expire_at = None
    session.flush()
    session.refresh(user_row)
    session.refresh(sub)
    # 解绑出会话，供响应序列化
    session.expunge(user_row)
    session.expunge(sub)
    return user_row, sub


@router.get("/plans", response_model=list[PlanItem])
def list_plans() -> list[PlanItem]:
    """订阅档位列表（无需登录，供客户端展示与商品匹配）。"""
    return _plans()


@router.post("/verify", response_model=MembershipStatus)
def verify_receipt(
    req: VerifyReceiptRequest, user: User = Depends(get_current_user)
) -> MembershipStatus:
    """校验并核销一笔 StoreKit2 交易，返回最新会员态。"""
    try:
        vt = storekit.verify_signed_transaction(req.signed_transaction)
    except storekit.BillingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except storekit.ReceiptError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    with get_session() as s:
        user_row, sub = _redeem(s, user, vt)
    return _membership_view(user_row, sub)


@router.post("/restore", response_model=MembershipStatus)
def restore_purchases(
    payloads: list[VerifyReceiptRequest], user: User = Depends(get_current_user)
) -> MembershipStatus:
    """恢复购买：校验多笔交易，取到期时间最晚的有效交易核销。"""
    if not payloads:
        raise HTTPException(status_code=400, detail="no transactions provided")

    verified: list[storekit.VerifiedTransaction] = []
    for p in payloads:
        try:
            verified.append(storekit.verify_signed_transaction(p.signed_transaction))
        except storekit.BillingConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except storekit.ReceiptError:
            # 单笔无效跳过，不阻断整体恢复
            continue

    if not verified:
        raise HTTPException(status_code=400, detail="no valid transactions")

    # 取到期最晚且未撤销的一笔；全撤销则取最后一笔以正确反映退款态
    active = [v for v in verified if not v.is_revoked and v.expires_date is not None]
    chosen = (
        max(active, key=lambda v: v.expires_date)
        if active
        else max(verified, key=lambda v: v.purchase_date or datetime.min.replace(tzinfo=timezone.utc))
    )

    with get_session() as s:
        user_row, sub = _redeem(s, user, chosen)
    return _membership_view(user_row, sub)
