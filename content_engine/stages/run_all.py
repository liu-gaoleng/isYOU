"""管线编排：按 status 顺序串起 6 个阶段。

阶段 4.3 会替换为 Celery chain；当前先提供同步顺序入口，便于本地端到端验证：

    python -m content_engine.stages.run_all
"""

from __future__ import annotations

import time

from . import classify, clean, cluster, collect, publish, score, summarize


def run_all() -> dict:
    t0 = time.time()
    print("=" * 60)
    print("「热读」内容引擎 —— 顺序编排执行（阶段 0 同步版）")
    print("=" * 60)

    stages_stats: dict[str, dict] = {}
    stages_stats["collect"] = collect.run()
    stages_stats["clean"] = clean.run()
    stages_stats["classify"] = classify.run()
    stages_stats["cluster"] = cluster.run()
    stages_stats["summarize"] = summarize.run()
    stages_stats["score"] = score.run()
    stages_stats["publish"] = publish.run()

    print("=" * 60)
    print(f"完成，耗时 {time.time() - t0:.1f}s")
    print("=" * 60)
    return stages_stats


if __name__ == "__main__":
    run_all()
