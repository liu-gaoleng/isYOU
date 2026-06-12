"""阶段 4.4：mock_server 真实数据适配层。

职责（对齐「改读 DB + 适配层 / 带 output.json 降级」决策）：
1. **事件类数据**：优先从 content_engine 真实 DB 读取 Event/EventContent，
   映射成 mock_server 既有的事件 dict 形态；DB 不可用或空库时**自动降级**读
   ``pipeline_demo/output.json``（铁律：可降级，mock 任何环境都能起）。
2. **运营态数据**：app_users / orders / reports / report_purchases / push_records /
   digest_config / admin_members / favorites / reading_history 落到真实 DB；
   首次运行若表空则用内置默认 seed 写入；DB 不可用时退回纯内存 seed。
3. **写回**：CMS 事件动作（approve/reject/publish/pin/edit/merge）与运营态变更
   尽力写回 DB（DB 不可用时 no-op，仅作用于 mock 内存态）。

说明：sources（信源库）与 RBAC 角色权限矩阵仍由 server.py 内常量维护（不在本轮
运营态表范围内），其增删改保持内存态。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "..", "pipeline_demo", "output.json")

MODULE_EN2CN = {"tech": "科技", "finance": "金融", "ai": "AI", "macro": "宏观"}
MODULE_CN2EN = {v: k for k, v in MODULE_EN2CN.items()}

# C 端可见状态映射：真实 EventStatus → mock status
# 未发布的中间态（clustered/summarized/scored）在 CMS 视为待审核
_STATUS_MAP = {
    "published": "published",
    "reviewing": "reviewing",
    "rejected": "rejected",
    "clustered": "reviewing",
    "summarized": "reviewing",
    "scored": "reviewing",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------
# DB 可用性探测（导入失败 / 连接失败都降级）
# ----------------------------------------------------------------------------
def _detect_db() -> bool:
    if os.getenv("MOCK_FORCE_JSON") == "1":
        return False
    try:
        from sqlalchemy import text

        from content_engine.models import get_session

        with get_session() as s:
            s.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001 —— 任何异常都降级
        print(f"[db_source] DB 不可用，降级 output.json：{exc}")
        return False


DB_ENABLED = _detect_db()


# ============================================================================
# 事件类数据
# ============================================================================
def _deep_content_fallback(title: str) -> str:
    return (
        f"【深度解读】围绕「{title[:20]}」，从行业格局、关键玩家、"
        "对投资人与创业者的影响三个维度展开分析……（此处为 Mock 全文，"
        "生产环境由大模型生成付费深度内容）"
    )


def _event_to_dict(ev, content) -> dict:
    """把真实 Event(+latest EventContent) 映射成 mock 事件 dict。"""
    module_en = ev.module.value
    module_cn = MODULE_EN2CN.get(module_en, "科技")
    importance = float(ev.importance or 0.0)
    title = content.title if content else ""
    summary = list(content.summary) if (content and content.summary) else []
    why_matters = (content.why_matters if content else "") or ""
    sources = list(content.sources) if (content and content.sources) else []
    deep = (content.deep_content if content else None) or _deep_content_fallback(title)
    last_update = ev.last_update or datetime.now(timezone.utc)
    return {
        "event_id": f"evt_{ev.id}",
        "db_id": ev.id,
        "module_cn": module_cn,
        "module": module_en,
        "title": title,
        "summary": summary,
        "why_matters": why_matters,
        "sources": sources,
        "source_count": ev.source_count,
        "importance": importance,
        "hotness": int((ev.hotness or 0.0) * 10000),
        "published_at": last_update.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": _STATUS_MAP.get(ev.status.value, "reviewing"),
        # pinned 由 importance 派生（与 review.py pin→importance=100 对齐）
        "pinned": importance >= 100.0,
        "pushed": False,  # 由 push_records 回填
        "updated_at": last_update.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "disclaimer": "本文内容由 AI 辅助生成，不构成投资建议"
        if module_cn in ("金融", "宏观")
        else "",
        "deep_content_full": deep,
    }


def _load_events_from_db() -> list[dict]:
    from content_engine.models import Event, get_session

    events: list[dict] = []
    with get_session() as s:
        rows = s.query(Event).order_by(Event.importance.desc()).all()
        pushed_refs = _pushed_event_refs(s)
        for ev in rows:
            content = max(ev.contents, key=lambda c: c.version) if ev.contents else None
            d = _event_to_dict(ev, content)
            if d["event_id"] in pushed_refs or str(ev.id) in pushed_refs:
                d["pushed"] = True
            events.append(d)
    return events


def _pushed_event_refs(s) -> set[str]:
    try:
        from content_engine.models import PushRecord

        refs = set()
        for p in s.query(PushRecord.event_ref).all():
            if p[0]:
                refs.add(str(p[0]))
        return refs
    except Exception:  # noqa: BLE001
        return set()


def _load_events_from_json() -> list[dict]:
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[db_source] 未找到 {DATA_FILE}，事件列表为空")
        raw = []
    events = []
    raw.sort(key=lambda x: x.get("importance", 0), reverse=True)
    for i, e in enumerate(raw):
        module_cn = e.get("module", "科技")
        eid = f"evt_{1000 + i}"
        importance = e.get("importance", 50.0)
        title = e.get("title", "")
        events.append({
            "event_id": eid,
            "db_id": None,
            "module_cn": module_cn,
            "module": MODULE_CN2EN.get(module_cn, "tech"),
            "title": title,
            "summary": e.get("summary", []),
            "why_matters": e.get("why_matters", ""),
            "sources": e.get("sources", []),
            "source_count": e.get("source_count", 1),
            "importance": importance,
            "hotness": int(importance * 10000),
            "published_at": "2026-06-08T08:00:00Z",
            "status": "reviewing" if i % 3 == 0 else "published",
            "pinned": False,
            "pushed": False,
            "updated_at": "2026-06-08T08:00:00Z",
            "disclaimer": "本文内容由 AI 辅助生成，不构成投资建议"
            if module_cn in ("金融", "宏观") else "",
            "deep_content_full": _deep_content_fallback(title),
        })
    return events


def load_events() -> list[dict]:
    """事件列表：DB 优先，空库或异常降级 output.json。"""
    if DB_ENABLED:
        try:
            db_events = _load_events_from_db()
            if db_events:
                print(f"[db_source] 已从 DB 加载 {len(db_events)} 个事件")
                return db_events
            print("[db_source] DB 事件为空，降级 output.json")
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] 读取 DB 事件失败，降级 output.json：{exc}")
    return _load_events_from_json()


def _parse_db_id(evt_id: str) -> int | None:
    """从 mock event_id（evt_<int>）解析真实 DB id；非真实事件返回 None。"""
    if not evt_id or not evt_id.startswith("evt_"):
        return None
    suffix = evt_id[4:]
    if not suffix.isdigit():
        return None
    val = int(suffix)
    # output.json 降级态用 evt_1000+i，非真实 DB id；DB 态 id 一般远小于 1000
    return val if DB_ENABLED else None


def _event_int(evt_id: str) -> int | None:
    """提取 evt_<int> 的整数部分（不判断是否真实 DB id），用于收藏/历史落库。"""
    if not evt_id or not evt_id.startswith("evt_"):
        return None
    suffix = evt_id[4:]
    return int(suffix) if suffix.isdigit() else None


def persist_event_status(evt_id: str, status: str, reviewer: str, note: str | None) -> None:
    """写回事件状态变更 + ReviewLog（best-effort）。"""
    db_id = _parse_db_id(evt_id)
    if db_id is None:
        return
    try:
        from content_engine.models import Event, EventStatus, ReviewLog, get_session

        with get_session() as s:
            ev = s.get(Event, db_id)
            if ev is None:
                return
            before = {"status": ev.status.value}
            ev.status = EventStatus(status)
            s.add(ReviewLog(
                event_id=db_id, reviewer=reviewer or "mock",
                action=status, before=before, after={"status": status}, note=note,
            ))
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_event_status 失败：{exc}")


def persist_event_pin(evt_id: str, pinned: bool, reviewer: str) -> None:
    db_id = _parse_db_id(evt_id)
    if db_id is None:
        return
    try:
        from content_engine.models import Event, ReviewLog, get_session

        with get_session() as s:
            ev = s.get(Event, db_id)
            if ev is None:
                return
            before = {"importance": ev.importance}
            ev.importance = 100.0 if pinned else min(ev.importance, 99.0)
            s.add(ReviewLog(
                event_id=db_id, reviewer=reviewer or "mock",
                action="pin" if pinned else "unpin",
                before=before, after={"importance": ev.importance}, note=None,
            ))
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_event_pin 失败：{exc}")


def persist_event_edit(evt_id: str, *, title=None, summary=None, why_matters=None) -> None:
    db_id = _parse_db_id(evt_id)
    if db_id is None:
        return
    try:
        from content_engine.models import Event, get_session

        with get_session() as s:
            ev = s.get(Event, db_id)
            if ev is None or not ev.contents:
                return
            content = max(ev.contents, key=lambda c: c.version)
            if title is not None:
                content.title = title
            if summary is not None:
                content.summary = summary
                ev.card_summary = (summary[0] if summary else ev.card_summary)
                ev.detail_summary = "\n".join(summary) if summary else ev.detail_summary
            if why_matters is not None:
                content.why_matters = why_matters
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_event_edit 失败：{exc}")


# ============================================================================
# 运营态 seed 默认值（首次运行写入 DB；DB 不可用时作内存态）
# ============================================================================
_SEED_APP_USERS = [
    {"biz_id": "au_1", "phone": "138****8001", "nick": "投资老张", "tier": "member",
     "status": "active", "registered_at": "2026-03-02T10:00:00Z",
     "member_expire": "2027-03-02", "total_paid": 298,
     "orders": [{"biz_id": "o_1001", "plan": "会员年卡", "amount": 298,
                 "paid_at": "2026-03-02T10:05:00Z"}]},
    {"biz_id": "au_2", "phone": "139****2046", "nick": "AI产品李", "tier": "member",
     "status": "active", "registered_at": "2026-04-11T09:30:00Z",
     "member_expire": "2026-07-11", "total_paid": 90,
     "orders": [{"biz_id": "o_1002", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-04-11T09:32:00Z"},
                {"biz_id": "o_1003", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-05-11T09:32:00Z"},
                {"biz_id": "o_1004", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-06-11T09:32:00Z"}]},
    {"biz_id": "au_3", "phone": "137****5588", "nick": "创业者王", "tier": "free",
     "status": "active", "registered_at": "2026-05-20T14:00:00Z",
     "member_expire": "", "total_paid": 0, "orders": []},
    {"biz_id": "au_4", "phone": "150****3322", "nick": "羊毛党", "tier": "free",
     "status": "banned", "registered_at": "2026-05-28T22:10:00Z",
     "member_expire": "", "total_paid": 0, "orders": []},
    {"biz_id": "au_5", "phone": "186****7799", "nick": "宏观研究员", "tier": "member",
     "status": "active", "registered_at": "2026-02-15T08:00:00Z",
     "member_expire": "2026-06-15", "total_paid": 328,
     "orders": [{"biz_id": "o_1005", "plan": "会员年卡", "amount": 298,
                 "paid_at": "2026-02-15T08:05:00Z"},
                {"biz_id": "o_1006", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-02-15T08:06:00Z"}]},
]

_SEED_REPORTS = [
    {"biz_id": "rpt_1", "title": "2026 AI 应用层投资地图", "module": "ai",
     "pages": 62, "price": 299, "member_free": False,
     "description": "62 页 · 含 200+ 标的数据库",
     "summary": "系统拆解 2026 年 AI 应用层的六大高增长方向，附 200+ 一二级标的数据库与估值对标。",
     "toc": ["应用层投资主线与时间窗口", "Agent / 多模态 / 垂直工作流三大赛道",
             "200+ 标的数据库与估值对标", "退出路径与风险提示"]},
    {"biz_id": "rpt_2", "title": "一级市场融资月报 · 5 月", "module": "finance",
     "pages": 38, "price": 99, "member_free": True,
     "description": "38 页 · 赛道热度与估值追踪",
     "summary": "5 月一级市场融资全景：按赛道统计金额、轮次分布与头部机构出手，附热度榜与估值变化。",
     "toc": ["5 月融资总览与同比", "热门赛道与代表案例", "活跃机构出手统计",
             "估值区间与下月展望"]},
    {"biz_id": "rpt_3", "title": "宏观季度展望：利率与流动性", "module": "macro",
     "pages": 45, "price": 199, "member_free": False,
     "description": "45 页 · 含数据看板访问权",
     "summary": "围绕利率路径与全球流动性，给出未来一季度宏观情景假设、资产配置含义与关键数据日历。",
     "toc": ["利率路径的三种情景", "全球流动性与汇率", "大类资产配置含义",
             "关键数据与事件日历"]},
]

_SEED_PURCHASES = {"guest": ["rpt_2", "rpt_3"]}

_SEED_PUSH = [
    {"biz_id": "push_seed03", "event_ref": "evt_1002", "type": "manual",
     "title": "美联储维持利率不变，鲍威尔释放年内降息信号",
     "audience": "all", "pushed_at": "2026-06-08T08:30:00Z",
     "sent": 10180, "opened": 2342, "event_ids": []},
    {"biz_id": "push_seed02", "event_ref": "evt_1005", "type": "daily",
     "title": "每日早报 · 6 月 7 日 | 科技 / 金融 / AI / 宏观",
     "audience": "all", "pushed_at": "2026-06-07T08:00:00Z",
     "sent": 9870, "opened": 2603, "event_ids": []},
    {"biz_id": "push_seed01", "event_ref": "evt_1001", "type": "manual",
     "title": "OpenAI 发布新一代模型，推理成本下降 80%",
     "audience": "member", "pushed_at": "2026-06-06T19:15:00Z",
     "sent": 2280, "opened": 821, "event_ids": []},
]

_SEED_DIGEST = {
    "enabled": True, "send_time": "08:00", "audience": "all",
    "modules": ["tech", "finance", "ai", "macro"], "top_n": 5,
    "title_template": "每日早报 · {date} | 今日 {count} 条要闻",
}

_SEED_MEMBERS = [
    {"biz_id": "u_1", "name": "陈管理", "role": "admin", "enabled": True},
    {"biz_id": "u_2", "name": "李审核", "role": "auditor", "enabled": True},
    {"biz_id": "u_3", "name": "王运营", "role": "operator", "enabled": True},
    {"biz_id": "u_4", "name": "访客demo", "role": "viewer", "enabled": False},
]

_SEED_FAVORITES = {"guest": ["evt_1001", "evt_1003", "evt_1006"]}
_SEED_HISTORY = {
    "guest": [
        {"event_id": "evt_1002", "viewed_at": "2026-06-09T21:12:00Z"},
        {"event_id": "evt_1004", "viewed_at": "2026-06-09T20:40:00Z"},
        {"event_id": "evt_1001", "viewed_at": "2026-06-09T08:15:00Z"},
    ],
}


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ensure_seeded() -> None:
    """首次运行：若运营态表为空，则写入默认 seed（仅 DB_ENABLED 时）。"""
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import (
            AdminMember,
            AppOrder,
            AppUser,
            DigestConfig,
            Favorite,
            PushRecord,
            ReadingHistory,
            Report,
            ReportPurchase,
            get_session,
        )

        with get_session() as s:
            if s.query(AppUser).count() == 0:
                for u in _SEED_APP_USERS:
                    user = AppUser(
                        biz_id=u["biz_id"], phone=u["phone"], nick=u["nick"],
                        tier=u["tier"], status=u["status"],
                        registered_at=_parse_dt(u["registered_at"]),
                        member_expire=u["member_expire"], total_paid=u["total_paid"],
                    )
                    s.add(user)
                    s.flush()
                    for o in u["orders"]:
                        s.add(AppOrder(
                            biz_id=o["biz_id"], user_id=user.id, plan=o["plan"],
                            amount=o["amount"], paid_at=_parse_dt(o["paid_at"]),
                        ))
            if s.query(Report).count() == 0:
                for r in _SEED_REPORTS:
                    s.add(Report(
                        biz_id=r["biz_id"], title=r["title"], module=r["module"],
                        pages=r["pages"], price=r["price"], member_free=r["member_free"],
                        description=r["description"], summary=r["summary"], toc=r["toc"],
                    ))
            if s.query(ReportPurchase).count() == 0:
                for token, ids in _SEED_PURCHASES.items():
                    for rid in ids:
                        s.add(ReportPurchase(token=token, report_biz_id=rid, amount=0))
            if s.query(PushRecord).count() == 0:
                for p in _SEED_PUSH:
                    s.add(PushRecord(
                        biz_id=p["biz_id"], event_ref=p["event_ref"], type=p["type"],
                        title=p["title"], audience=p["audience"],
                        pushed_at=_parse_dt(p["pushed_at"]),
                        sent=p["sent"], opened=p["opened"], event_ids=p["event_ids"],
                    ))
            if s.query(DigestConfig).count() == 0:
                s.add(DigestConfig(
                    enabled=_SEED_DIGEST["enabled"], send_time=_SEED_DIGEST["send_time"],
                    audience=_SEED_DIGEST["audience"], modules=_SEED_DIGEST["modules"],
                    top_n=_SEED_DIGEST["top_n"], title_template=_SEED_DIGEST["title_template"],
                ))
            if s.query(AdminMember).count() == 0:
                for m in _SEED_MEMBERS:
                    s.add(AdminMember(
                        biz_id=m["biz_id"], name=m["name"], role=m["role"],
                        enabled=m["enabled"],
                    ))
            if s.query(Favorite).count() == 0:
                for token, ids in _SEED_FAVORITES.items():
                    for eid in ids:
                        num = eid[4:]
                        if num.isdigit():
                            s.add(Favorite(token=token, event_id=int(num)))
            if s.query(ReadingHistory).count() == 0:
                for token, items in _SEED_HISTORY.items():
                    for h in items:
                        num = h["event_id"][4:]
                        if num.isdigit():
                            s.add(ReadingHistory(
                                token=token, event_id=int(num),
                                viewed_at=_parse_dt(h["viewed_at"]) or datetime.now(timezone.utc),
                            ))
        print("[db_source] 运营态表 seed 完成")
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] ensure_seeded 失败（忽略）：{exc}")


# ============================================================================
# 运营态访问器：返回 server.py 兼容的结构
# （DB 可用则读 DB，否则回退内置 seed —— 保证 mock 任何环境都能起）
# ============================================================================
def load_app_users() -> list[dict]:
    if DB_ENABLED:
        try:
            from content_engine.models import AppUser, get_session

            out = []
            with get_session() as s:
                for u in s.query(AppUser).order_by(AppUser.id).all():
                    out.append({
                        "id": u.biz_id, "phone": u.phone, "nick": u.nick,
                        "tier": u.tier, "status": u.status,
                        "registered_at": u.registered_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if u.registered_at else "",
                        "member_expire": u.member_expire, "total_paid": u.total_paid,
                        "orders": [
                            {"order_id": o.biz_id, "plan": o.plan, "amount": o.amount,
                             "paid_at": o.paid_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                             if o.paid_at else ""}
                            for o in sorted(u.orders, key=lambda x: x.id)
                        ],
                    })
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_app_users 失败，回退 seed：{exc}")
    # 内存态 seed（结构与 server.py 既有 APP_USERS 对齐）
    return [
        {"id": u["biz_id"], "phone": u["phone"], "nick": u["nick"], "tier": u["tier"],
         "status": u["status"], "registered_at": u["registered_at"],
         "member_expire": u["member_expire"], "total_paid": u["total_paid"],
         "orders": [{"order_id": o["biz_id"], "plan": o["plan"], "amount": o["amount"],
                     "paid_at": o["paid_at"]} for o in u["orders"]]}
        for u in _SEED_APP_USERS
    ]


def load_reports() -> list[dict]:
    if DB_ENABLED:
        try:
            from content_engine.models import Report, get_session

            out = []
            with get_session() as s:
                for r in s.query(Report).order_by(Report.id).all():
                    out.append({
                        "id": r.biz_id, "title": r.title, "module": r.module,
                        "pages": r.pages, "price": r.price, "member_free": r.member_free,
                        "desc": r.description, "summary": r.summary, "toc": list(r.toc or []),
                    })
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_reports 失败，回退 seed：{exc}")
    return [
        {"id": r["biz_id"], "title": r["title"], "module": r["module"], "pages": r["pages"],
         "price": r["price"], "member_free": r["member_free"], "desc": r["description"],
         "summary": r["summary"], "toc": r["toc"]}
        for r in _SEED_REPORTS
    ]


def load_purchases() -> dict[str, list[str]]:
    if DB_ENABLED:
        try:
            from content_engine.models import ReportPurchase, get_session

            out: dict[str, list[str]] = {}
            with get_session() as s:
                for p in s.query(ReportPurchase).all():
                    out.setdefault(p.token, []).append(p.report_biz_id)
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_purchases 失败，回退 seed：{exc}")
    return {k: list(v) for k, v in _SEED_PURCHASES.items()}


def load_push_history() -> list[dict]:
    if DB_ENABLED:
        try:
            from content_engine.models import PushRecord, get_session

            out = []
            with get_session() as s:
                rows = s.query(PushRecord).order_by(PushRecord.pushed_at.desc()).all()
                for p in rows:
                    out.append({
                        "push_id": p.biz_id, "event_id": p.event_ref, "type": p.type,
                        "title": p.title, "audience": p.audience,
                        "pushed_at": p.pushed_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if p.pushed_at else "",
                        "sent": p.sent, "opened": p.opened,
                    })
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_push_history 失败，回退 seed：{exc}")
    return [
        {"push_id": p["biz_id"], "event_id": p["event_ref"], "type": p["type"],
         "title": p["title"], "audience": p["audience"], "pushed_at": p["pushed_at"],
         "sent": p["sent"], "opened": p["opened"]}
        for p in _SEED_PUSH
    ]


def load_digest_config() -> dict:
    if DB_ENABLED:
        try:
            from content_engine.models import DigestConfig, get_session

            with get_session() as s:
                d = s.query(DigestConfig).first()
                if d:
                    return {"enabled": d.enabled, "send_time": d.send_time,
                            "audience": d.audience, "modules": list(d.modules or []),
                            "top_n": d.top_n, "title_template": d.title_template}
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_digest_config 失败，回退 seed：{exc}")
    return dict(_SEED_DIGEST)


def load_admin_members() -> list[dict]:
    if DB_ENABLED:
        try:
            from content_engine.models import AdminMember, get_session

            out = []
            with get_session() as s:
                for m in s.query(AdminMember).order_by(AdminMember.id).all():
                    out.append({
                        "id": m.biz_id, "name": m.name, "role": m.role,
                        "enabled": m.enabled,
                        "created_at": m.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
                        if m.created_at else "",
                    })
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_admin_members 失败，回退 seed：{exc}")
    return [
        {"id": m["biz_id"], "name": m["name"], "role": m["role"], "enabled": m["enabled"],
         "created_at": "2026-05-01T08:00:00Z"}
        for m in _SEED_MEMBERS
    ]


def load_favorites() -> dict[str, list[str]]:
    if DB_ENABLED:
        try:
            from content_engine.models import Favorite, get_session

            out: dict[str, list[str]] = {}
            with get_session() as s:
                for f in s.query(Favorite).order_by(Favorite.id.desc()).all():
                    out.setdefault(f.token, []).append(f"evt_{f.event_id}")
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_favorites 失败，回退 seed：{exc}")
    return {k: list(v) for k, v in _SEED_FAVORITES.items()}


def load_history() -> dict[str, list[dict]]:
    if DB_ENABLED:
        try:
            from content_engine.models import ReadingHistory, get_session

            out: dict[str, list[dict]] = {}
            with get_session() as s:
                rows = s.query(ReadingHistory).order_by(ReadingHistory.viewed_at.desc()).all()
                for h in rows:
                    out.setdefault(h.token, []).append({
                        "event_id": f"evt_{h.event_id}",
                        "viewed_at": h.viewed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
            if out:
                return out
        except Exception as exc:  # noqa: BLE001
            print(f"[db_source] load_history 失败，回退 seed：{exc}")
    return {k: [dict(i) for i in v] for k, v in _SEED_HISTORY.items()}


# ============================================================================
# 运营态写回：把后台/ C 端操作持久化到 DB（best-effort，DB 不可用时 no-op）
# ============================================================================
def persist_app_user(biz_id: str, *, status=None, tier=None, member_expire=None) -> None:
    """C 端用户运营动作落库（ban/unban/set_tier/extend 后由 server 传最终值）。"""
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import AppUser, get_session

        with get_session() as s:
            u = s.query(AppUser).filter_by(biz_id=biz_id).first()
            if u is None:
                return
            if status is not None:
                u.status = status
            if tier is not None:
                u.tier = tier
            if member_expire is not None:
                u.member_expire = member_expire
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_app_user 失败：{exc}")


def persist_member_add(biz_id: str, name: str, role: str, enabled: bool) -> None:
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import AdminMember, get_session

        with get_session() as s:
            s.add(AdminMember(biz_id=biz_id, name=name, role=role, enabled=enabled))
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_member_add 失败：{exc}")


def persist_member_update(biz_id: str, *, name=None, role=None, enabled=None) -> None:
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import AdminMember, get_session

        with get_session() as s:
            m = s.query(AdminMember).filter_by(biz_id=biz_id).first()
            if m is None:
                return
            if name is not None:
                m.name = name
            if role is not None:
                m.role = role
            if enabled is not None:
                m.enabled = enabled
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_member_update 失败：{exc}")


def persist_member_delete(biz_id: str) -> None:
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import AdminMember, get_session

        with get_session() as s:
            m = s.query(AdminMember).filter_by(biz_id=biz_id).first()
            if m is not None:
                s.delete(m)
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_member_delete 失败：{exc}")


def persist_favorite(token: str, evt_id: str, added: bool) -> None:
    """收藏增删落库（added=True 新增，False 移除）。"""
    if not DB_ENABLED:
        return
    eid = _event_int(evt_id)
    if eid is None:
        return
    try:
        from content_engine.models import Favorite, get_session

        with get_session() as s:
            existing = s.query(Favorite).filter_by(token=token, event_id=eid).first()
            if added and existing is None:
                s.add(Favorite(token=token, event_id=eid))
            elif not added and existing is not None:
                s.delete(existing)
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_favorite 失败：{exc}")


def persist_history_view(token: str, evt_id: str, viewed_at: str) -> None:
    """阅读历史落库：同 token+event 去重后更新时间（最近在前由查询排序保证）。"""
    if not DB_ENABLED:
        return
    eid = _event_int(evt_id)
    if eid is None:
        return
    dt = _parse_dt(viewed_at) or datetime.now(timezone.utc)
    try:
        from content_engine.models import ReadingHistory, get_session

        with get_session() as s:
            row = s.query(ReadingHistory).filter_by(token=token, event_id=eid).first()
            if row is None:
                s.add(ReadingHistory(token=token, event_id=eid, viewed_at=dt))
            else:
                row.viewed_at = dt
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_history_view 失败：{exc}")


def persist_history_clear(token: str) -> None:
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import ReadingHistory, get_session

        with get_session() as s:
            s.query(ReadingHistory).filter_by(token=token).delete()
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_history_clear 失败：{exc}")


def persist_purchase(token: str, report_biz_id: str, amount: int) -> None:
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import ReportPurchase, get_session

        with get_session() as s:
            existing = s.query(ReportPurchase).filter_by(
                token=token, report_biz_id=report_biz_id
            ).first()
            if existing is None:
                s.add(ReportPurchase(
                    token=token, report_biz_id=report_biz_id, amount=amount,
                    purchased_at=datetime.now(timezone.utc),
                ))
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_purchase 失败：{exc}")


def persist_digest_config(**fields) -> None:
    """保存定时早报配置（单行 upsert）。仅写入传入的键。"""
    if not DB_ENABLED:
        return
    allowed = {"enabled", "send_time", "audience", "modules", "top_n", "title_template"}
    try:
        from content_engine.models import DigestConfig, get_session

        with get_session() as s:
            d = s.query(DigestConfig).first()
            if d is None:
                d = DigestConfig()
                s.add(d)
            for k, v in fields.items():
                if k in allowed:
                    setattr(d, k, v)
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_digest_config 失败：{exc}")


def persist_push_record(rec: dict) -> None:
    """新增一条推送历史（手动推送 / 每日早报手动触发）。"""
    if not DB_ENABLED:
        return
    try:
        from content_engine.models import PushRecord, get_session

        with get_session() as s:
            s.add(PushRecord(
                biz_id=rec.get("push_id") or rec.get("biz_id"),
                event_ref=rec.get("event_id") or rec.get("event_ref"),
                type=rec.get("type", "manual"),
                title=rec.get("title", ""),
                audience=rec.get("audience", "all"),
                pushed_at=_parse_dt(rec.get("pushed_at")) or datetime.now(timezone.utc),
                sent=rec.get("sent", 0),
                opened=rec.get("opened", 0),
                event_ids=rec.get("event_ids", []),
            ))
    except Exception as exc:  # noqa: BLE001
        print(f"[db_source] persist_push_record 失败：{exc}")
