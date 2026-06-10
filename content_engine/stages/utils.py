"""阶段间共享的纯函数工具：清洗、分词、相似度、哈希、SimHash。

从 pipeline_demo/run_pipeline.py 抽离，无副作用、可单测。
"""

from __future__ import annotations

import hashlib
import html
import re

# SimHash 库延迟到使用时再 import，避免单测此模块时强依赖（库本身轻量但保险起见）
try:
    from simhash import Simhash as _Simhash  # type: ignore
except ImportError:  # pragma: no cover
    _Simhash = None  # 未安装时 simhash64 会显式报错

# ----------------------------------------------------------------------------
# 1) 清洗（对应方案 §3.1）
# ----------------------------------------------------------------------------
def clean_text(text: str | None) -> str:
    """去 HTML 标签、转义符、压缩空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ----------------------------------------------------------------------------
# 2) 分词（中英文混合：英文按词，中文按 2-gram）
# ----------------------------------------------------------------------------
def tokenize(text: str) -> set[str]:
    text = (text or "").lower()
    en = re.findall(r"[a-z0-9]+", text)
    cn = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cn[i : i + 2]) for i in range(len(cn) - 1)]
    return set(en) | set(bigrams)


def jaccard(a: str, b: str) -> float:
    """Jaccard 相似度，阶段 0 用作 Embedding 上线前的近似。"""
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ----------------------------------------------------------------------------
# 3) 指纹
# ----------------------------------------------------------------------------
def content_hash(title: str, content: str) -> str:
    """基于标题+正文生成稳定的 64 位十六进制哈希（精确字节级一致性）。"""
    payload = f"{(title or '').strip()}\n{(content or '').strip()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------------------
# 3b) SimHash —— 近似去重（阶段 1.1）
# ----------------------------------------------------------------------------
def _simhash_features(title: str, content: str) -> list[str]:
    """从标题+正文抽取 SimHash 特征 token。

    中英混合：英文按词，中文按 2-gram；与 tokenize() 同源以便单测复用。
    标题与正文合并后取 token 列表（保留重复以参与权重）。
    """
    text = f"{title or ''} {content or ''}".lower()
    en = re.findall(r"[a-z0-9]+", text)
    cn = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cn[i : i + 2]) for i in range(len(cn) - 1)]
    return en + bigrams


def simhash64(title: str, content: str) -> str | None:
    """生成 64-bit SimHash，返回 16 位十六进制字符串；空输入返回 None。

    存储用 hex 字符串而非 BigInteger：避免 Python int(unsigned 64) 与 PG bigint(signed)
    的符号转换坑，对比汉明距离时再 int(x, 16)。
    """
    if _Simhash is None:
        raise RuntimeError("缺少依赖 simhash，请先 pip install -e '.[dev]'")
    feats = _simhash_features(title, content)
    if not feats:
        return None
    value = _Simhash(feats).value  # 0 ~ 2^64-1
    return f"{value:016x}"


def hamming_distance(a: str | int, b: str | int) -> int:
    """两个 SimHash 之间的汉明距离（接受 hex 字符串或 int）。"""
    ai = int(a, 16) if isinstance(a, str) else a
    bi = int(b, 16) if isinstance(b, str) else b
    return (ai ^ bi).bit_count()


# ----------------------------------------------------------------------------
# 4) 抽取式摘要兜底（保护小数避免错误断句）
# ----------------------------------------------------------------------------
def extractive_summary(text: str, max_sentences: int = 3) -> list[str]:
    if not text:
        return []
    text = re.sub(r"(\d)\.(\d)", r"\1__DOT__\2", text)
    sentences = re.split(r"(?<=[。！？!?])\s*|(?<=[a-z])\.\s+", text)
    sentences = [s.replace("__DOT__", ".").strip() for s in sentences if s.strip()]
    return sentences[:max_sentences]


__all__ = [
    "clean_text",
    "tokenize",
    "jaccard",
    "content_hash",
    "simhash64",
    "hamming_distance",
    "extractive_summary",
]
