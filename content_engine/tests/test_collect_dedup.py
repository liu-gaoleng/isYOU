"""collect 去重回滚修复的回归测试。

曾经的 bug（「collect 新增几十但 clean 处理 0」的根因）：
旧实现遇到重复 URL 时整事务 ``s.rollback()``，把本轮已 flush 的新文章一并抹掉——
RSS feed 新文章在前、历史重复条目在后，一旦撞上重复，本轮全部新文章被静默吞掉。
修复后：SAVEPOINT（begin_nested）只回滚当前重复条目，新文章必须全部入库。

真实 RawArticle 含 JSONB/Vector 列无法 SQLite 建表，这里用列子集替身 +
monkeypatch ``collect.RawArticle`` / ``_parse_feed`` / ``get_session``。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import StaticPool

from content_engine.stages import collect

TestBase = declarative_base()


class _RawArticle(TestBase):
    """SQLite 兼容的 raw_articles 列子集替身（保留 url 唯一约束）。"""

    __tablename__ = "raw_articles"
    __table_args__ = (UniqueConstraint("url", name="uq_raw_articles_url"),)

    # 注意：SQLite 仅 INTEGER PRIMARY KEY 支持自增，不能用 BigInteger
    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False)
    url = Column(String(1024), nullable=False)
    title = Column(String(512), nullable=False)
    content = Column(Text, nullable=False, default="")
    raw_hash = Column(String(64), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(16), nullable=False, default="raw")


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestBase.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)


def _fake_get_session(session_factory):
    @contextmanager
    def fake():
        s = session_factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return fake


def _entry(title: str, url: str) -> dict:
    return {"title": title, "summary": f"{title} 的正文内容", "link": url}


def test_new_articles_survive_later_duplicate(monkeypatch, session_factory):
    """新文章在前、重复条目在后：新文章必须全部保留（回归核心场景）。"""
    with session_factory() as s:
        s.add(
            _RawArticle(
                source_id=1, url="http://x/dup", title="已存在", content="旧文",
                raw_hash="h0",
            )
        )
        s.commit()

    # feed 顺序：新1 → 重复 → 新2（旧实现会在「重复」处把新1 一起回滚掉）
    feed = SimpleNamespace(
        entries=[
            _entry("新文章一", "http://x/new1"),
            _entry("已存在", "http://x/dup"),
            _entry("新文章二", "http://x/new2"),
        ]
    )
    monkeypatch.setattr(collect, "_parse_feed", lambda url: feed)
    monkeypatch.setattr(collect, "RawArticle", _RawArticle)
    monkeypatch.setattr(collect, "get_session", _fake_get_session(session_factory))

    source = SimpleNamespace(id=1, url="http://feed", name="测试源")
    inserted, skipped, total = collect._collect_one(source)

    assert (inserted, skipped, total) == (2, 1, 3)
    with session_factory() as s:
        urls = set(s.execute(select(_RawArticle.url)).scalars().all())
    assert urls == {"http://x/dup", "http://x/new1", "http://x/new2"}


def test_all_new_feed_inserts_all(monkeypatch, session_factory):
    """全新 feed：全部入库。"""
    feed = SimpleNamespace(entries=[_entry(f"新闻{i}", f"http://y/{i}") for i in range(3)])
    monkeypatch.setattr(collect, "_parse_feed", lambda url: feed)
    monkeypatch.setattr(collect, "RawArticle", _RawArticle)
    monkeypatch.setattr(collect, "get_session", _fake_get_session(session_factory))

    source = SimpleNamespace(id=1, url="http://feed", name="测试源")
    inserted, skipped, total = collect._collect_one(source)
    assert (inserted, skipped, total) == (3, 0, 3)

    with session_factory() as s:
        n = s.execute(select(func.count()).select_from(_RawArticle)).scalar_one()
    assert n == 3
