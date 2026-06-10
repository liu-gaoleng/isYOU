"""阶段 0 起步用的种子配置。

后续会迁到 DB 表 sources 由后台维护；当前先用 Python 常量灌库。
信源、关键词、来源等级权重均与 pipeline_demo/config.py 对齐。

阶段 1.6（iOS-first 调整）：信源按目标人群六大画像划分——
互联网产品 / 大厂战略 / 一级市场融资 / AI 应用层 / 出海 / 创业者实操。
现阶段先把现有 8 个源按新画像归类（用 tags 标记，避免改 Module 枚举），
后续人工补足到 ≥40 个时直接挂相应画像。
"""

from __future__ import annotations

from content_engine.models import Module, SourceLevel

# -------- 阶段 1.6：六大目标人群画像（用 tag 表达，与 Module 枚举正交） --------
# 现阶段挂在 seed 字典里，后续可迁到 sources.tags JSONB 字段
PERSONA_TAGS: dict[str, str] = {
    "internet_product": "互联网产品",
    "big_tech_strategy": "大厂战略",
    "vc_funding": "一级市场融资",
    "ai_application": "AI 应用层",
    "going_global": "出海",
    "founder_ops": "创业者实操",
}


# 信源种子（阶段 1.6：扩到 ≥40 源，覆盖六大画像 + 四大模块 macro 补足）
# `personas` 为可选字段，描述该源主要服务于哪些目标人群画像；后续会迁到 sources.tags JSONB。
# 维护原则：URL 不通时由 source_health 表自动告警，逐步替换。
SEED_SOURCES: list[dict] = [
    # ---------- 互联网产品 (internet_product) ----------
    {
        "name": "36氪",
        "url": "https://36kr.com/feed",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["internet_product", "big_tech_strategy"],
    },
    {
        "name": "少数派",
        "url": "https://sspai.com/feed",
        "level": SourceLevel.B,
        "module": Module.tech,
        "personas": ["internet_product"],
    },
    {
        "name": "极客公园",
        "url": "https://www.geekpark.net/rss",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["internet_product", "big_tech_strategy"],
        "enabled": False,  # 阶段 1.6 排查：Cloudflare 拦截 / 连接被拒，等待人工找替代
    },
    {
        "name": "虎嗅",
        "url": "https://rss.huxiu.com/",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["internet_product", "founder_ops"],
    },
    {
        "name": "人人都是产品经理",
        "url": "http://www.woshipm.com/feed",
        "level": SourceLevel.B,
        "module": Module.tech,
        "personas": ["internet_product"],
    },
    {
        "name": "PingWest品玩",
        "url": "https://www.pingwest.com/feed/",
        "level": SourceLevel.B,
        "module": Module.tech,
        "personas": ["internet_product", "big_tech_strategy"],
        "enabled": False,  # 阶段 1.6 排查：feed 路径已下架（405 / 404）
    },
    {
        "name": "钛媒体",
        "url": "https://www.tmtpost.com/rss.xml",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["internet_product", "vc_funding", "founder_ops"],
    },

    # ---------- 大厂战略 (big_tech_strategy) ----------
    {
        "name": "Ars Technica",
        "url": "https://arstechnica.com/feed/",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["big_tech_strategy"],
    },
    {
        "name": "Stratechery",
        "url": "https://stratechery.com/feed/",
        "level": SourceLevel.S,
        "module": Module.tech,
        "personas": ["big_tech_strategy"],
    },
    {
        "name": "The Verge",
        "url": "https://www.theverge.com/rss/index.xml",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["big_tech_strategy", "internet_product"],
    },
    {
        "name": "Wired",
        "url": "https://www.wired.com/feed/rss",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["big_tech_strategy"],
    },
    {
        "name": "Reuters Technology",
        "url": "https://www.reutersagency.com/feed/?best-topics=tech&post_type=best",
        "level": SourceLevel.S,
        "module": Module.tech,
        "personas": ["big_tech_strategy", "going_global"],
        "enabled": False,  # 阶段 1.6 排查：404，Reuters 已迁付费 API
    },

    # ---------- 一级市场融资 (vc_funding) ----------
    {
        "name": "华尔街见闻",
        "url": "https://dedicated.wallstreetcn.com/rss.xml",
        "level": SourceLevel.A,
        "module": Module.finance,
        "personas": ["vc_funding"],
    },
    {
        "name": "36氪-资本",
        "url": "https://36kr.com/feed-newsflash",
        "level": SourceLevel.A,
        "module": Module.finance,
        "personas": ["vc_funding", "founder_ops"],
    },
    {
        "name": "投中网",
        "url": "https://www.chinaventure.com.cn/rss",
        "level": SourceLevel.A,
        "module": Module.finance,
        "personas": ["vc_funding"],
        "enabled": False,  # 阶段 1.6 排查：403 Forbidden，需 cookie/UA 白名单
    },
    {
        "name": "创业邦",
        "url": "https://www.cyzone.cn/rss/",
        "level": SourceLevel.B,
        "module": Module.finance,
        "personas": ["vc_funding", "founder_ops"],
    },
    {
        "name": "投资界",
        "url": "https://www.pedaily.cn/rss/",
        "level": SourceLevel.A,
        "module": Module.finance,
        "personas": ["vc_funding"],
        "enabled": False,  # 阶段 1.6 排查：404，路径变更未发现新地址
    },
    {
        "name": "财联社",
        "url": "https://www.cls.cn/feed.xml",
        "level": SourceLevel.A,
        "module": Module.finance,
        "personas": ["vc_funding", "founder_ops"],
        "enabled": False,  # 阶段 1.6 排查：404，财联社未公开 RSS 入口
    },

    # ---------- AI 应用层 (ai_application) ----------
    {
        "name": "机器之心",
        "url": "https://www.jiqizhixin.com/rss",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application"],
    },
    {
        "name": "MIT Tech Review",
        "url": "https://www.technologyreview.com/feed/",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application", "big_tech_strategy"],
    },
    {
        "name": "量子位",
        "url": "https://www.qbitai.com/feed",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application"],
    },
    {
        "name": "硅星人",
        "url": "https://www.guixingren.com/feed/",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application", "big_tech_strategy"],
        "enabled": False,  # 阶段 1.6 排查：DNS 解析失败，域名疑已弃用
    },
    {
        "name": "智东西",
        "url": "https://zhidx.com/feed",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application"],
        "enabled": False,  # 阶段 1.6 排查：500 内部错误（WordPress feed 故障）
    },
    {
        "name": "The Decoder",
        "url": "https://the-decoder.com/feed/",
        "level": SourceLevel.B,
        "module": Module.ai,
        "personas": ["ai_application"],
    },
    {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog/feed.xml",
        "level": SourceLevel.A,
        "module": Module.ai,
        "personas": ["ai_application"],
    },
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "level": SourceLevel.S,
        "module": Module.ai,
        "personas": ["ai_application", "big_tech_strategy"],
    },

    # ---------- 出海 (going_global) ----------
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["going_global", "vc_funding"],
    },
    {
        "name": "Rest of World",
        "url": "https://restofworld.org/feed/latest/",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["going_global"],
        "enabled": False,  # 阶段 1.6 排查：连接超时，可能站点封锁海外抓取
    },
    {
        "name": "白鲸出海",
        "url": "https://www.baijing.cn/feed",
        "level": SourceLevel.A,
        "module": Module.tech,
        "personas": ["going_global"],
    },
    {
        "name": "雷峰网",
        "url": "https://www.leiphone.com/feed",
        "level": SourceLevel.B,
        "module": Module.tech,
        "personas": ["going_global", "ai_application"],
    },
    {
        "name": "Nikkei Asia",
        "url": "https://asia.nikkei.com/rss/feed/nar",
        "level": SourceLevel.S,
        "module": Module.finance,
        "personas": ["going_global"],
    },
    {
        "name": "Bloomberg Markets",
        "url": "https://feeds.bloomberg.com/markets/news.rss",
        "level": SourceLevel.S,
        "module": Module.finance,
        "personas": ["going_global", "vc_funding"],
    },

    # ---------- 创业者实操 (founder_ops) ----------
    {
        "name": "经济观察网",
        "url": "https://www.eeo.com.cn/rss.xml",
        "level": SourceLevel.A,
        "module": Module.macro,
        "personas": ["founder_ops"],
    },
    {
        "name": "第一财经",
        "url": "https://www.yicai.com/api/rss/feed",
        "level": SourceLevel.A,
        "module": Module.macro,
        "personas": ["founder_ops", "vc_funding"],
        "enabled": False,  # 阶段 1.6 排查：404，第一财经无公开 RSS（已通过界面新闻替代）
    },
    {
        "name": "Hacker News Front Page",
        "url": "https://hnrss.org/frontpage",
        "level": SourceLevel.B,
        "module": Module.tech,
        "personas": ["founder_ops", "ai_application"],
    },

    # ---------- 宏观 (macro 模块补足) ----------
    {
        "name": "国家统计局",
        "url": "http://www.stats.gov.cn/sj/sjjd/rss.xml",
        "level": SourceLevel.S,
        "module": Module.macro,
        "personas": ["founder_ops"],
    },
    {
        "name": "中国人民银行",
        "url": "http://www.pbc.gov.cn/rss/zhengwugongkai/rss.xml",
        "level": SourceLevel.S,
        "module": Module.macro,
        "personas": ["founder_ops", "vc_funding"],
        "enabled": False,  # 阶段 1.6 排查：404，央行新版站未保留 RSS
    },
    {
        "name": "国务院政策",
        "url": "https://www.gov.cn/zhengce/rss.xml",
        "level": SourceLevel.S,
        "module": Module.macro,
        "personas": ["founder_ops"],
        "enabled": False,  # 阶段 1.6 排查：404，国务院网站未公开 RSS
    },
    {
        "name": "Reuters Business",
        "url": "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best",
        "level": SourceLevel.S,
        "module": Module.macro,
        "personas": ["going_global", "vc_funding"],
        "enabled": False,  # 阶段 1.6 排查：404，Reuters 已迁付费 API
    },
    {
        "name": "Financial Times China",
        "url": "https://www.ft.com/world/asia-pacific/china?format=rss",
        "level": SourceLevel.S,
        "module": Module.macro,
        "personas": ["going_global", "founder_ops"],
    },
    {
        "name": "界面新闻",
        "url": "https://a.jiemian.com/index.php?m=article&a=rss",
        "level": SourceLevel.A,
        "module": Module.macro,
        "personas": ["founder_ops", "vc_funding", "internet_product"],
    },
]

# 来源等级权重（方案 §2.1 / §7.1）
LEVEL_WEIGHT: dict[SourceLevel, float] = {
    SourceLevel.S: 1.0,
    SourceLevel.A: 0.7,
    SourceLevel.B: 0.3,
}

# 分类关键词规则（规则兜底，方案 §4.3；阶段 2.1 会叠加 LLM 分类）
CLASSIFY_RULES: dict[Module, list[str]] = {
    Module.ai: [
        "AI", "人工智能", "大模型", "GPT", "模型", "算力", "芯片", "Agent",
        "机器学习", "深度学习", "LLM", "神经网络", "OpenAI", "推理", "训练",
    ],
    Module.finance: [
        "股", "融资", "估值", "IPO", "上市", "并购", "基金", "央行", "利率",
        "美元", "比特币", "加密", "财报", "营收", "投资", "市值", "美股", "A股",
    ],
    Module.macro: [
        "GDP", "CPI", "通胀", "经济", "政策", "财政", "货币", "出口", "进口",
        "就业", "楼市", "地产", "统计局", "降准", "降息", "关税",
    ],
    Module.tech: [
        "发布", "新品", "手机", "平台", "出海", "监管", "互联网", "数码",
        "操作系统", "硬件", "软件", "应用", "智能", "电动车", "自动驾驶",
    ],
}


__all__ = ["SEED_SOURCES", "LEVEL_WEIGHT", "CLASSIFY_RULES", "PERSONA_TAGS"]
