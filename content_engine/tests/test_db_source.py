"""阶段 4.4 单测：mock_server 真实数据适配层 db_source。

覆盖两条主路径：
1. **降级态**（MOCK_FORCE_JSON=1，DB 不可用）：load_events 读 output.json，
   运营态访问器回退内置 seed，写回函数 no-op，_parse_db_id 返回 None；
2. **事件映射**：_event_to_dict 把真实 Event(+EventContent) 形态映射成
   mock 事件 dict（用轻量 stub 对象，避免依赖 pgvector / PG）。
"""

from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

_DB_SOURCE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "mock_server", "db_source.py",
)


def _load_db_source(monkeypatch, *, force_json=True):
    """按路径全新导入 db_source（DB_ENABLED 在导入时定型，故每次重载）。"""
    if force_json:
        monkeypatch.setenv("MOCK_FORCE_JSON", "1")
    else:
        monkeypatch.delenv("MOCK_FORCE_JSON", raising=False)
    spec = importlib.util.spec_from_file_location("db_source_under_test", _DB_SOURCE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ds(monkeypatch):
    return _load_db_source(monkeypatch, force_json=True)


# ---------------------------------------------------------------------------
# 降级态
# ---------------------------------------------------------------------------
def test_force_json_disables_db(ds):
    assert ds.DB_ENABLED is False


def test_load_events_falls_back_to_json(ds):
    events = ds.load_events()
    assert isinstance(events, list)
    assert len(events) > 0
    e = events[0]
    # 降级态事件 id 形如 evt_1000+i，db_id 为 None
    assert e["event_id"].startswith("evt_10")
    assert e["db_id"] is None
    # 必备字段齐全，供 server.py 直接消费
    for key in ("module", "module_cn", "title", "summary", "importance", "status"):
        assert key in e


def test_parse_db_id_none_when_disabled(ds):
    assert ds._parse_db_id("evt_1001") is None
    assert ds._parse_db_id("not-an-event") is None


def test_persist_functions_are_noop_when_disabled(ds):
    # 降级态写回不应抛异常（best-effort no-op）
    ds.persist_event_status("evt_1001", "published", "tester", "note")
    ds.persist_event_pin("evt_1001", True, "tester")
    ds.persist_event_edit("evt_1001", title="x", summary=["a"], why_matters="y")


def test_ensure_seeded_noop_when_disabled(ds):
    # 不应连接 DB，安静返回
    ds.ensure_seeded()


def test_operational_accessors_return_seed(ds):
    users = ds.load_app_users()
    assert any(u["id"] == "au_1" for u in users)
    assert "orders" in users[0]

    reports = ds.load_reports()
    assert any(r["id"] == "rpt_1" for r in reports)
    assert "desc" in reports[0] and "toc" in reports[0]

    purchases = ds.load_purchases()
    assert purchases.get("guest") == ["rpt_2", "rpt_3"]

    push = ds.load_push_history()
    assert push and "push_id" in push[0] and "sent" in push[0]

    digest = ds.load_digest_config()
    assert digest["enabled"] is True
    assert set(digest["modules"]) == {"tech", "finance", "ai", "macro"}

    members = ds.load_admin_members()
    assert any(m["id"] == "u_1" and m["role"] == "admin" for m in members)

    favorites = ds.load_favorites()
    assert favorites.get("guest")

    history = ds.load_history()
    assert history.get("guest") and "viewed_at" in history["guest"][0]


# ---------------------------------------------------------------------------
# 事件映射（stub Event/EventContent，不触碰真实 DB）
# ---------------------------------------------------------------------------
def _stub_event(**over):
    base = dict(
        id=7, module=SimpleNamespace(value="finance"), importance=88.0,
        source_count=3, hotness=0.42,
        last_update=datetime(2026, 6, 8, 8, 0, tzinfo=timezone.utc),
        status=SimpleNamespace(value="published"),
    )
    base.update(over)
    return SimpleNamespace(**base)


def _stub_content(**over):
    base = dict(
        title="美联储维持利率不变", summary=["第一句", "第二句", "第三句"],
        why_matters="影响全球流动性预期", sources=[{"name": "Reuters"}],
        deep_content="付费全文……", version=2,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_event_to_dict_maps_fields(ds):
    d = ds._event_to_dict(_stub_event(), _stub_content())
    assert d["event_id"] == "evt_7"
    assert d["db_id"] == 7
    assert d["module"] == "finance"
    assert d["module_cn"] == "金融"
    assert d["title"] == "美联储维持利率不变"
    assert d["summary"] == ["第一句", "第二句", "第三句"]
    assert d["status"] == "published"
    assert d["source_count"] == 3
    assert d["hotness"] == int(0.42 * 10000)
    # 金融/宏观自动带免责声明
    assert d["disclaimer"]
    # importance < 100 不置顶
    assert d["pinned"] is False


def test_event_to_dict_pinned_by_high_importance(ds):
    d = ds._event_to_dict(_stub_event(importance=100.0), _stub_content())
    assert d["pinned"] is True


def test_event_to_dict_intermediate_status_maps_to_reviewing(ds):
    d = ds._event_to_dict(
        _stub_event(status=SimpleNamespace(value="scored")), _stub_content()
    )
    assert d["status"] == "reviewing"


def test_event_to_dict_without_content_uses_fallback(ds):
    d = ds._event_to_dict(_stub_event(module=SimpleNamespace(value="tech")), None)
    assert d["title"] == ""
    assert d["summary"] == []
    assert d["deep_content_full"]  # 兜底深度全文
    assert d["disclaimer"] == ""   # 科技无免责
