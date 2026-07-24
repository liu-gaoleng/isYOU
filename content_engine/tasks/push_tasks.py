"""阶段 4.2：APNs 推送 Celery 任务（每日早报 dispatcher）。

调度策略（时区感知）：
- ``dispatch_daily_briefs`` 由 beat 每分钟触发；拉取全部开启每日推送的设置，
  按 ``push_settings.tz`` 分组，用「该时区当前的本地 HH:MM」匹配 ``push_time``
  ——北京用户设 08:00 就在北京时间 08:00 推，而非 UTC 08:00（= 北京 16:00）。
- 每个 (时区, 本地日期, HHMM) 组产生一条 :class:`PushRecord` 审计行
  （``biz_id="daily-<tz>-<YYYYMMDD>-<HHMM>"``，唯一约束兜底重跑幂等）。
- 简报内容窗口按「该时区的本地自然日」取 UTC 边界——早 8 点（北京）派发时
  窗口是北京的今天，而非刚开始了 0 分钟的 UTC 当天（否则大面积空跑）。

降级路径（铁律「不杜撰、可降级」）：
- APNs 凭据未配置（``settings.apns.configured == False``）→ 跳过实际下发但
  仍写一条 ``sent=0`` 的 PushRecord，便于灰度环境单测继续跑；
- 发送按 ``device_tokens.environment`` 经 :class:`ApnsClientPool` 分流到
  sandbox / production 主机，避免生产后端误杀 sandbox token；
- 单 token 失效（HTTP 410 / Unregistered）→ 回写 ``device_tokens.invalid_at``
  软删，后续 dispatcher 自动跳过；
- 单 token 其它错误（5xx / 传输层）→ 仅记日志，不重试也不抛出（不影响其它用户）。
"""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import desc, func, select

from content_engine.logging_config import get_logger
from content_engine.models import (
    DEFAULT_PUSH_TZ,
    PUBLIC_EVENT_STATUSES,
    DeviceToken,
    Event,
    PushRecord,
    PushSetting,
    get_session,
)
from content_engine.services.apns import (
    ApnsBadTokenError,
    ApnsClientPool,
    ApnsConfigError,
    ApnsError,
    build_payload,
)

from .celery_app import celery_app

_logger = get_logger(__name__)

# 与 brief router 一致：仅已过护栏发布的事件可推
_VISIBLE_STATUSES = PUBLIC_EVENT_STATUSES


def _safe_tz(tz_name: str | None) -> str:
    """取合法 IANA 时区名；NULL/非法值按 DEFAULT_PUSH_TZ 兜底。"""
    if not tz_name:
        return DEFAULT_PUSH_TZ
    try:
        ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return DEFAULT_PUSH_TZ
    return tz_name


def _local_day_window(tz_name: str, now: datetime) -> tuple[datetime, datetime]:
    """以 ``tz_name`` 时区的本地自然日为锚的当日窗口（UTC 边界，含端点）。"""
    tz = ZoneInfo(tz_name)
    local_today = now.astimezone(tz).date()
    start = datetime.combine(local_today, time.min, tzinfo=tz)
    end = datetime.combine(local_today, time.max, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _today_top_event(
    session, window_start: datetime, window_end: datetime
) -> tuple[Event | None, int]:
    """取窗口内 importance 最高的事件 + 窗口内可见事件总数。"""
    visible = (
        select(Event)
        .where(Event.status.in_(_VISIBLE_STATUSES))
        .where(Event.last_update >= window_start)
        .where(Event.last_update <= window_end)
    )
    total = session.execute(
        select(func.count()).select_from(visible.subquery())
    ).scalar_one()
    top = (
        session.execute(
            visible.order_by(desc(Event.importance), desc(Event.last_update)).limit(1)
        )
        .scalars()
        .first()
    )
    return top, total


def dispatch_daily_briefs(now: datetime | None = None) -> dict:
    """每分钟入口：按各时区本地 ``HH:MM`` 命中的用户下发当日早报。

    返回处理摘要（matched_users / sent / invalidated / configured /
    skipped_no_brief / already_done）。
    """
    now = now or datetime.now(timezone.utc)
    summary: dict = {
        "matched_users": 0,
        "sent": 0,
        "invalidated": 0,
        "configured": False,
        "skipped_no_brief": False,
    }

    with get_session() as s:
        # 1) 拉全部开启每日推送的设置，按时区分组、用该时区本地 HH:MM 匹配
        rows = (
            s.execute(select(PushSetting).where(PushSetting.daily_push.is_(True)))
            .scalars()
            .all()
        )
        groups: dict[str, list[PushSetting]] = {}
        for ps in rows:
            tz = _safe_tz(ps.tz)
            local_hhmm = now.astimezone(ZoneInfo(tz)).strftime("%H:%M")
            if ps.push_time == local_hhmm:
                groups.setdefault(tz, []).append(ps)
        summary["matched_users"] = sum(len(m) for m in groups.values())
        if not groups:
            return summary

        # 2) 构造 APNs 客户端池（凭证缺失则降级为干运行）
        pool: ApnsClientPool | None
        try:
            pool = ApnsClientPool.from_settings()
            summary["configured"] = True
        except ApnsConfigError as e:
            _logger.warning("[push] APNs 干运行（凭据未配置）：%s", e)
            pool = None

        # 3) 逐时区分组派发
        try:
            for tz, members in groups.items():
                _dispatch_group(s, pool=pool, tz=tz, members=members, now=now, summary=summary)
        finally:
            if pool is not None:
                pool.close()
        return summary


def _dispatch_group(
    session,
    *,
    pool: ApnsClientPool | None,
    tz: str,
    members: list[PushSetting],
    now: datetime,
    summary: dict,
) -> None:
    """派发一个 (时区, 本地日期, HHMM) 分组：内容窗口与幂等键都按该时区本地日。"""
    local_now = now.astimezone(ZoneInfo(tz))
    hhmm = local_now.strftime("%H%M")
    biz_id = f"daily-{tz.replace('/', '-')}-{local_now.strftime('%Y%m%d')}-{hhmm}"

    # 同 biz_id 已分发过 → 幂等返回（防止 beat 重投或本地手动重跑双重下发）
    existed = session.execute(
        select(PushRecord).where(PushRecord.biz_id == biz_id)
    ).scalar_one_or_none()
    if existed is not None:
        summary["already_done"] = True
        summary["sent"] += existed.sent
        return

    # 该时区本地自然日窗口内的 top1 事件 + 总数（用于推文 body）
    window_start, window_end = _local_day_window(tz, now)
    top, total = _today_top_event(session, window_start, window_end)
    if top is None:
        summary["skipped_no_brief"] = True
        # 仍记一行 PushRecord 审计，便于查"早 8 点空跑"原因
        session.add(
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
        return

    title = "今日早报"
    body = f"{total} 条要闻已就位，点开查看"
    custom = {
        "event_id": top.id,
        "kind": "daily_brief",
        "date": local_now.date().isoformat(),
    }
    collapse_id = f"daily-{local_now.date().isoformat()}"

    sent = 0
    invalidated = 0
    for ps in members:
        tokens = (
            session.execute(
                select(DeviceToken).where(
                    DeviceToken.user_id == ps.user_id,
                    DeviceToken.invalid_at.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for dt in tokens:
            if pool is None:
                continue
            # 按 token 上报的 environment 分流到对应 APNs 主机
            client = pool.client_for(dt.environment)
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
                # 含传输层错误（transport_error）：单 token 失败不影响整批
                _logger.warning(
                    "[push] 单条下发失败（已跳过）: user=%s status=%s reason=%s",
                    ps.user_id,
                    e.status_code,
                    e.reason,
                )

    session.add(
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
    summary["sent"] += sent
    summary["invalidated"] += invalidated


@celery_app.task(name="content_engine.tasks.push_tasks.dispatch_daily_briefs")
def dispatch_daily_briefs_task() -> dict:
    """beat 入口：每分钟运行 :func:`dispatch_daily_briefs`。"""
    return dispatch_daily_briefs()


__all__ = ["dispatch_daily_briefs", "dispatch_daily_briefs_task"]
