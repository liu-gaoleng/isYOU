"""管线状态枚举。

阶段状态机贯穿 raw_articles 与 events，支持断点重跑：
任何阶段失败后，按 status 重新拉取待处理数据再入队即可。
"""

from __future__ import annotations

import enum


class ArticleStatus(str, enum.Enum):
    """单篇原文的状态机（阶段 1~2 流转）。

    raw         : 刚采集落库，未清洗
    cleaned     : 已清洗、已生成 embedding
    classified  : 已分类（含规则兜底或 LLM）
    clustered   : 已并入或新建事件簇
    dropped     : 被质量门槛或精确去重淘汰（保留以便复盘）
    """

    raw = "raw"
    cleaned = "cleaned"
    classified = "classified"
    clustered = "clustered"
    dropped = "dropped"


class EventStatus(str, enum.Enum):
    """事件簇的状态机（阶段 3~4 流转）。

    clustered  : 刚生成或刚追加成员，尚未生成摘要
    summarized : 已生成摘要/解读
    scored     : 已计算 importance 并写入榜单
    reviewing  : 进入人工待审（低置信 / 必检 / 护栏命中）
    published  : 已通过审核，对外发布
    rejected   : 人工/自动驳回，不发布
    """

    clustered = "clustered"
    summarized = "summarized"
    scored = "scored"
    reviewing = "reviewing"
    published = "published"
    rejected = "rejected"


class SourceLevel(str, enum.Enum):
    """信源分级（沿用 PRD §2.2 / 方案 §2.1）。"""

    S = "S"  # 官方 / 监管 / 头部财经，权重 1.0
    A = "A"  # 垂直专业媒体、研报，权重 0.7
    B = "B"  # 自媒体 / 社交，权重 0.3，仅作热度信号


class Module(str, enum.Enum):
    """四大业务模块。"""

    tech = "tech"
    finance = "finance"
    ai = "ai"
    macro = "macro"
