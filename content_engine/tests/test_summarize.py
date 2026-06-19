"""summarize 单测：并发改造后的纯函数 + 三段式 run() 路径。

不连真实 DB / 真实 LLM：
- 纯函数（_parse_llm_response / _summary_extractive_core / _build_prompt）直接断言；
- run() 通过 monkeypatch _load_jobs（返回 job 快照）、get_llm_client（假客户端）、
  _persist（记录落库）覆盖并发与串行两条路径，验证全部 job 被处理、stats 正确、
  并发不丢条；LLM 失败时回退抽取式。
"""

from __future__ import annotations

import threading

from content_engine.config import settings
from content_engine.services.llm_client import LLMError, LLMResponse
from content_engine.stages import summarize as S


def _job(event_id: int) -> S._SummaryJob:
    return S._SummaryJob(
        event_id=event_id,
        fingerprint=f"fp-{event_id}",
        prev_version=0,
        needs_disclaimer=False,
        main_title=f"标题{event_id}",
        main_content=f"正文{event_id}。第二句。第三句。",
        prompt=f"prompt-{event_id}",
        sources=[{"name": "src", "level": "A", "url": "http://x"}],
    )


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------
def test_parse_llm_response_basic():
    resp = LLMResponse(
        content='{"title":"T","card_summary":"卡片","detail_summary":"详情","why_matters":"重要"}',
        model="m",
        usage={"prompt_tokens": 1, "completion_tokens": 2},
    )
    out = S._parse_llm_response(resp, fallback_title="fb")
    assert out["title"] == "T"
    assert out["card_summary"] == "卡片"
    assert out["method"] == "llm"
    assert out["llm_meta"]["model"] == "m"
    assert out["llm_meta"]["prompt_version"] == S.SUMMARY_PROMPT_VERSION


def test_parse_llm_response_truncates_overlong_card():
    long_card = "句。" * 200  # 远超 120 字
    resp = LLMResponse(
        content=f'{{"title":"","card_summary":"{long_card}","detail_summary":"d","why_matters":""}}',
        model="m",
        usage=None,
    )
    out = S._parse_llm_response(resp, fallback_title="fb")
    assert len(out["card_summary"]) <= S.CARD_SUMMARY_MAX_CHARS
    assert out["title"] == "fb"  # title 空时回退


def test_parse_llm_response_caps_detail():
    long_detail = "字" * 1000
    resp = LLMResponse(
        content=f'{{"title":"t","card_summary":"c","detail_summary":"{long_detail}","why_matters":""}}',
        model="m",
        usage=None,
    )
    out = S._parse_llm_response(resp, fallback_title="fb")
    assert len(out["detail_summary"]) == S.DETAIL_SUMMARY_MAX_CHARS


def test_summary_extractive_core_fallback():
    out = S._summary_extractive_core("标题", "第一句。第二句。第三句。第四句。")
    assert out["method"] == "extractive"
    assert out["llm_meta"] is None
    assert out["detail_summary"]
    assert len(out["card_summary"]) <= S.CARD_SUMMARY_MAX_CHARS


def test_summary_extractive_core_empty_uses_title():
    out = S._summary_extractive_core("仅有标题", "")
    assert out["detail_summary"] == "仅有标题"


# ---------------------------------------------------------------------------
# _generate_summary：LLM 成功 / 失败回退 / 未配置
# ---------------------------------------------------------------------------
class _FakeClient:
    enabled = True

    def __init__(self, content: str | None = None, raise_exc: Exception | None = None):
        self._content = content
        self._raise = raise_exc

    def chat_json(self, prompt: str, temperature: float = 0.0) -> LLMResponse:
        if self._raise is not None:
            raise self._raise
        return LLMResponse(content=self._content, model="fake", usage=None)


def test_generate_summary_llm_success(monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "k")  # enabled=True
    content = '{"title":"t","card_summary":"c","detail_summary":"d","why_matters":"w"}'
    monkeypatch.setattr(S, "get_llm_client", lambda: _FakeClient(content=content))
    out = S._generate_summary(_job(1))
    assert out["method"] == "llm"
    assert out["card_summary"] == "c"


def test_generate_summary_falls_back_on_llm_error(monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "k")
    monkeypatch.setattr(
        S, "get_llm_client", lambda: _FakeClient(raise_exc=LLMError("boom"))
    )
    out = S._generate_summary(_job(2))
    assert out["method"] == "extractive"  # 回退


def test_generate_summary_falls_back_on_bad_json(monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "k")
    monkeypatch.setattr(
        S, "get_llm_client", lambda: _FakeClient(content="not-json")
    )
    out = S._generate_summary(_job(3))
    assert out["method"] == "extractive"


def test_generate_summary_extractive_when_disabled(monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "")  # enabled=False
    out = S._generate_summary(_job(4))
    assert out["method"] == "extractive"


# ---------------------------------------------------------------------------
# run()：并发 / 串行两条路径，全部 job 被处理、不丢条
# ---------------------------------------------------------------------------
def _patch_run(monkeypatch, jobs, *, concurrency: int, content: str):
    monkeypatch.setattr(settings.llm, "api_key", "k")
    monkeypatch.setattr(settings.llm, "summarize_concurrency", concurrency)
    monkeypatch.setattr(S, "_load_jobs", lambda: (list(jobs), 0))
    monkeypatch.setattr(S, "get_llm_client", lambda: _FakeClient(content=content))
    persisted: list[int] = []
    lock = threading.Lock()

    def _fake_persist(job, summary):
        with lock:
            persisted.append(job.event_id)

    monkeypatch.setattr(S, "_persist", _fake_persist)
    return persisted


def test_run_concurrent_processes_all_jobs(monkeypatch):
    jobs = [_job(i) for i in range(50)]
    content = '{"title":"t","card_summary":"c","detail_summary":"d","why_matters":"w"}'
    persisted = _patch_run(monkeypatch, jobs, concurrency=8, content=content)
    stats = S.run()
    assert stats["summarized"] == 50
    assert stats["llm"] == 50
    assert stats["extractive"] == 0
    assert sorted(persisted) == list(range(50))  # 不丢条、不重复


def test_run_serial_path(monkeypatch):
    jobs = [_job(i) for i in range(5)]
    content = '{"title":"t","card_summary":"c","detail_summary":"d","why_matters":"w"}'
    persisted = _patch_run(monkeypatch, jobs, concurrency=1, content=content)
    stats = S.run()
    assert stats["summarized"] == 5
    assert sorted(persisted) == list(range(5))


def test_run_empty_jobs(monkeypatch):
    monkeypatch.setattr(settings.llm, "api_key", "k")
    monkeypatch.setattr(S, "_load_jobs", lambda: ([], 3))
    stats = S.run()
    assert stats == {"summarized": 0, "llm": 0, "extractive": 0, "skipped": 3}


def test_run_concurrent_counts_fallback(monkeypatch):
    jobs = [_job(i) for i in range(10)]
    persisted = _patch_run(monkeypatch, jobs, concurrency=4, content="bad-json")
    stats = S.run()
    assert stats["summarized"] == 10
    assert stats["extractive"] == 10  # 全部回退抽取式
    assert stats["llm"] == 0
    assert sorted(persisted) == list(range(10))
