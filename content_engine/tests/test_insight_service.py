"""热点透视 service 层单测（services/insight.py）。

- build_prompt / parse_insight_response / rank_related：纯函数，不触 DB/LLM；
- claim_for_generation：SQLite in-memory 只建 event_analyses 表（JSON with_variant 兼容）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.models import EventAnalysis
from content_engine.services import insight
from content_engine.services.insight import (
    InsightParseError,
    build_prompt,
    claim_for_generation,
    parse_insight_response,
    rank_related,
)
from content_engine.services.llm_client import LLMResponse

NOW = datetime(2026, 8, 4, 12, 0, 0, tzinfo=timezone.utc)


def _resp(payload: dict) -> LLMResponse:
    return LLMResponse(
        content=__import__("json").dumps(payload, ensure_ascii=False),
        model="deepseek-chat",
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        cost=0.001,
    )


def _sections(h=80, c=100, f=60) -> dict:
    return {"history": "史" * h, "current": "现" * c, "forecast": "预" * f}


def _materials(**over) -> dict:
    base = {
        "title": "某大厂发布新模型",
        "detail_summary": "详情摘要……",
        "why_matters": "为何重要……",
        "facts": [{"text": "事实一"}, "事实二"],
        "sources": [
            {"name": "路透", "level": "S", "title": "报道标题", "excerpt": "正文" * 10},
        ],
        "related": [{"id": 9, "date": "2026-05-12", "title": "历史事件", "excerpt": "摘要"}],
        "related_ids": [9],
        "fingerprint": "fp",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------
def test_prompt_contains_sections_json_keyword_and_materials_order():
    p = build_prompt(_materials())
    # 三段指令 + DeepSeek JSON mode 硬性要求的「JSON」字样
    for key in ("history", "current", "forecast"):
        assert key in p
    assert "JSON" in p
    # 素材顺序：本事件资料 → 多源原文 → 相关历史事件
    # （用带上下文的唯一标记，避开规则区的同名引用）
    assert (
        p.index("【本事件资料】\n标题")
        < p.index("多源原文（按信源级别排序）")
        < p.index("【相关历史事件】（按相关度排序）")
    )
    # 事实两种形态都能渲染
    assert "事实一" in p and "事实二" in p
    # 推演段措辞约束 + 不自写免责声明（系统追加）
    assert "可能" in p and "不要自己写免责声明" in p


def test_prompt_without_related_events_degrades_gracefully():
    p = build_prompt(_materials(related=[]))
    assert "（库内无相关历史事件）" in p
    assert "禁止虚构历史节点" in p


# ---------------------------------------------------------------------------
# parse_insight_response
# ---------------------------------------------------------------------------
def test_parse_ok():
    out = parse_insight_response(_resp(_sections()))
    assert out["history"] == "史" * 80
    assert set(out) == {"history", "current", "forecast"}


def test_parse_missing_key_raises():
    bad = _sections()
    del bad["forecast"]
    with pytest.raises(InsightParseError):
        parse_insight_response(_resp(bad))


def test_parse_non_string_raises():
    bad = _sections()
    bad["current"] = ["不是字符串"]
    with pytest.raises(InsightParseError):
        parse_insight_response(_resp(bad))


def test_parse_too_short_raises():
    with pytest.raises(InsightParseError):
        parse_insight_response(_resp(_sections(h=10)))


def test_parse_truncates_overlong_sections():
    out = parse_insight_response(_resp(_sections(h=800, c=900, f=700)))
    assert len(out["history"]) == insight.HISTORY_MAX_CHARS
    assert len(out["current"]) == insight.CURRENT_MAX_CHARS
    assert len(out["forecast"]) == insight.FORECAST_MAX_CHARS


def test_parse_invalid_json_raises():
    with pytest.raises(__import__("json").JSONDecodeError):
        parse_insight_response(LLMResponse("不是json", "m", None, 0.0))


# ---------------------------------------------------------------------------
# rank_related（纯函数：SimpleNamespace 候选，不触 DB）
# ---------------------------------------------------------------------------
def _ev(id: int, centroid) -> SimpleNamespace:
    return SimpleNamespace(id=id, centroid=centroid)


def test_rank_related_excludes_self_and_sorts_by_cos():
    event = _ev(1, [1.0, 0.0])
    same = _ev(1, [1.0, 0.0])          # 自身
    close = _ev(2, [0.9, 0.1])         # cos≈0.99
    far = _ev(3, [0.0, 1.0])           # cos=0
    none_c = _ev(4, None)              # 无 centroid
    out = rank_related(event, [same, far, close, none_c])
    assert [e.id for e in out] == [2]


def test_rank_related_threshold_and_topk():
    event = _ev(1, [1.0, 0.0])
    # 构造 6 个高相关 + 1 个低于阈值
    candidates = [_ev(10 + i, [1.0, 0.01 * i]) for i in range(6)]
    candidates.append(_ev(99, [0.2, 0.98]))  # cos≈0.2 < 0.5
    out = rank_related(event, candidates)
    assert len(out) == insight.RELATED_TOP_K
    assert 99 not in [e.id for e in out]
    # 排序：cos 最高的排最前（[1.0, 0.0] 与自身方向一致度最高）
    assert out[0].id == 10


def test_rank_related_no_centroid_returns_empty():
    assert rank_related(_ev(1, None), [_ev(2, [1.0, 0.0])]) == []


# ---------------------------------------------------------------------------
# claim_for_generation（SQLite，仅 event_analyses 表）
# ---------------------------------------------------------------------------
@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    EventAnalysis.__table__.create(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)


def _get(session_factory, event_id=7) -> EventAnalysis:
    with session_factory() as s:
        return s.execute(
            select(EventAnalysis).where(EventAnalysis.event_id == event_id)
        ).scalar_one()


def test_claim_creates_pending_and_enqueues(session_factory):
    with session_factory() as s:
        row, should = claim_for_generation(s, 7, NOW)
        s.commit()
    assert should is True
    assert row.status == "pending"
    assert _get(session_factory).event_id == 7


def test_claim_inflight_does_not_enqueue(session_factory):
    with session_factory() as s:
        s.add(EventAnalysis(event_id=7, status="generating"))
        s.commit()
    with session_factory() as s:
        row, should = claim_for_generation(s, 7, NOW)
    assert should is False
    assert row.status == "generating"


def test_claim_ready_does_not_enqueue(session_factory):
    with session_factory() as s:
        s.add(EventAnalysis(event_id=7, status="ready", sections=_sections()))
        s.commit()
    with session_factory() as s:
        _, should = claim_for_generation(s, 7, NOW)
    assert should is False


def test_claim_failed_requeues(session_factory):
    with session_factory() as s:
        s.add(EventAnalysis(event_id=7, status="failed", error="x"))
        s.commit()
    with session_factory() as s:
        row, should = claim_for_generation(s, 7, NOW)
        s.commit()
    assert should is True
    assert row.status == "pending"
    assert _get(session_factory).error is None


def test_claim_stale_inflight_requeues(session_factory):
    stale_at = NOW - timedelta(seconds=insight.STALE_SECONDS + 60)
    with session_factory() as s:
        s.add(EventAnalysis(event_id=7, status="generating", updated_at=stale_at))
        s.commit()
    with session_factory() as s:
        _, should = claim_for_generation(s, 7, NOW)
        s.commit()
    assert should is True
    assert _get(session_factory).status == "pending"


def test_claim_fresh_inflight_not_stale(session_factory):
    fresh_at = NOW - timedelta(seconds=insight.STALE_SECONDS - 60)
    with session_factory() as s:
        s.add(EventAnalysis(event_id=7, status="pending", updated_at=fresh_at))
        s.commit()
    with session_factory() as s:
        _, should = claim_for_generation(s, 7, NOW)
    assert should is False
