"""「热读」后端 Mock 服务
================================================
零额外依赖（Python 标准库 http.server），复用 pipeline_demo/output.json 真实数据。

对应文档：API设计.md
启动：
    python3 mock_server/server.py            # 默认 8000 端口
    python3 mock_server/server.py 9000        # 指定端口

特性：
    - 真实数据：以内容管线产出的 output.json 为数据源
    - 付费墙：按会员态裁剪 deep_content（用 token 区分 free / member）
    - CORS 全开，便于前端 / 原型页直接联调
    - 统一响应包络：{code, message, data, request_id}

联调用 token（放 Authorization: Bearer <token>）：
    free-token    → 免费用户（详情深度解读被锁）
    member-token  → 会员（解锁全文）
    （无 token 视为游客 = 免费态）
"""

import json
import os
import sys
import uuid
import re
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "..", "pipeline_demo", "output.json")

# 模块中英映射（API 用英文 key，数据用中文）
MODULE_EN2CN = {"tech": "科技", "finance": "金融", "ai": "AI", "macro": "宏观"}
MODULE_CN2EN = {v: k for k, v in MODULE_EN2CN.items()}


# ----------------------------------------------------------------------------
# 数据层：加载 output.json 并增强为完整 API 数据模型
# ----------------------------------------------------------------------------
def load_events():
    """读取管线产物，补齐 event_id / 排名 / 热度 / 深度解读等 API 字段。"""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        print(f"[警告] 未找到 {DATA_FILE}，请先运行 pipeline_demo/run_pipeline.py")
        raw = []

    events = []
    # 按 importance 排序后赋全局排名
    raw.sort(key=lambda x: x.get("importance", 0), reverse=True)
    for i, e in enumerate(raw):
        module_cn = e.get("module", "科技")
        eid = f"evt_{1000 + i}"
        importance = e.get("importance", 50.0)
        events.append({
            "event_id": eid,
            "module_cn": module_cn,
            "module": MODULE_CN2EN.get(module_cn, "tech"),
            "title": e.get("title", ""),
            "summary": e.get("summary", []),
            "why_matters": e.get("why_matters", ""),
            "sources": e.get("sources", []),
            "source_count": e.get("source_count", 1),
            "importance": importance,
            "hotness": int(importance * 10000),   # 模拟热度值
            "published_at": "2026-06-08T08:00:00Z",
            # CMS 状态机：reviewing（待审核）→ published（已发布）/ rejected（已打回）
            # 前 1/3 默认置为待审核，其余视为已发布，便于审核页有真实分布
            "status": "reviewing" if i % 3 == 0 else "published",
            "pinned": False,        # 是否置顶
            "pushed": False,        # 是否已推送
            "updated_at": "2026-06-08T08:00:00Z",
            "disclaimer": "本文内容由 AI 辅助生成，不构成投资建议" if module_cn in ("金融", "宏观") else "",
            # 深度解读（付费内容）
            "deep_content_full": (
                f"【深度解读】围绕「{e.get('title','')[:20]}」，从行业格局、"
                "关键玩家、对投资人与创业者的影响三个维度展开分析……（此处为 Mock 全文，"
                "生产环境由大模型生成付费深度内容）"
            ),
        })
    return events


EVENTS = load_events()
EVENTS_BY_ID = {e["event_id"]: e for e in EVENTS}

# 内存态：用户收藏（按 token 区分）
FAVORITES = {}

# 内存态：推送历史（审核页「推送」动作写入，供推送运营查看）
# 预置几条历史，带触达/打开指标，便于推送运营页演示
PUSH_HISTORY = [
    {"push_id": "push_seed03", "event_id": "evt_1002", "type": "manual",
     "title": "美联储维持利率不变，鲍威尔释放年内降息信号",
     "audience": "all", "pushed_at": "2026-06-08T08:30:00Z",
     "sent": 10180, "opened": 2342},
    {"push_id": "push_seed02", "event_id": "evt_1005", "type": "daily",
     "title": "每日早报 · 6 月 7 日 | 科技 / 金融 / AI / 宏观",
     "audience": "all", "pushed_at": "2026-06-07T08:00:00Z",
     "sent": 9870, "opened": 2603},
    {"push_id": "push_seed01", "event_id": "evt_1001", "type": "manual",
     "title": "OpenAI 发布新一代模型，推理成本下降 80%",
     "audience": "member", "pushed_at": "2026-06-06T19:15:00Z",
     "sent": 2280, "opened": 821},
]

# 内存态：定时早报配置
DIGEST_CONFIG = {
    "enabled": True,
    "send_time": "08:00",                 # 每日推送时间
    "audience": "all",                    # all / member / free
    "modules": ["tech", "finance", "ai", "macro"],   # 纳入早报的模块
    "top_n": 5,                           # 每日精选条数
    "title_template": "每日早报 · {date} | 今日 {count} 条要闻",
}

# 内存态：信源库（信源管理页的增/改/调权重写操作作用于此）
# level S/A/B 对应权重 1.0/0.7/0.3
SOURCE_WEIGHT = {"S": 1.0, "A": 0.7, "B": 0.3}
SOURCES = [
    {"id": "s_1", "name": "36氪", "level": "A", "weight": 0.7,
     "url": "https://36kr.com", "enabled": True},
    {"id": "s_2", "name": "华尔街见闻", "level": "A", "weight": 0.7,
     "url": "https://wallstreetcn.com", "enabled": True},
    {"id": "s_3", "name": "Reuters", "level": "S", "weight": 1.0,
     "url": "https://reuters.com", "enabled": True},
]

# 会员套餐
PLANS = [
    {"id": "member_month", "tier": "member", "name": "会员月卡", "price": 30,
     "period": "month", "store_product_id": "com.redu.member.month"},
    {"id": "member_year", "tier": "member", "name": "会员年卡", "price": 298,
     "period": "year", "daily_equiv": 0.8, "badge": "最划算",
     "store_product_id": "com.redu.member.year"},
]

MARKET_TICKER = [
    {"name": "上证指数", "value": "3,210.5", "change_pct": -0.62, "direction": "down"},
    {"name": "纳斯达克", "value": "17,890", "change_pct": 1.21, "direction": "up"},
    {"name": "比特币", "value": "68,200", "change_pct": -2.05, "direction": "down"},
]

SLOGAN = "每天 10 分钟，读懂科技、金融、AI、宏观四个赛道最值得关注的事"


# ----------------------------------------------------------------------------
# 视图转换：把内部事件转为不同接口需要的形态
# ----------------------------------------------------------------------------
def published(events):
    """仅返回已发布事件，置顶优先。客户端可见性受 CMS 状态控制。"""
    pub = [e for e in events if e.get("status") == "published"]
    return sorted(pub, key=lambda e: (e.get("pinned", False), e["importance"]),
                  reverse=True)


def to_card(e):
    """事件卡片（列表/Feed 用）。"""
    return {
        "event_id": e["event_id"], "module": e["module"],
        "title": e["title"], "summary": e["summary"][:1],
        "why_matters": e["why_matters"], "importance": e["importance"],
        "source_count": e["source_count"],
    }


def to_rank_item(e, rank, trend="flat", rank_change=0):
    """热榜条目。"""
    return {
        "rank": rank, "event_id": e["event_id"], "module": e["module"],
        "title": e["title"], "hotness": e["hotness"],
        "trend": trend, "rank_change": rank_change,
        "source_count": e["source_count"],
    }


def to_detail(e, is_member):
    """事件详情：按会员态裁剪 deep_content（付费墙核心）。"""
    detail = {
        "event_id": e["event_id"], "module": e["module"], "title": e["title"],
        "summary": e["summary"], "why_matters": e["why_matters"],
        "facts": [{"text": s, "source_ref": [1]} for s in e["summary"]],
        "sources": e["sources"], "source_count": e["source_count"],
        "published_at": e["published_at"], "disclaimer": e["disclaimer"],
    }
    if is_member:
        detail["deep_content"] = {"is_locked": False, "content": e["deep_content_full"]}
    else:
        detail["deep_content"] = {
            "is_locked": True,
            "preview": e["deep_content_full"][:40] + "……",
            "paywall": {"required_tier": "member", "cta": "开通会员，解锁完整深度解读"},
        }
    return detail


def trend_for(rank):
    """Mock 趋势：演示 ▲/新/— 三态。"""
    if rank <= 3:
        return ("up", rank)
    if rank % 5 == 0:
        return ("new", 0)
    return ("flat", 0)


# ----------------------------------------------------------------------------
# 鉴权：用 token 简单区分会员态（Mock）
# ----------------------------------------------------------------------------
def parse_membership(headers):
    auth = headers.get("Authorization", "")
    token = auth.replace("Bearer ", "").strip()
    if token == "member-token":
        return token, "member", True
    return (token or "guest"), "free", False


# ----------------------------------------------------------------------------
# 路由处理
# ----------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    # ---- 工具 ----
    def _send(self, data, code=0, http_status=200, message="ok"):
        body = json.dumps({
            "code": code, "message": message, "data": data,
            "request_id": "req_" + uuid.uuid4().hex[:8],
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(http_status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *args):
        print(f"  [{self.command}] {self.path} -> {args[1] if len(args) > 1 else ''}")

    def do_OPTIONS(self):
        self._send({}, http_status=204)

    # ---- GET ----
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        q = parse_qs(u.query)
        token, tier, is_member = parse_membership(self.headers)

        # 健康检查 / 接口索引
        if path in ("", "/v1", "/v1/ping"):
            return self._send({"service": "redu-mock", "events": len(EVENTS),
                               "endpoints": ENDPOINT_LIST})

        # 今日简报
        if path == "/v1/home/briefing":
            pub = published(EVENTS)
            top = pub[0] if pub else None
            hot = [to_rank_item(e, i + 1, *trend_for(i + 1)) for i, e in enumerate(pub[:10])]
            return self._send({
                "date": "2026-06-08", "slogan": SLOGAN, "total_count": len(pub),
                "market_ticker": MARKET_TICKER,
                "top_headline": (to_card(top) | {"rank": 1}) if top else None,
                "hot_list": hot,
                "feed": [to_card(e) for e in pub[:20]],
            })

        # 频道页
        m = re.match(r"^/v1/channels/(tech|finance|ai|macro)$", path)
        if m:
            module = m.group(1)
            items = published([e for e in EVENTS if e["module"] == module])
            page = int(q.get("page", ["1"])[0]); size = int(q.get("size", ["20"])[0])
            start = (page - 1) * size
            paged = items[start:start + size]
            hot = [to_rank_item(e, i + 1, *trend_for(i + 1)) for i, e in enumerate(items[:10])]
            return self._send({
                "module": module, "updated_at": "2026-06-08T09:41:00Z",
                "top_headline": (to_card(items[0]) | {"rank": 1}) if items else None,
                "hot_list": hot,
                "feed": [to_card(e) for e in paged],
                "pagination": {"page": page, "size": size, "total": len(items),
                               "has_more": start + size < len(items)},
            })

        # 热榜
        if path == "/v1/ranking":
            scope = q.get("scope", ["global"])[0]
            limit = int(q.get("limit", ["10"])[0])
            pool = published(EVENTS) if scope == "global" \
                else published([e for e in EVENTS if e["module"] == scope])
            items = [to_rank_item(e, i + 1, *trend_for(i + 1)) for i, e in enumerate(pool[:limit])]
            return self._send({"scope": scope, "updated_at": "2026-06-08T09:41:00Z",
                               "items": items})

        # 事件详情
        m = re.match(r"^/v1/events/(evt_\d+)$", path)
        if m:
            e = EVENTS_BY_ID.get(m.group(1))
            if not e:
                return self._send(None, code=1003, http_status=404, message="事件不存在")
            detail = to_detail(e, is_member)
            detail["user_state"] = {"is_favorited": m.group(1) in FAVORITES.get(token, set())}
            return self._send(detail)

        # 相关推荐
        m = re.match(r"^/v1/events/(evt_\d+)/related$", path)
        if m:
            e = EVENTS_BY_ID.get(m.group(1))
            if not e:
                return self._send(None, code=1003, http_status=404, message="事件不存在")
            rel = [to_card(x) for x in EVENTS if x["module"] == e["module"]
                   and x["event_id"] != e["event_id"]][:5]
            return self._send({"items": rel})

        # 我的
        if path == "/v1/me":
            return self._send({"id": "u_001", "nickname": "投资人A", "avatar": "",
                               "membership": {"tier": tier, "expire_at":
                                              "2027-06-08T00:00:00Z" if is_member else None}})
        if path == "/v1/me/favorites":
            ids = FAVORITES.get(token, set())
            return self._send({"items": [to_card(EVENTS_BY_ID[i]) for i in ids
                                         if i in EVENTS_BY_ID]})

        # 会员套餐
        if path == "/v1/membership/plans":
            return self._send({"plans": PLANS})

        # ---- CMS ----
        if path == "/v1/admin/events":
            status = q.get("status", ["published"])[0]
            module = q.get("module", [None])[0]
            kw = q.get("q", [None])[0]
            pool = [e for e in EVENTS if e.get("status") == status]
            if module:
                pool = [e for e in pool if e["module"] == module]
            if kw:
                pool = [e for e in pool if kw in e["title"]]
            # 置顶优先，其次重要度
            pool = sorted(pool, key=lambda e: (e.get("pinned", False), e["importance"]),
                          reverse=True)
            return self._send({"items": [
                {"event_id": e["event_id"], "module": e["module"], "title": e["title"],
                 "status": e["status"], "importance": e["importance"],
                 "pinned": e.get("pinned", False), "pushed": e.get("pushed", False)}
                for e in pool],
                "pagination": {"page": 1, "size": len(pool), "total": len(pool),
                               "has_more": False}})

        m = re.match(r"^/v1/admin/events/(evt_\d+)/validation$", path)
        if m:
            return self._send({"checks": [
                {"type": "citation", "pass": True},
                {"type": "number_consistency", "pass": True},
                {"type": "compliance", "pass": True},
                {"type": "disclaimer", "pass": True},
            ], "overall": "pass"})

        if path == "/v1/admin/sources":
            return self._send({"items": list(SOURCES)})

        if path == "/v1/admin/stats/pipeline":
            return self._send({"collected": 80, "dedup_removed": 2, "events": len(EVENTS),
                               "classify_acc": 0.92, "reject_rate": 0.05,
                               "e2e_latency_sec": 5})

        if path == "/v1/admin/stats/business":
            # 近 7 日趋势（Mock：演示用，呈温和增长）
            days = ["06-03", "06-04", "06-05", "06-06", "06-07", "06-08", "06-09"]
            dau = [8200, 8600, 8900, 9300, 9100, 9800, 10240]
            new_user = [620, 680, 710, 760, 690, 820, 910]
            pay = [12, 15, 14, 19, 17, 22, 26]
            return self._send({
                "kpi": {
                    "dau": 10240, "dau_wow": 0.124,            # 周环比
                    "mau": 86500, "mau_wow": 0.083,
                    "retention_d7": 0.42, "retention_d30": 0.21,
                    "push_open_rate": 0.187,
                    "pay_conversion": 0.027,                    # 付费转化率
                    "paying_users": 2310, "paying_wow": 0.061,
                    "arpu": 18.6, "mrr": 42960,                 # 月度经常性收入
                },
                "trend": {
                    "days": days, "dau": dau,
                    "new_user": new_user, "paying": pay,
                },
                "funnel": [                                     # 付费转化漏斗
                    {"stage": "活跃用户", "value": 10240},
                    {"stage": "阅读详情", "value": 6820},
                    {"stage": "触达付费墙", "value": 2150},
                    {"stage": "进入下单", "value": 480},
                    {"stage": "完成付费", "value": 276},
                ],
                "module_dist": [                                # 各模块阅读占比
                    {"module": "tech", "pct": 0.34},
                    {"module": "ai", "pct": 0.31},
                    {"module": "finance", "pct": 0.24},
                    {"module": "macro", "pct": 0.11},
                ],
            })

        # 推送历史（带触达 / 打开率）
        if path == "/v1/admin/push/history":
            items = []
            total_sent = total_open = 0
            for p in PUSH_HISTORY:
                sent, opened = p.get("sent", 0), p.get("opened", 0)
                total_sent += sent; total_open += opened
                items.append(dict(p, open_rate=round(opened / sent, 4) if sent else 0))
            return self._send({
                "items": items,
                "summary": {
                    "push_count": len(items),
                    "total_sent": total_sent, "total_opened": total_open,
                    "avg_open_rate": round(total_open / total_sent, 4) if total_sent else 0,
                },
            })

        # 定时早报配置
        if path == "/v1/admin/push/digest":
            return self._send(DIGEST_CONFIG)

        return self._send(None, code=1003, http_status=404, message="接口不存在")

    # ---- POST ----
    def do_POST(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/")
        body = self._body()
        token, tier, is_member = parse_membership(self.headers)

        # 登录
        if path == "/v1/auth/login":
            login_type = body.get("type", "phone")
            # Mock：phone 默认返回 free，可用 type=member 模拟会员登录
            member = body.get("type") == "member"
            return self._send({
                "access_token": "member-token" if member else "free-token",
                "refresh_token": "refresh-xyz", "expires_in": 7200,
                "user": {"id": "u_001", "nickname": "投资人A", "avatar": "",
                         "membership": {"tier": "member" if member else "free",
                                        "expire_at": None}},
            })

        if path == "/v1/auth/refresh":
            return self._send({"access_token": token or "free-token", "expires_in": 7200})

        # 收藏
        m = re.match(r"^/v1/events/(evt_\d+)/favorite$", path)
        if m:
            eid = m.group(1)
            fav = FAVORITES.setdefault(token, set())
            if body.get("action") == "remove":
                fav.discard(eid)
                return self._send({"is_favorited": False})
            fav.add(eid)
            return self._send({"is_favorited": True})

        # 下单
        if path == "/v1/membership/orders":
            plan = next((p for p in PLANS if p["id"] == body.get("plan_id")), PLANS[0])
            return self._send({"order_id": "ord_" + uuid.uuid4().hex[:8],
                               "store_product_id": plan["store_product_id"],
                               "amount": plan["price"]})

        # 支付校验
        if path == "/v1/membership/verify":
            return self._send({"status": "success",
                               "membership": {"tier": "member",
                                              "expire_at": "2027-06-08T00:00:00Z"}})

        # CMS 审核动作：真实变更内存态
        m = re.match(r"^/v1/admin/events/(evt_\d+)/(approve|reject|publish|pin|unpin|push)$", path)
        if m:
            eid, action = m.group(1), m.group(2)
            e = EVENTS_BY_ID.get(eid)
            if not e:
                return self._send(None, code=1003, http_status=404, message="事件不存在")
            if action == "approve":
                e["status"] = "published"        # 审核通过即发布到客户端
            elif action == "reject":
                e["status"] = "rejected"
            elif action == "publish":
                e["status"] = "published"
            elif action == "pin":
                e["pinned"] = True
            elif action == "unpin":
                e["pinned"] = False
            elif action == "push":
                e["pushed"] = True
                aud = body.get("audience", "all")
                # Mock 触达量：全量约 1.02 万，会员约 2300
                sent = 10240 if aud == "all" else (2300 if aud == "member" else 7940)
                PUSH_HISTORY.insert(0, {
                    "push_id": "push_" + uuid.uuid4().hex[:8],
                    "event_id": eid, "title": e["title"], "type": "manual",
                    "pushed_at": _now(), "audience": aud,
                    "sent": sent, "opened": 0,    # 刚推送，打开数从 0 起
                })
            e["updated_at"] = _now()
            return self._send({"event_id": eid, "action": action, "status": e["status"],
                               "pinned": e["pinned"], "pushed": e["pushed"]})

        # CMS 在线编辑：更新标题 / 摘要 / 为何重要
        m = re.match(r"^/v1/admin/events/(evt_\d+)/edit$", path)
        if m:
            e = EVENTS_BY_ID.get(m.group(1))
            if not e:
                return self._send(None, code=1003, http_status=404, message="事件不存在")
            if "title" in body:
                e["title"] = body["title"]
            if "summary" in body:
                e["summary"] = body["summary"] if isinstance(body["summary"], list) \
                    else [s.strip() for s in str(body["summary"]).split("\n") if s.strip()]
            if "why_matters" in body:
                e["why_matters"] = body["why_matters"]
            e["updated_at"] = _now()
            return self._send({"event_id": e["event_id"], "title": e["title"],
                               "summary": e["summary"], "why_matters": e["why_matters"]})

        # CMS 事件合并：把 source 事件并入 target（来源合并、source 标记为打回）
        if path == "/v1/admin/events/merge":
            target_id = body.get("target_id")
            source_ids = body.get("source_ids", [])
            target = EVENTS_BY_ID.get(target_id)
            if not target:
                return self._send(None, code=1003, http_status=404, message="主事件不存在")
            merged = 0
            seen = {s.get("url") or s.get("name") for s in target["sources"]}
            for sid in source_ids:
                se = EVENTS_BY_ID.get(sid)
                if not se or sid == target_id:
                    continue
                for s in se["sources"]:
                    key = s.get("url") or s.get("name")
                    if key not in seen:
                        target["sources"].append(s); seen.add(key)
                se["status"] = "rejected"        # 被合并的事件下线
                se["updated_at"] = _now()
                merged += 1
            target["source_count"] = len(target["sources"])
            target["updated_at"] = _now()
            return self._send({"target_id": target_id, "merged_count": merged,
                               "source_count": target["source_count"]})

        # 保存定时早报配置
        if path == "/v1/admin/push/digest":
            for k in ("enabled", "send_time", "audience", "modules", "top_n",
                      "title_template"):
                if k in body:
                    DIGEST_CONFIG[k] = body[k]
            return self._send(DIGEST_CONFIG)

        # 立即发送一期早报（手动触发）
        if path == "/v1/admin/push/digest/send":
            aud = DIGEST_CONFIG["audience"]
            pub = [e for e in published(EVENTS)
                   if e["module"] in DIGEST_CONFIG["modules"]][:DIGEST_CONFIG["top_n"]]
            sent = 10240 if aud == "all" else (2300 if aud == "member" else 7940)
            rec = {
                "push_id": "push_" + uuid.uuid4().hex[:8], "type": "daily",
                "title": DIGEST_CONFIG["title_template"].format(
                    date=_now()[:10], count=len(pub)),
                "audience": aud, "pushed_at": _now(),
                "sent": sent, "opened": 0,
                "event_ids": [e["event_id"] for e in pub],
            }
            PUSH_HISTORY.insert(0, rec)
            return self._send(dict(rec, included=[
                {"event_id": e["event_id"], "title": e["title"]} for e in pub]))

        # 信源管理：新增信源
        if path == "/v1/admin/sources":
            name = (body.get("name") or "").strip()
            if not name:
                return self._send(None, code=1002, http_status=400,
                                  message="信源名称不能为空")
            level = body.get("level", "B")
            if level not in SOURCE_WEIGHT:
                return self._send(None, code=1002, http_status=400,
                                  message="信源等级须为 S/A/B")
            rec = {
                "id": "s_" + uuid.uuid4().hex[:8], "name": name, "level": level,
                "weight": body.get("weight", SOURCE_WEIGHT[level]),
                "url": (body.get("url") or "").strip(),
                "enabled": bool(body.get("enabled", True)),
            }
            SOURCES.append(rec)
            return self._send(rec)

        # 信源管理：编辑 / 调权重 / 启停
        m = re.match(r"^/v1/admin/sources/(s_\w+)$", path)
        if m:
            src = next((s for s in SOURCES if s["id"] == m.group(1)), None)
            if not src:
                return self._send(None, code=1003, http_status=404,
                                  message="信源不存在")
            if "name" in body:
                src["name"] = (body["name"] or "").strip() or src["name"]
            if "url" in body:
                src["url"] = (body["url"] or "").strip()
            if "level" in body and body["level"] in SOURCE_WEIGHT:
                src["level"] = body["level"]
                # 调级时若未显式传 weight，则按等级回填默认权重
                if "weight" not in body:
                    src["weight"] = SOURCE_WEIGHT[src["level"]]
            if "weight" in body:
                try:
                    src["weight"] = max(0.0, min(1.0, float(body["weight"])))
                except (TypeError, ValueError):
                    pass
            if "enabled" in body:
                src["enabled"] = bool(body["enabled"])
            return self._send(src)

        # 信源管理：删除信源
        m = re.match(r"^/v1/admin/sources/(s_\w+)/delete$", path)
        if m:
            idx = next((i for i, s in enumerate(SOURCES)
                        if s["id"] == m.group(1)), -1)
            if idx < 0:
                return self._send(None, code=1003, http_status=404,
                                  message="信源不存在")
            removed = SOURCES.pop(idx)
            return self._send({"id": removed["id"], "deleted": True})

        return self._send(None, code=1003, http_status=404, message="接口不存在")

    def do_PUT(self):
        self._send({"result": "ok"})

    def do_DELETE(self):
        self._send({"result": "ok"})


ENDPOINT_LIST = [
    "GET  /v1/home/briefing",
    "GET  /v1/channels/{tech|finance|ai|macro}",
    "GET  /v1/ranking?scope=global|tech|finance|ai|macro",
    "GET  /v1/events/{id}",
    "GET  /v1/events/{id}/related",
    "POST /v1/events/{id}/favorite",
    "GET  /v1/me  /v1/me/favorites",
    "POST /v1/auth/login (type=phone|member)",
    "GET  /v1/membership/plans",
    "POST /v1/membership/orders  /v1/membership/verify",
    "GET  /v1/admin/events?status=&module=&q=  /v1/admin/sources  /v1/admin/stats/{pipeline|business}",
    "POST /v1/admin/events/{id}/{approve|reject|publish|pin|unpin|push}",
    "POST /v1/admin/events/{id}/edit   /v1/admin/events/merge",
    "GET  /v1/admin/push/history   /v1/admin/push/digest",
    "POST /v1/admin/push/digest   /v1/admin/push/digest/send",
    "POST /v1/admin/sources   /v1/admin/sources/{id}   /v1/admin/sources/{id}/delete",
]


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("=" * 56)
    print(f"「热读」Mock 服务已启动： http://localhost:{port}/v1/ping")
    print(f"加载事件：{len(EVENTS)} 条（数据源 pipeline_demo/output.json）")
    print("会员联调：Authorization: Bearer member-token（解锁付费墙）")
    print("=" * 56)
    for ep in ENDPOINT_LIST:
        print("  " + ep)
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")


if __name__ == "__main__":
    main()
