"""阶段 3.4：Redis 榜单（ZSet）。

为首页 / 频道 TOP-N 提供 O(logN) 排序读取，避免每次实时扫 events 表排序。

键设计：
- ``rank:all``        全站榜（所有模块混排）
- ``rank:{module}``   分模块榜（tech / finance / ai / macro）
- member = event_id（字符串），score = importance

写入（score 阶段调用 ``rebuild``）：
- 用 pipeline 先 DEL 再 ZADD，保证与 DB 当前快照一致；
- 每个 ZSet 仅保留 TOP ``settings.ranking.keep_top``，``ZREMRANGEBYRANK`` 裁尾。

读取（``top``）：``ZREVRANGE`` 取分数最高的 N 个 event_id。

降级（铁律：可降级）：
- ``settings.ranking.enabled=False`` 或 Redis 连接失败 → 写入静默跳过、读取返回 None；
- 调用方（score / API）据此回退到 DB ``ORDER BY importance``。
"""

from __future__ import annotations

import logging

from content_engine.config import settings
from content_engine.models import Module

logger = logging.getLogger(__name__)

_ALL_KEY = "rank:all"


def _module_key(module: str) -> str:
    return f"rank:{module}"


def _get_client():
    """惰性建连；失败返回 None（触发降级）。"""
    if not settings.ranking.enabled:
        return None
    try:
        import redis

        client = redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return client
    except Exception as e:  # 连接/导入失败都降级
        logger.warning("[ranking] Redis 不可用，降级跳过：%s", e)
        return None


def rebuild(rows: list[tuple[int, str, float]]) -> int:
    """用当前快照重建榜单 ZSet。

    Args:
        rows: [(event_id, module, importance), ...]
    Returns:
        写入的成员条数（降级时返回 0）。
    """
    client = _get_client()
    if client is None:
        return 0

    keep = settings.ranking.keep_top
    all_mapping: dict[str, float] = {}
    module_mapping: dict[str, dict[str, float]] = {m.value: {} for m in Module}
    for event_id, module, importance in rows:
        all_mapping[str(event_id)] = importance
        if module in module_mapping:
            module_mapping[module][str(event_id)] = importance

    try:
        pipe = client.pipeline()
        pipe.delete(_ALL_KEY)
        if all_mapping:
            pipe.zadd(_ALL_KEY, all_mapping)
            # ZREMRANGEBYRANK 删除排名最低的（保留分数最高的 keep 个）
            pipe.zremrangebyrank(_ALL_KEY, 0, -keep - 1)
        for module, mapping in module_mapping.items():
            key = _module_key(module)
            pipe.delete(key)
            if mapping:
                pipe.zadd(key, mapping)
                pipe.zremrangebyrank(key, 0, -keep - 1)
        pipe.execute()
        return len(all_mapping)
    except Exception as e:
        logger.warning("[ranking] 写入失败，降级：%s", e)
        return 0


def top(module: str | None = None, n: int = 10) -> list[int] | None:
    """取榜单 TOP-N 的 event_id（按 importance 倒序）。

    Returns:
        event_id 列表；Redis 不可用时返回 None（调用方回退 DB）。
    """
    client = _get_client()
    if client is None:
        return None
    key = _module_key(module) if module else _ALL_KEY
    try:
        members = client.zrevrange(key, 0, n - 1)
        return [int(m) for m in members]
    except Exception as e:
        logger.warning("[ranking] 读取失败，降级：%s", e)
        return None


__all__ = ["rebuild", "top"]
