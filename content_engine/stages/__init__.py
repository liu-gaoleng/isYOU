"""管线阶段模块：collect / clean / classify / cluster / summarize / score。

每个阶段独立可调用、可单测、可断点重入；
输入输出均通过 DB（见 content_engine.models）流转，不再依赖内存 list。

外部使用：
    from content_engine.stages import collect, clean, classify, cluster, summarize, score
    collect.run(); clean.run(); ...
或一次性串跑：
    python -m content_engine.stages.run_all
"""

from . import classify, clean, cluster, collect, score, summarize  # noqa: F401

__all__ = ["collect", "clean", "classify", "cluster", "summarize", "score"]
