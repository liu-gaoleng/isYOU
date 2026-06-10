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


def _host(url):
    """提取 URL 主机名（去 www. 前缀），用于信源去重比对。"""
    if not url:
        return ""
    h = urlparse(url if "://" in url else "http://" + url).netloc.lower()
    return h[4:] if h.startswith("www.") else h

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
FAVORITES = {
    "guest": ["evt_1001", "evt_1003", "evt_1006"],
}

# 内存态：阅读历史（按 token 区分，最近在前；进入事件详情时写入）
HISTORY = {
    "guest": [
        {"event_id": "evt_1002", "viewed_at": "2026-06-09T21:12:00Z"},
        {"event_id": "evt_1004", "viewed_at": "2026-06-09T20:40:00Z"},
        {"event_id": "evt_1001", "viewed_at": "2026-06-09T08:15:00Z"},
    ],
}

# 内存态：推送设置（按 token 区分）
DEFAULT_PUSH_SETTINGS = {"daily_push": True, "push_time": "08:00",
                         "breaking_push": False}
PUSH_SETTINGS = {}

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

# ----------------------------------------------------------------------------
# 后台 RBAC：角色权限矩阵 + 运营成员
# 权限级别：none（不可见）/ read（只读）/ write（可写）
# 页面 key 与前端 data-page 对应：overview/business/review/push/sources/members
# ----------------------------------------------------------------------------
ROLE_PAGES = ["overview", "business", "review", "push", "sources", "members", "users"]
ROLE_PERMS = {
    "admin": {"name": "超级管理员",
              "perms": {"overview": "write", "business": "write", "review": "write",
                        "push": "write", "sources": "write", "members": "write",
                        "users": "write"}},
    "auditor": {"name": "审核员",
                "perms": {"overview": "write", "business": "read", "review": "write",
                          "push": "write", "sources": "read", "members": "none",
                          "users": "read"}},
    "operator": {"name": "运营",
                 "perms": {"overview": "write", "business": "write", "review": "read",
                           "push": "write", "sources": "write", "members": "none",
                           "users": "write"}},
    "viewer": {"name": "只读访客",
               "perms": {"overview": "read", "business": "read", "review": "read",
                         "push": "read", "sources": "read", "members": "none",
                         "users": "read"}},
}

ADMIN_USERS = [
    {"id": "u_1", "name": "陈管理", "role": "admin", "enabled": True,
     "created_at": "2026-05-01T08:00:00Z"},
    {"id": "u_2", "name": "李审核", "role": "auditor", "enabled": True,
     "created_at": "2026-05-12T08:00:00Z"},
    {"id": "u_3", "name": "王运营", "role": "operator", "enabled": True,
     "created_at": "2026-05-20T08:00:00Z"},
    {"id": "u_4", "name": "访客demo", "role": "viewer", "enabled": False,
     "created_at": "2026-06-01T08:00:00Z"},
]

# ----------------------------------------------------------------------------
# C 端用户运营：App 注册 / 付费用户
# tier: free / member；status: active / banned
# ----------------------------------------------------------------------------
APP_USERS = [
    {"id": "au_1", "phone": "138****8001", "nick": "投资老张", "tier": "member",
     "status": "active", "registered_at": "2026-03-02T10:00:00Z",
     "member_expire": "2027-03-02", "total_paid": 298,
     "orders": [{"order_id": "o_1001", "plan": "会员年卡", "amount": 298,
                 "paid_at": "2026-03-02T10:05:00Z"}]},
    {"id": "au_2", "phone": "139****2046", "nick": "AI产品李", "tier": "member",
     "status": "active", "registered_at": "2026-04-11T09:30:00Z",
     "member_expire": "2026-07-11", "total_paid": 90,
     "orders": [{"order_id": "o_1002", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-04-11T09:32:00Z"},
                {"order_id": "o_1003", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-05-11T09:32:00Z"},
                {"order_id": "o_1004", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-06-11T09:32:00Z"}]},
    {"id": "au_3", "phone": "137****5588", "nick": "创业者王", "tier": "free",
     "status": "active", "registered_at": "2026-05-20T14:00:00Z",
     "member_expire": "", "total_paid": 0, "orders": []},
    {"id": "au_4", "phone": "150****3322", "nick": "羊毛党", "tier": "free",
     "status": "banned", "registered_at": "2026-05-28T22:10:00Z",
     "member_expire": "", "total_paid": 0, "orders": []},
    {"id": "au_5", "phone": "186****7799", "nick": "宏观研究员", "tier": "member",
     "status": "active", "registered_at": "2026-02-15T08:00:00Z",
     "member_expire": "2026-06-15", "total_paid": 328,
     "orders": [{"order_id": "o_1005", "plan": "会员年卡", "amount": 298,
                 "paid_at": "2026-02-15T08:05:00Z"},
                {"order_id": "o_1006", "plan": "会员月卡", "amount": 30,
                 "paid_at": "2026-02-15T08:06:00Z"}]},
]

# 会员套餐
PLANS = [
    {"id": "member_month", "tier": "member", "name": "会员月卡", "price": 30,
     "period": "month", "store_product_id": "com.redu.member.month"},
    {"id": "member_year", "tier": "member", "name": "会员年卡", "price": 298,
     "period": "year", "daily_equiv": 0.8, "badge": "最划算",
     "store_product_id": "com.redu.member.year"},
]

# 内存态：付费报告。member_free=True 表示会员免费，否则会员 8 折
REPORTS = [
    {"id": "rpt_1", "title": "2026 AI 应用层投资地图", "module": "ai",
     "pages": 62, "price": 299, "member_free": False,
     "desc": "62 页 · 含 200+ 标的数据库",
     "summary": "系统拆解 2026 年 AI 应用层的六大高增长方向，附 200+ 一二级标的数据库与估值对标。",
     "toc": ["应用层投资主线与时间窗口", "Agent / 多模态 / 垂直工作流三大赛道",
             "200+ 标的数据库与估值对标", "退出路径与风险提示"]},
    {"id": "rpt_2", "title": "一级市场融资月报 · 5 月", "module": "finance",
     "pages": 38, "price": 99, "member_free": True,
     "desc": "38 页 · 赛道热度与估值追踪",
     "summary": "5 月一级市场融资全景：按赛道统计金额、轮次分布与头部机构出手，附热度榜与估值变化。",
     "toc": ["5 月融资总览与同比", "热门赛道与代表案例", "活跃机构出手统计",
             "估值区间与下月展望"]},
    {"id": "rpt_3", "title": "宏观季度展望：利率与流动性", "module": "macro",
     "pages": 45, "price": 199, "member_free": False,
     "desc": "45 页 · 含数据看板访问权",
     "summary": "围绕利率路径与全球流动性，给出未来一季度宏观情景假设、资产配置含义与关键数据日历。",
     "toc": ["利率路径的三种情景", "全球流动性与汇率", "大类资产配置含义",
             "关键数据与事件日历"]},
]
REPORTS_BY_ID = {r["id"]: r for r in REPORTS}

# 内存态：已购报告（按 token 区分），预置 guest 已购 2 份
PURCHASED_REPORTS = {
    "guest": ["rpt_2", "rpt_3"],
}


def report_card(r, owned):
    """报告列表/详情共用的卡片视图，附 owned 与会员价。"""
    member_price = 0 if r["member_free"] else round(r["price"] * 0.8)
    return {
        "id": r["id"], "title": r["title"], "module": r["module"],
        "pages": r["pages"], "price": r["price"], "desc": r["desc"],
        "member_free": r["member_free"], "member_price": member_price,
        "owned": owned,
    }


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
            # 四模块均衡配额：按模块分组后轮询交错，保证每个赛道都有露出
            buckets = {mod: [e for e in pub if e["module"] == mod]
                       for mod in MODULE_EN2CN}
            feed_events, i = [], 0
            while len(feed_events) < 20 and any(i < len(b) for b in buckets.values()):
                for mod in MODULE_EN2CN:
                    if i < len(buckets[mod]):
                        feed_events.append(buckets[mod][i])
                i += 1
            return self._send({
                "date": "2026-06-08", "slogan": SLOGAN, "total_count": len(pub),
                "market_ticker": MARKET_TICKER,
                "top_headline": (to_card(top) | {"rank": 1}) if top else None,
                "hot_list": hot,
                "feed": [to_card(e) for e in feed_events[:20]],
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

        # 搜索：按标题/摘要/why_matters 关键词命中，支持模块筛选
        if path == "/v1/search":
            kw = (q.get("q", [""])[0]).strip()
            scope = q.get("scope", ["all"])[0]
            page = int(q.get("page", ["1"])[0]); size = int(q.get("size", ["20"])[0])
            if not kw:
                return self._send({"keyword": "", "scope": scope, "items": [],
                                   "pagination": {"page": page, "size": size,
                                                  "total": 0, "has_more": False}})
            pool = published(EVENTS) if scope in ("all", "") \
                else published([e for e in EVENTS if e["module"] == scope])
            low = kw.lower()

            def hit(e):
                hay = (e["title"] + " " + " ".join(e["summary"]) + " "
                       + e.get("why_matters", "")).lower()
                return low in hay
            matched = [e for e in pool if hit(e)]
            start = (page - 1) * size
            paged = matched[start:start + size]
            return self._send({
                "keyword": kw, "scope": scope, "total_count": len(matched),
                "items": [to_card(e) for e in paged],
                "pagination": {"page": page, "size": size, "total": len(matched),
                               "has_more": start + size < len(matched)},
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
            eid = m.group(1)
            e = EVENTS_BY_ID.get(eid)
            if not e:
                return self._send(None, code=1003, http_status=404, message="事件不存在")
            detail = to_detail(e, is_member)
            detail["user_state"] = {"is_favorited": eid in FAVORITES.get(token, [])}
            # 记录阅读历史（去重后置顶，最多保留 50 条）
            hist = HISTORY.setdefault(token, [])
            hist[:] = [h for h in hist if h["event_id"] != eid]
            hist.insert(0, {"event_id": eid, "viewed_at": _now()})
            del hist[50:]
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
            favs = FAVORITES.get(token, [])
            hist = HISTORY.get(token, [])
            reports = PURCHASED_REPORTS.get(token, [])
            return self._send({"id": "u_001", "nickname": "投资人A", "avatar": "",
                               "membership": {"tier": tier, "expire_at":
                                              "2027-06-08T00:00:00Z" if is_member else None},
                               "stats": {"favorites": len(favs), "history": len(hist),
                                         "reports": len(reports)}})
        if path == "/v1/me/favorites":
            ids = FAVORITES.get(token, [])
            return self._send({"items": [to_card(EVENTS_BY_ID[i]) for i in ids
                                         if i in EVENTS_BY_ID]})
        if path == "/v1/me/history":
            items = []
            for h in HISTORY.get(token, []):
                e = EVENTS_BY_ID.get(h["event_id"])
                if e:
                    card = to_card(e)
                    card["viewed_at"] = h["viewed_at"]
                    items.append(card)
            return self._send({"items": items})
        if path == "/v1/me/settings":
            return self._send(dict(PUSH_SETTINGS.get(token, DEFAULT_PUSH_SETTINGS)))

        # 会员套餐
        if path == "/v1/membership/plans":
            return self._send({"plans": PLANS})

        # 报告：列表
        if path == "/v1/reports":
            owned_ids = PURCHASED_REPORTS.get(token, [])
            return self._send({"items": [report_card(r, r["id"] in owned_ids)
                                         for r in REPORTS], "is_member": is_member})

        # 报告：我的已购
        if path == "/v1/reports/mine":
            owned_ids = PURCHASED_REPORTS.get(token, [])
            items = [report_card(REPORTS_BY_ID[i], True) for i in owned_ids
                     if i in REPORTS_BY_ID]
            return self._send({"items": items})

        # 报告：详情
        m = re.match(r"^/v1/reports/(rpt_\w+)$", path)
        if m:
            r = REPORTS_BY_ID.get(m.group(1))
            if not r:
                return self._send(None, code=1003, http_status=404, message="报告不存在")
            owned = r["id"] in PURCHASED_REPORTS.get(token, [])
            card = report_card(r, owned)
            card["summary"] = r["summary"]
            card["toc"] = r["toc"]
            return self._send(card)

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

        # RBAC：角色权限矩阵（供前端渲染权限表与按角色控制 UI）
        if path == "/v1/admin/roles":
            return self._send({
                "pages": ROLE_PAGES,
                "page_names": {"overview": "运营概览", "business": "运营数据",
                               "review": "内容审核", "push": "推送运营",
                               "sources": "信源管理", "members": "成员权限",
                               "users": "用户运营"},
                "roles": [dict(code=k, **v) for k, v in ROLE_PERMS.items()],
            })

        # RBAC：运营成员列表
        if path == "/v1/admin/members":
            items = [dict(m, role_name=ROLE_PERMS.get(m["role"], {}).get("name", m["role"]))
                     for m in ADMIN_USERS]
            return self._send({"items": items})

        # C 端用户运营：用户列表 + 筛选 + summary
        if path == "/v1/admin/users":
            f_tier = (q.get("tier") or [""])[0]
            f_status = (q.get("status") or [""])[0]
            kw = (q.get("q") or [""])[0].strip()
            rows = APP_USERS
            if f_tier:
                rows = [u for u in rows if u["tier"] == f_tier]
            if f_status:
                rows = [u for u in rows if u["status"] == f_status]
            if kw:
                rows = [u for u in rows if kw in u["phone"] or kw in u["nick"]]
            items = [{k: u[k] for k in ("id", "phone", "nick", "tier", "status",
                                        "registered_at", "member_expire", "total_paid")}
                     for u in rows]
            summary = {
                "total": len(APP_USERS),
                "member": len([u for u in APP_USERS if u["tier"] == "member"]),
                "banned": len([u for u in APP_USERS if u["status"] == "banned"]),
                "revenue": sum(u["total_paid"] for u in APP_USERS),
            }
            return self._send({"items": items, "summary": summary})

        # C 端用户运营：用户详情（含付费记录）
        m = re.match(r"^/v1/admin/users/(au_\w+)$", path)
        if m:
            user = next((u for u in APP_USERS if u["id"] == m.group(1)), None)
            if not user:
                return self._send(None, code=1003, http_status=404,
                                  message="用户不存在")
            return self._send(dict(user))

        # 信源智能推荐：从事件 sources 中统计未入库信源，按出现频次降序
        if path == "/v1/admin/sources/suggest":
            known = {s["name"] for s in SOURCES}
            known_urls = {_host(s.get("url", "")) for s in SOURCES if s.get("url")}
            agg = {}
            for ev in EVENTS:
                for src in ev.get("sources", []):
                    name = (src.get("name") or "").strip()
                    if not name or name in known:
                        continue
                    host = _host(src.get("url", ""))
                    if host and host in known_urls:
                        continue
                    item = agg.setdefault(name, {
                        "name": name, "level": src.get("level", "B"),
                        "url": "https://" + host if host else "",
                        "count": 0,
                    })
                    item["count"] += 1
            suggestions = sorted(agg.values(),
                                 key=lambda x: x["count"], reverse=True)
            return self._send({"items": suggestions})

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
            # Mock：phone+验证码 默认返回 free，可用 type=member 模拟会员登录
            member = login_type == "member"
            phone = str(body.get("phone", "")).strip()
            code = str(body.get("code", "")).strip()
            if login_type == "phone":
                if not re.match(r"^1\d{10}$", phone):
                    return self._send(None, code=1002, http_status=400, message="手机号格式不正确")
                if code != "1234":
                    return self._send(None, code=1002, http_status=400, message="验证码错误（Mock 验证码为 1234）")
            nick = ("用户" + phone[-4:]) if phone else "投资人A"
            return self._send({
                "access_token": "member-token" if member else "free-token",
                "refresh_token": "refresh-xyz", "expires_in": 7200,
                "user": {"id": "u_001", "nickname": nick, "avatar": "", "phone": phone,
                         "membership": {"tier": "member" if member else "free",
                                        "expire_at": None}},
            })

        # 发送验证码（Mock：固定 1234）
        if path == "/v1/auth/sms":
            phone = str(body.get("phone", "")).strip()
            if not re.match(r"^1\d{10}$", phone):
                return self._send(None, code=1002, http_status=400, message="手机号格式不正确")
            return self._send({"sent": True, "mock_code": "1234", "expires_in": 300})


        if path == "/v1/auth/refresh":
            return self._send({"access_token": token or "free-token", "expires_in": 7200})

        # 收藏
        m = re.match(r"^/v1/events/(evt_\d+)/favorite$", path)
        if m:
            eid = m.group(1)
            fav = FAVORITES.setdefault(token, [])
            if body.get("action") == "remove":
                if eid in fav:
                    fav.remove(eid)
                return self._send({"is_favorited": False})
            if eid not in fav:
                fav.insert(0, eid)
            return self._send({"is_favorited": True})

        # 推送设置：更新
        if path == "/v1/me/settings":
            cur = dict(PUSH_SETTINGS.get(token, DEFAULT_PUSH_SETTINGS))
            if "daily_push" in body:
                cur["daily_push"] = bool(body["daily_push"])
            if "breaking_push" in body:
                cur["breaking_push"] = bool(body["breaking_push"])
            if body.get("push_time"):
                cur["push_time"] = str(body["push_time"])
            PUSH_SETTINGS[token] = cur
            return self._send(cur)

        # 阅读历史：清空
        if path == "/v1/me/history/clear":
            HISTORY[token] = []
            return self._send({"cleared": True})

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

        # 报告：购买（Mock 直接入库已购）
        m = re.match(r"^/v1/reports/(rpt_\w+)/purchase$", path)
        if m:
            r = REPORTS_BY_ID.get(m.group(1))
            if not r:
                return self._send(None, code=1003, http_status=404, message="报告不存在")
            owned = PURCHASED_REPORTS.setdefault(token, [])
            if r["id"] not in owned:
                owned.append(r["id"])
            amount = 0 if (r["member_free"] and is_member) \
                else (round(r["price"] * 0.8) if is_member else r["price"])
            return self._send({"order_id": "rord_" + uuid.uuid4().hex[:8],
                               "report_id": r["id"], "amount": amount, "owned": True})

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

        # 信源管理：批量导入（粘贴多行，每行「名称,网址,分级」）
        if path == "/v1/admin/sources/import":
            rows = body.get("rows") or []
            added, skipped = [], []
            known_names = {s["name"] for s in SOURCES}
            known_hosts = {_host(s.get("url", "")) for s in SOURCES if s.get("url")}
            for row in rows:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                url = (row.get("url") or "").strip()
                host = _host(url)
                level = row.get("level", "B")
                if level not in SOURCE_WEIGHT:
                    level = "B"
                # 去重：同名或同主机视为已存在
                if name in known_names or (host and host in known_hosts):
                    skipped.append({"name": name, "reason": "已存在"})
                    continue
                rec = {
                    "id": "s_" + uuid.uuid4().hex[:8], "name": name,
                    "level": level, "weight": SOURCE_WEIGHT[level],
                    "url": url, "enabled": True,
                }
                SOURCES.append(rec)
                added.append(rec)
                known_names.add(name)
                if host:
                    known_hosts.add(host)
            return self._send({
                "added": added, "skipped": skipped,
                "added_count": len(added), "skipped_count": len(skipped),
            })

        # 信源管理：重复检测（按主机名分组，找出重复信源）
        if path == "/v1/admin/sources/dedup-scan":
            by_host = {}
            for s in SOURCES:
                host = _host(s.get("url", ""))
                if not host:
                    continue
                by_host.setdefault(host, []).append(s)
            groups = [{"host": h, "items": items}
                      for h, items in by_host.items() if len(items) > 1]
            return self._send({"groups": groups, "group_count": len(groups)})

        # RBAC：新增成员
        if path == "/v1/admin/members":
            name = (body.get("name") or "").strip()
            if not name:
                return self._send(None, code=1002, http_status=400,
                                  message="成员名称不能为空")
            role = body.get("role", "viewer")
            if role not in ROLE_PERMS:
                return self._send(None, code=1002, http_status=400,
                                  message="角色不存在")
            rec = {
                "id": "u_" + uuid.uuid4().hex[:8], "name": name, "role": role,
                "enabled": bool(body.get("enabled", True)), "created_at": _now(),
            }
            ADMIN_USERS.append(rec)
            return self._send(dict(rec, role_name=ROLE_PERMS[role]["name"]))

        # RBAC：编辑成员（改角色 / 启停 / 改名）
        m = re.match(r"^/v1/admin/members/(u_\w+)$", path)
        if m:
            user = next((u for u in ADMIN_USERS if u["id"] == m.group(1)), None)
            if not user:
                return self._send(None, code=1003, http_status=404,
                                  message="成员不存在")
            if "name" in body:
                user["name"] = (body["name"] or "").strip() or user["name"]
            if "role" in body and body["role"] in ROLE_PERMS:
                user["role"] = body["role"]
            if "enabled" in body:
                user["enabled"] = bool(body["enabled"])
            return self._send(dict(user, role_name=ROLE_PERMS[user["role"]]["name"]))

        # RBAC：删除成员
        m = re.match(r"^/v1/admin/members/(u_\w+)/delete$", path)
        if m:
            target = m.group(1)
            admins = [u for u in ADMIN_USERS if u["role"] == "admin" and u["enabled"]]
            victim = next((u for u in ADMIN_USERS if u["id"] == target), None)
            if not victim:
                return self._send(None, code=1003, http_status=404,
                                  message="成员不存在")
            # 防呆：不允许删除最后一个启用的超级管理员
            if victim["role"] == "admin" and victim["enabled"] and len(admins) <= 1:
                return self._send(None, code=1002, http_status=400,
                                  message="至少保留一个超级管理员")
            ADMIN_USERS[:] = [u for u in ADMIN_USERS if u["id"] != target]
            return self._send({"id": target, "deleted": True})

        # C 端用户运营：人工操作（封禁/解禁、调档、延长会员）
        m = re.match(r"^/v1/admin/users/(au_\w+)$", path)
        if m:
            user = next((u for u in APP_USERS if u["id"] == m.group(1)), None)
            if not user:
                return self._send(None, code=1003, http_status=404,
                                  message="用户不存在")
            action = body.get("action", "")
            if action == "ban":
                user["status"] = "banned"
            elif action == "unban":
                user["status"] = "active"
            elif action == "set_tier":
                new_tier = body.get("tier")
                if new_tier in ("free", "member"):
                    user["tier"] = new_tier
                    if new_tier == "free":
                        user["member_expire"] = ""
            elif action == "extend":
                # 延长会员 N 天（基于现有到期日或今天起算）
                days = int(body.get("days", 30))
                base = user.get("member_expire") or _now()[:10]
                try:
                    dt = datetime.strptime(base, "%Y-%m-%d")
                except ValueError:
                    dt = datetime.now(timezone.utc)
                from datetime import timedelta
                user["member_expire"] = (dt + timedelta(days=days)).strftime("%Y-%m-%d")
                user["tier"] = "member"
            else:
                return self._send(None, code=1002, http_status=400,
                                  message="未知操作")
            return self._send(dict(user))

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
    "GET  /v1/search?q=&scope=all|tech|finance|ai|macro&page=&size=",
    "GET  /v1/events/{id}",
    "GET  /v1/events/{id}/related",
    "POST /v1/events/{id}/favorite",
    "GET  /v1/me  /v1/me/favorites  /v1/me/history  /v1/me/settings",
    "POST /v1/me/settings   /v1/me/history/clear",
    "POST /v1/auth/sms (phone)   /v1/auth/login (type=phone|member, phone, code=1234)",
    "GET  /v1/membership/plans",
    "POST /v1/membership/orders  /v1/membership/verify",
    "GET  /v1/reports  /v1/reports/mine  /v1/reports/{id}",
    "POST /v1/reports/{id}/purchase",
    "GET  /v1/admin/events?status=&module=&q=  /v1/admin/sources  /v1/admin/stats/{pipeline|business}",
    "POST /v1/admin/events/{id}/{approve|reject|publish|pin|unpin|push}",
    "POST /v1/admin/events/{id}/edit   /v1/admin/events/merge",
    "GET  /v1/admin/push/history   /v1/admin/push/digest",
    "POST /v1/admin/push/digest   /v1/admin/push/digest/send",
    "GET  /v1/admin/sources/suggest",
    "POST /v1/admin/sources   /v1/admin/sources/{id}   /v1/admin/sources/{id}/delete",
    "POST /v1/admin/sources/import   /v1/admin/sources/dedup-scan",
    "GET  /v1/admin/roles   /v1/admin/members",
    "POST /v1/admin/members   /v1/admin/members/{id}   /v1/admin/members/{id}/delete",
    "GET  /v1/admin/users?tier=&status=&q=   /v1/admin/users/{id}",
    "POST /v1/admin/users/{id} (action=ban|unban|set_tier|extend)",
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
