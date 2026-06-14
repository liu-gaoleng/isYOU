"""A 阶段评测单测：分类准确率读回打分（score_classify）。

只测纯函数逻辑（CSV → 指标），不依赖 DB / pgvector：
sample_classify / eval_cluster 依赖真实 DB 的 Vector 列，放到联调脚本里手动验证。
"""

from __future__ import annotations

import csv

from content_engine.stages.evaluate import _CSV_FIELDS, score_classify


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for r in rows:
            base = {k: "" for k in _CSV_FIELDS}
            base.update(r)
            w.writerow(base)


def test_score_classify_basic_accuracy(tmp_path):
    p = tmp_path / "s.csv"
    _write_csv(p, [
        {"article_id": 1, "predicted_module": "tech", "true_module": "tech"},
        {"article_id": 2, "predicted_module": "ai", "true_module": "ai"},
        {"article_id": 3, "predicted_module": "tech", "true_module": "finance"},  # 错
        {"article_id": 4, "predicted_module": "finance", "true_module": "finance"},
    ])
    r = score_classify(str(p))
    assert r["labeled"] == 4
    assert r["correct"] == 3
    assert r["accuracy"] == 0.75


def test_score_classify_skips_unlabeled(tmp_path):
    p = tmp_path / "s.csv"
    _write_csv(p, [
        {"article_id": 1, "predicted_module": "tech", "true_module": "tech"},
        {"article_id": 2, "predicted_module": "ai", "true_module": ""},  # 未标注，跳过
        {"article_id": 3, "predicted_module": "ai", "true_module": "bogus"},  # 非法，跳过
    ])
    r = score_classify(str(p))
    assert r["labeled"] == 1
    assert r["accuracy"] == 1.0


def test_score_classify_empty(tmp_path):
    p = tmp_path / "s.csv"
    _write_csv(p, [
        {"article_id": 1, "predicted_module": "tech", "true_module": ""},
    ])
    r = score_classify(str(p))
    assert r["labeled"] == 0
    assert r["accuracy"] is None


def test_score_classify_blank_correct_mode(tmp_path):
    # 只标错误口径：空白=预测正确，仅误分类行填正确模块
    p = tmp_path / "s.csv"
    _write_csv(p, [
        {"article_id": 1, "predicted_module": "tech", "true_module": ""},      # 正确
        {"article_id": 2, "predicted_module": "ai", "true_module": ""},         # 正确
        {"article_id": 3, "predicted_module": "tech", "true_module": "ai"},     # 错
        {"article_id": 4, "predicted_module": "finance", "true_module": ""},    # 正确
    ])
    r = score_classify(str(p), blank_correct=True)
    assert r["labeled"] == 4
    assert r["correct"] == 3
    assert r["accuracy"] == 0.75
    # 混淆矩阵：真值 ai 行 = {ai:1（row2 正确）, tech:1（row3 被误判成 tech）}
    assert r["confusion"]["ai"] == {"ai": 1, "tech": 1}


def test_score_classify_per_module_precision_recall(tmp_path):
    p = tmp_path / "s.csv"
    # tech: 2 真值，1 命中 1 漏判成 ai => recall 0.5；预测 tech 1 次全对 => precision 1.0
    _write_csv(p, [
        {"article_id": 1, "predicted_module": "tech", "true_module": "tech"},
        {"article_id": 2, "predicted_module": "ai", "true_module": "tech"},
        {"article_id": 3, "predicted_module": "ai", "true_module": "ai"},
    ])
    r = score_classify(str(p))
    tech = r["per_module"]["tech"]
    assert tech["support"] == 2
    assert tech["recall"] == 0.5
    assert tech["precision"] == 1.0
    # 混淆矩阵：真值 tech 被预测为 {tech:1, ai:1}
    assert r["confusion"]["tech"] == {"tech": 1, "ai": 1}
