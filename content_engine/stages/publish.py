"""阶段 7：发布护栏（publish）。

阶段 4.1（本次）：在 score 之后、对外可见之前，加一道**发布前机器卡点**。

流程：
1. 取 status=scored 的事件；
2. 取该事件最新 EventContent + 全部信源原文，跑 ``services.guard.check_event``；
3. 通过 → 自动补全金融免责（如需）→ status=published；
4. 不通过 → status=reviewing，把 violations 写进最新 EventContent.llm_meta.guard，
   供 CMS 人工质检定位问题（铁律：脏内容零直发、可回溯）。

状态推进：scored → published / reviewing。

注：reviewing 在阶段 2.2 已用于「低置信待审」，这里复用同一队列；
人工 approve 后由质检 API 置回 scored 重新过护栏，或直接 published。
"""

from __future__ import annotations

from sqlalchemy import select

from content_engine.models import (
    Event,
    EventContent,
    EventStatus,
    get_session,
)
from content_engine.services import guard


def _latest_content(ev: Event) -> EventContent | None:
    return max(ev.contents, key=lambda c: c.version) if ev.contents else None


def _source_texts(ev: Event) -> list[str]:
    """事件全部信源原文（标题 + 正文），供数字一致性校验。"""
    texts: list[str] = []
    for link in ev.article_links:
        art = link.article
        if art is None:
            continue
        texts.append(f"{art.title or ''} {art.content or ''}")
    return texts


def run() -> dict:
    stats = {"checked": 0, "published": 0, "blocked": 0, "patched_disclaimer": 0}
    with get_session() as s:
        events = (
            s.execute(select(Event).where(Event.status == EventStatus.scored))
            .scalars()
            .all()
        )
        for ev in events:
            stats["checked"] += 1
            content = _latest_content(ev)
            result = guard.check_event(
                module=ev.module.value,
                card_summary=ev.card_summary,
                detail_summary=ev.detail_summary,
                why_matters=(content.why_matters if content else None),
                source_texts=_source_texts(ev),
            )

            # 自动修正：补全金融免责声明（不算拦截）
            if result.patched_why_matters is not None and content is not None:
                content.why_matters = result.patched_why_matters
                stats["patched_disclaimer"] += 1

            if result.passed:
                ev.status = EventStatus.published
                stats["published"] += 1
            else:
                ev.status = EventStatus.reviewing
                stats["blocked"] += 1
                # 把拦截原因留痕到 EventContent.llm_meta.guard，供 CMS 定位
                if content is not None:
                    meta = dict(content.llm_meta or {})
                    meta["guard"] = {"passed": False, "violations": result.violations}
                    content.llm_meta = meta

    print(
        f"  [publish] 检查 {stats['checked']} 个事件  发布 {stats['published']}  "
        f"打回 {stats['blocked']}  补免责 {stats['patched_disclaimer']}"
    )
    return stats


if __name__ == "__main__":
    print("=" * 60)
    print("[阶段 7/7] publish 发布护栏")
    print("=" * 60)
    print(run())
