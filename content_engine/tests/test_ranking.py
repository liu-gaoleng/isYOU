"""阶段 3.4 单测：Redis 榜单 ZSet 写读 + 降级。

不依赖真实 Redis：用内存版 FakeRedis 实现 zadd/zrevrange/delete/zremrangebyrank/pipeline，
通过 monkeypatch 注入 ``ranking._get_client``。另测两条降级路径（enabled=False / 连接失败）。
"""

from __future__ import annotations

from content_engine.services import ranking


class _FakePipeline:
    def __init__(self, store: dict):
        self._store = store
        self._ops: list = []

    def delete(self, key):
        self._ops.append(("delete", key))
        return self

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, dict(mapping)))
        return self

    def zremrangebyrank(self, key, start, stop):
        self._ops.append(("zremrangebyrank", key, start, stop))
        return self

    def execute(self):
        for op in self._ops:
            if op[0] == "delete":
                self._store.pop(op[1], None)
            elif op[0] == "zadd":
                self._store.setdefault(op[1], {}).update(op[2])
            elif op[0] == "zremrangebyrank":
                key, start, stop = op[1], op[2], op[3]
                items = sorted(self._store.get(key, {}).items(), key=lambda kv: kv[1])
                # 删除 [start, stop] 排名区间（负索引按 Redis 语义）
                idx = list(range(len(items)))
                sel = set(idx[start:] if stop == -1 else idx[start : stop + 1])
                self._store[key] = {
                    k: v for i, (k, v) in enumerate(items) if i not in sel
                }
        self._ops.clear()


class _FakeRedis:
    def __init__(self):
        self._store: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return _FakePipeline(self._store)

    def zrevrange(self, key, start, stop):
        items = sorted(
            self._store.get(key, {}).items(), key=lambda kv: kv[1], reverse=True
        )
        end = None if stop == -1 else stop + 1
        return [k for k, _ in items[start:end]]


def test_rebuild_and_top(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ranking, "_get_client", lambda: fake)

    rows = [
        (1, "tech", 90.0),
        (2, "tech", 70.0),
        (3, "finance", 80.0),
    ]
    written = ranking.rebuild(rows)
    assert written == 3

    # 全站榜按分数倒序
    assert ranking.top(None, 10) == [1, 3, 2]
    # 分模块榜
    assert ranking.top("tech", 10) == [1, 2]
    assert ranking.top("finance", 10) == [3]


def test_top_respects_limit(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ranking, "_get_client", lambda: fake)
    ranking.rebuild([(i, "tech", float(i)) for i in range(1, 6)])
    assert ranking.top(None, 2) == [5, 4]


def test_rebuild_keep_top_trims(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(ranking, "_get_client", lambda: fake)
    monkeypatch.setattr(ranking.settings.ranking, "keep_top", 2)
    ranking.rebuild([(i, "tech", float(i)) for i in range(1, 6)])
    # 仅保留分数最高的 2 个：5、4
    assert ranking.top(None, 10) == [5, 4]


def test_degrade_when_disabled(monkeypatch):
    monkeypatch.setattr(ranking.settings.ranking, "enabled", False)
    assert ranking.rebuild([(1, "tech", 1.0)]) == 0
    assert ranking.top(None, 10) is None


def test_degrade_when_client_unavailable(monkeypatch):
    monkeypatch.setattr(ranking, "_get_client", lambda: None)
    assert ranking.rebuild([(1, "tech", 1.0)]) == 0
    assert ranking.top(None, 10) is None
