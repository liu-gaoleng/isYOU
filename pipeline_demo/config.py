"""「热读」内容管线 Demo —— 配置

包含：信源清单（含来源等级 S/A/B）、分类关键词规则。
选用公开、无需鉴权的中英文 RSS 源；某源不可用时 Demo 会自动跳过。
"""

# 信源清单（对应《内容管线方案》§2.1 分级、§2.2 种子信源）
SOURCES = [
    {"name": "36氪",            "url": "https://36kr.com/feed",                          "level": "A", "module": "科技"},
    {"name": "少数派",          "url": "https://sspai.com/feed",                         "level": "B", "module": "科技"},
    {"name": "机器之心",        "url": "https://www.jiqizhixin.com/rss",                 "level": "A", "module": "AI"},
    {"name": "MIT Tech Review", "url": "https://www.technologyreview.com/feed/",         "level": "A", "module": "AI"},
    {"name": "华尔街见闻",      "url": "https://dedicated.wallstreetcn.com/rss.xml",     "level": "A", "module": "金融"},
    {"name": "36氪-资本",       "url": "https://36kr.com/feed-newsflash",                "level": "A", "module": "金融"},
    {"name": "TechCrunch",      "url": "https://techcrunch.com/feed/",                   "level": "A", "module": "科技"},
    {"name": "Ars Technica",    "url": "https://feeds.arstechnica.com/arstechnica/index","level": "A", "module": "科技"},
]

# 来源等级权重（对应方案 §2.1 / §7.1）
LEVEL_WEIGHT = {"S": 1.0, "A": 0.7, "B": 0.3}

# 分类关键词规则（规则兜底，对应方案 §4）
CLASSIFY_RULES = {
    "AI":   ["AI", "人工智能", "大模型", "GPT", "模型", "算力", "芯片", "Agent", "机器学习",
             "深度学习", "LLM", "神经网络", "OpenAI", "推理", "训练"],
    "金融": ["股", "融资", "估值", "IPO", "上市", "并购", "基金", "央行", "利率", "美元",
             "比特币", "加密", "财报", "营收", "投资", "市值", "美股", "A股"],
    "宏观": ["GDP", "CPI", "通胀", "经济", "政策", "财政", "货币", "出口", "进口", "就业",
             "楼市", "地产", "统计局", "降准", "降息", "关税"],
    "科技": ["发布", "新品", "手机", "平台", "出海", "监管", "互联网", "数码", "操作系统",
             "硬件", "软件", "应用", "智能", "电动车", "自动驾驶"],
}

# 去重 / 聚类阈值（对应方案 §3.2 / §5.2，Demo 用轻量 Jaccard 近似向量相似度）
DEDUP_THRESHOLD = 0.75      # 标题+正文相似度 ≥ 此值视为重复/同事件
CLUSTER_THRESHOLD = 0.45    # 事件聚类相似度阈值（偏保守，防串卡）

# 每个源最多取多少条（Demo 控量）
MAX_PER_SOURCE = 12
