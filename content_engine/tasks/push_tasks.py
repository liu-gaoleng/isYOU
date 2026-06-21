"""阶段 4.2：APNs 推送 Celery 任务（每日早报 dispatcher）。

调度策略：
- ``dispatch_daily_briefs`` 由 beat 每分钟触发；按当前 UTC ``HH:MM`` 匹配
  ``push_setting.push_time``，覆盖任意用户设定的时间点而无需多时区扇出。
- 每分钟最多产生一条 :class:`PushRecord` 审计行（``biz_id="daily-<YYYYMMDD-HHMM>"``
  唯一约束兜底重跑幂等）。

降级路径（铁律「不杜撰、可降级」）：
- APNs 凭据未配置（``settings.apns.configured == False``）→ 跳过实际下发但
  仍写一条 ``sent=0`` 的 PushRecord，便于灰度环境单测继续跑；
- 单 token 失效（HTTP 410 / Unregistered）→ 回写 ``device_tokens.invalid_at``
  软删，后续 dispatcher 自动跳过；
- 单 token 其它错误（5xx / 超时）→ 仅记日志，不重试也不抛出（不影响其它用户）。
"""

from __future__ import annotations

from datetime import datetime, time, timezone

from sqlalchemy import desc, select

from content_engine.logging_config import get_logger
from content_engine.models import (
    DeviceToken,
    Event,
    EventStatus,
    PushRecord,
    PushSetting,
    get_session,
)
from content_engine.services.apns import (
    ApnsBadTokenError,
    ApnsClient,
    ApnsConfigError,
    ApnsError,
    build_payload,
)

from .celery_app import celery_app

_logger = get_logger(__name__)

# 与 brief router 一致：仅"已生成可读内容"的事件可推
_VISIBLE_STATUSES = (
    EventStatus.summarized,
    EventStatus.scored,
    EventStatus.published,
)


def _today_window(now: datetime) -> tuple[datetime, datetime]:
    """以 ``now`` 的日期为锚的当日 UTC 窗口（含端点）。"""
    today = now.date()
    return (
        datetime.combine(today, time.min, tzinfo=timezone.utc),
        datetime.combine(today, time.max, tzinfo=timezone.utc),
    )


def _today_top_event(session, now: datetime) -> tuple[Event | None, int]:
    """取当日 importance 最高的事件 + 当日可见事件总数。"""
    day_start, day_end = _today_window(now)
    visible = (
        select(Event)
        .where(Event.status.in_(_VISIBLE_STATUSES))
        .where(Event.last_update >= day_start)
        .where(Event.last_update <= day_end)
    )
    rows = (
        session.execute(visible.order_by(desc(Event.importance), desc(Event.last_update)))
        .scalars()
        .all()
    )
    if not rows:
        return None, 0
    return rows[0], len(rows)


def dispatch_daily_briefs(now: datetime | None = None) -> dict:
    """每分钟入口：按 ``push_time=HH:MM`` 命中的用户下发当日早报。

    返回处理摘要（matched_users / sent / invalidated / configured / skipped_no_brief）。
    """
    now = now or datetime.now(timezone.utc)
    hhmm = now.strftime("%H:%M")
    biz_id = f"daily-{now.strftime('%Y%m%d-%H%M')}"
    summary: dict = {
        "matched_users": 0,
        "sent": 0,
        "invalidated": 0,
        "configured": False,
        "skipped_no_brief": False,
        "biz_id": biz_id,
    }

    with get_session() as s:
        # 1) 同 biz_id 已分发过 → 幂等返回（防止 beat 重投或本地手动重跑双重下发）
        existed = s.execute(
            select(PushRecord).where(PushRecord.biz_id == biz_id)
        ).scalar_one_or_none()
        if existed is not None:
            summary["already_done"] = True
            summary["sent"] = existed.sent
            return summary

        # 2) 拉当前分钟应下发的 push_settings
        push_rows = (
            s.execute(
                select(PushSetting).where(
                    PushSetting.daily_push.is_(True),
                    PushSetting.push_time == hhmm,
                )
            )
            .scalars()
            .all()
        )
        summary["matched_users"] = len(push_rows)
        if not push_rows:
            return summary

        # 3) 当日 top1 事件 + 总数（用于推文 body）
        top, total = _today_top_event(s, now)
        if top is None:
            summary["skipped_no_brief"] = True
            # 仍记一行 PushRecord 审计，便于查"早 8 点空跑"原因
            s.add(
                PushRecord(
                    biz_id=biz_id,
                    type="daily",
                    title="今日早报",
                    audience="all",
                    pushed_at=now,
                    sent=0,
                    event_ids=[],
                )
            )
            return summary

        title = "今日早报"
        body = f"{total} 条要闻已就位，点开查看"
        custom = {
            "event_id": top.id,
            "kind": "daily_brief",
            "date": now.date().isoformat(),
        }
        collapse_id = f"daily-{now.date().isoformat()}"

        # 4) 构造 APNs 客户端（凭证缺失则降级为干运行）
        client: ApnsClient | None
        try:
            client = ApnsClient.from_settings()
            summary["configured"] = True
        except ApnsConfigError as e:
            _logger.warning("[push] APNs 干运行（凭据未配置）：%s", e)
            client = None

        # 5) 遍历用户 → 取活跃 token → 单条下发
        sent = 0
        invalidated = 0
        try:
            for ps in push_rows:
                tokens = (
                    s.execute(
                        select(DeviceToken).where(
                            DeviceToken.user_id == ps.user_id,
                            DeviceToken.invalid_at.is_(None),
                        )
                    )
                    .scalars()
                    .all()
                )
                for dt in tokens:
                    if client is None:
                        continue
                    try:
                        client.send(
                            token=dt.token,
                            payload=build_payload(title=title, body=body, custom=custom),
                            collapse_id=collapse_id,
                        )
                        sent += 1
                    except ApnsBadTokenError as e:
                        dt.invalid_at = now
                        invalidated += 1
                        _logger.info(
                            "[push] token 失效已软删: user=%s reason=%s",
                            ps.user_id,
                            e.reason,
                        )
                    except ApnsError as e:
                        _logger.warning(
                            "[push] 单条下发失败（已跳过）: user=%s status=%s reason=%s",
                            ps.user_id,
                            e.status_code,
                            e.reason,
                        )
        finally:
            if client is not None:
                client.close()

        s.add(
            PushRecord(
                biz_id=biz_id,
                event_ref=str(top.id),
                type="daily",
                title=title,
                audience="all",
                pushed_at=now,
                sent=sent,
                event_ids=[top.id],
            )
        )
        summary["sent"] = sent
        summary["invalidated"] = invalidated
        return summary


@celery_app.task(name="content_engine.tasks.push_tasks.dispatch_daily_briefs")
def dispatch_daily_briefs_task() -> dict:
    """beat 入口：每分钟运行 :func:`dispatch_daily_briefs`。"""
    return dispatch_daily_briefs()


__all__ = ["dispatch_daily_briefs", "dispatch_daily_briefs_task"]
