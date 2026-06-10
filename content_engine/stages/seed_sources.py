"""信源种子灌库脚本（同步语义）。

用法（venv 激活，DB 已建表）：
    python -m content_engine.stages.seed_sources

语义：按 name upsert
- 不存在：新增；
- 已存在：同步更新 url / level / module / enabled 四个字段（对齐 seed_data 的最新声明）。
返回 (inserted, updated, disabled) 三元组。
"""

from __future__ import annotations

from sqlalchemy import select

from content_engine.models import Source, get_session

from .seed_data import SEED_SOURCES


def seed() -> tuple[int, int, int]:
    """灌入/同步种子信源，返回 (新增, 更新, 已禁用)。"""
    inserted = 0
    updated = 0
    disabled = 0
    with get_session() as s:
        rows = s.execute(select(Source)).scalars().all()
        existing: dict[str, Source] = {row.name: row for row in rows}
        for src in SEED_SOURCES:
            enabled = src.get("enabled", True)
            if not enabled:
                disabled += 1
            row = existing.get(src["name"])
            if row is None:
                s.add(
                    Source(
                        name=src["name"],
                        url=src["url"],
                        level=src["level"],
                        module=src["module"],
                        type="rss",
                        enabled=enabled,
                    )
                )
                inserted += 1
            else:
                changed = False
                if row.url != src["url"]:
                    row.url = src["url"]
                    changed = True
                if row.level != src["level"]:
                    row.level = src["level"]
                    changed = True
                if row.module != src["module"]:
                    row.module = src["module"]
                    changed = True
                if row.enabled != enabled:
                    row.enabled = enabled
                    changed = True
                if changed:
                    updated += 1
    return inserted, updated, disabled


def main() -> None:
    inserted, updated, disabled = seed()
    print(
        f"[seed_sources] 新增 {inserted} 条 / 更新 {updated} 条 / 禁用 {disabled} 条"
    )


if __name__ == "__main__":
    main()
