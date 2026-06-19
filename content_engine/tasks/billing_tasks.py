"""阶段 3.2：会员到期巡检 Celery 任务（懒降级的定时兜底）。

会员权益实时判定已由 deps.is_member 按 member_expire_at 处理（懒降级）；
本任务做定时兜底，把已过期用户的 ``User.member_tier`` 落库改回 ``free``、
把对应 ``Subscription.status`` 置 ``expired``，使按 tier 圈选人群（如推送分层、
运营统计）准确，不被"逻辑已过期但库里仍 member"的用户污染。

幂等：只改"tier=member 且已过期"的行，重复执行无副作用。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update

from content_engine.models import Subscription, SubscriptionStatus, User, get_session

from .celery_app import celery_app


def downgrade_expired() -> dict:
    """把已过期会员降级为 free，并把过期订阅置 expired。返回处理计数。"""
    now = datetime.now(timezone.utc)
    with get_session() as s:
        # 1) 过期会员降级（tier=member 且 expire_at 已过）
        expired_users = (
            s.execute(
                select(User).where(
                    User.member_tier == "member",
                    User.member_expire_at.is_not(None),
                    User.member_expire_at < now,
                )
            )
            .scalars()
            .all()
        )
        for u in expired_users:
            u.member_tier = "free"

        # 2) 过期订阅置 expired（active 且 expires_at 已过）
        result = s.execute(
            update(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.active.value,
                Subscription.expires_at.is_not(None),
                Subscription.expires_at < now,
            )
            .values(status=SubscriptionStatus.expired.value)
        )
        return {
            "downgraded_users": len(expired_users),
            "expired_subscriptions": result.rowcount or 0,
        }


@celery_app.task(name="content_engine.tasks.billing_tasks.downgrade_expired_members")
def downgrade_expired_members() -> dict:
    """beat 周期入口：会员到期巡检（懒降级的定时兜底）。"""
    return downgrade_expired()


__all__ = ["downgrade_expired", "downgrade_expired_members"]
