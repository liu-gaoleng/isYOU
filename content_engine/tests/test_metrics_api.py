"""阶段 D 单测：可观测性报表接口 + LLM 成本换算 + pipeline_run 落库。

不依赖真实 PG：metrics 用真实 SQLite in-memory（建非 pgvector 表）+ monkeypatch
get_session；estimate_cost 纯函数直接断言。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_engine.config import settings
from content_engine.models.enums import Module
from content_engine.services.llm_client import estimate_cost

TOKEN = "test-admin-token"
NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# estimate_cost 纯函数
# ---------------------------------------------------------------------------
def test_estimate_cost_none_usage_zero():
    assert estimate_cost(None, 1.0, 2.0) == 0.0


def test_estimate_cost_zero_price_zero():
    usage = {"prompt_tokens": 1000, "completion_tokens": 1000}
    assert estimate_cost(usage, 0.0, 0.0) == 0.0


def test_estimate_cost_basic():
    usage = {"prompt_tokens": 2000, "completion_tokens": 1000}
    # 2000/1000*0.5 + 1000/1000*1.5 = 1.0 + 1.5 = 2.5
    assert estimate_cost(usage, 0.5, 1.5) == 2.5


def test_estimate_cost_missing_fields():
    usage = {"prompt_tokens": 500}
    assert estimate_cost(usage, 1.0, 2.0) == 0.5


# ---------------------------------------------------------------------------
# metrics API（真实 SQLite in-memory）
# ---------------------------------------------------------------------------
@pytest.fixture
def metrics_session():
    """建 metrics 涉及的非 pgvector 表：events / review_logs / event_contents /
    raw_articles / sources / source_health / pipeline_runs。

    raw_articles / events 含 Vector 列，SQLite 不支持，故只建用得到的列子集——
    用 sqlite 时 Vector 列在 create_all 会报错，这里改为逐表用 schema 中无 Vector
    的表 + 对含 Vector 表做特殊处理。
    """
    from sqlalchemy import (
        JSON,
        BigInteger,
        Column,
        DateTime,
        Float,
        Integer,
        String,
    )
    from sqlalchemy.orm import declarative_base
    from sqlalchemy.pool import StaticPool

    from content_engine.models import PipelineRun, SourceHealth
    from content_engine.models.schema import Source

    # TestClient 在独立线程跑 endpoint，故用 StaticPool + check_same_thread=False
    # 让同一块 in-memory DB 可跨线程共享。
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # 含 Vector/JSONB 列的 events / raw_articles / event_contents / review_logs
    # 不能直接在 SQLite create_all，这里为单测临时建等价（无 Vector / JSON 代 JSONB）
    # 的影子表，列名与 metrics 查询字段一致。
    ShadowBase = declarative_base()

    class _Event(ShadowBase):
        __tablename__ = "events"
        id = Column(Integer, primary_key=True, autoincrement=True)
        module = Column(String(16), nullable=False)
        status = Column(String(16), nullable=False)
        importance = Column(Float, default=0.0)
        last_update = Column(DateTime(timezone=True), nullable=False)

    class _RawArticle(ShadowBase):
        __tablename__ = "raw_articles"
        id = Column(Integer, primary_key=True, autoincrement=True)
        cls_confidence = Column(Float, nullable=True)

    class _EventContent(ShadowBase):
        __tablename__ = "event_contents"
        id = Column(Integer, primary_key=True, autoincrement=True)
        llm_meta = Column(JSON, nullable=True)

    class _ReviewLog(ShadowBase):
        __tablename__ = "review_logs"
        id = Column(Integer, primary_key=True, autoincrement=True)
        event_id = Column(BigInteger, nullable=False)
        reviewer = Column(String(64), nullable=False)
        action = Column(String(32), nullable=False)
        created_at = Column(DateTime(timezone=True), nullable=False)

    Source.__table__.create(engine)
    SourceHealth.__table__.create(engine)
    PipelineRun.__table__.create(engine)
    ShadowBase.metadata.create_all(engine)

    SessionLocal = sessionmaker(engine, expire_on_commit=False, future=True)
    s = SessionLocal()
    try:
        yield s, _Event, _RawArticle, _EventContent, _ReviewLog
    finally:
        s.close()


@pytest.fixture
def client(metrics_session, monkeypatch):
    s, _Event, _RawArticle, _EventContent, _ReviewLog = metrics_session

    # 种子数据
    from content_engine.models import PipelineRun, SourceHealth
    from content_engine.models.enums import SourceLevel
    from content_engine.models.schema import Source

    # 事件：3 published（2 个同日 + 1 个另一日）、1 reviewing
    s.add_all([
        _Event(module="tech", status="published", last_update=NOW),
        _Event(module="tech", status="published", last_update=NOW),
        _Event(module="finance", status="published", last_update=NOW - timedelta(days=1)),
        _Event(module="ai", status="reviewing", last_update=NOW),
    ])
    # 分类置信度
    s.add_all([
        _RawArticle(cls_confidence=0.9),
        _RawArticle(cls_confidence=0.6),
        _RawArticle(cls_confidence=0.3),
        _RawArticle(cls_confidence=None),
    ])
    # 护栏：3 条内容，1 条命中
    s.add_all([
        _EventContent(llm_meta={"guard": {"violations": ["数字一致性存疑"]}}),
        _EventContent(llm_meta={"guard": {"violations": []}}),
        _EventContent(llm_meta={"model": "x"}),  # 无 guard，不计入 checked
    ])
    # 质检：2 approve + 1 reject => pass_rate = 2/3
    s.add_all([
        _ReviewLog(event_id=1, reviewer="a", action="approve", created_at=NOW),
        _ReviewLog(event_id=2, reviewer="a", action="approve", created_at=NOW),
        _ReviewLog(event_id=3, reviewer="a", action="reject", created_at=NOW),
    ])
    # 信源 + 健康记录
    src = Source(name="S1", level=SourceLevel.A, type="rss",
                 url="https://e.com/rss", module=Module.tech, weight=1.0, enabled=True)
    s.add(src)
    s.flush()
    s.add_all([
        SourceHealth(source_id=src.id, fetched_at=NOW - timedelta(minutes=10),
                     status="failed", consecutive_failures=1),
        SourceHealth(source_id=src.id, fetched_at=NOW,
                     status="failed", consecutive_failures=2),  # 最近一次仍失败
    ])
    # 管线运行：1 success + 1 failed
    s.add_all([
        PipelineRun(trigger="manual", status="success", started_at=NOW,
                    finished_at=NOW, duration_ms=1200, stages={"collect": {}}, llm_cost=0.5),
        PipelineRun(trigger="manual", status="failed", started_at=NOW,
                    duration_ms=300, stages={}, llm_cost=0.1, error="clean: X"),
    ])
    s.commit()

    @contextmanager
    def fake_get_session():
        # 复用同一会话（不真正关闭）
        yield s

    from content_engine.api.routers import metrics as metrics_mod

    monkeypatch.setattr(metrics_mod, "get_session", fake_get_session)
    monkeypatch.setattr(settings.admin, "token", TOKEN)

    from content_engine.api.app import app

    with TestClient(app) as c:
        yield c


def _h(token=TOKEN):
    return {"X-Admin-Token": token}


def test_overview_missing_token_401(client):
    assert client.get("/api/v1/admin/metrics/overview").status_code == 401


def test_overview_ok(client):
    r = client.get("/api/v1/admin/metrics/overview?days=30", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["events_total"] == 4
    assert body["events_by_status"]["published"] == 3
    assert body["events_by_module"]["tech"] == 2
    # 质检通过率 2 approve / (2 approve + 1 reject) = 0.6667
    assert body["review_action_counts"]["approve"] == 2
    assert round(body["review_pass_rate"], 2) == 0.67
    # 护栏：checked=2（有 guard 的），intercepted=1
    assert body["guard_checked"] == 2
    assert body["guard_intercepted"] == 1
    assert body["guard_interception_rate"] == 0.5
    # 置信度桶
    cb = body["classification_confidence"]
    assert cb == {"high": 1, "mid": 1, "low": 1, "unknown": 1}
    # 信源健康：最近一次仍失败 => failing_sources=1
    assert body["source_health"]["failing_sources"] == 1
    assert body["source_health"]["status_counts"]["failed"] == 2
    # 管线：2 次，1 成功 => 0.5；LLM 成本 0.6
    assert body["pipeline_runs_total"] == 2
    assert body["pipeline_success_rate"] == 0.5
    assert round(body["llm_cost_total"], 2) == 0.6
    # 按日发布量：两天
    assert len(body["daily_published"]) == 2


def test_pipeline_runs_ok(client):
    r = client.get("/api/v1/admin/metrics/pipeline-runs", headers=_h())
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 2
    statuses = {x["status"] for x in runs}
    assert statuses == {"success", "failed"}


def test_pipeline_runs_filter_status(client):
    r = client.get("/api/v1/admin/metrics/pipeline-runs?status=failed", headers=_h())
    assert r.status_code == 200
    runs = r.json()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert runs[0]["error"] == "clean: X"
