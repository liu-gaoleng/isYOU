"""「热读」内容管线最小可运行 Demo
================================================

真实跑通：采集(RSS) → 清洗 → 分类 → 去重/事件聚类 → 摘要 → 评分 → 结构化输出

对应文档：内容管线方案.md（§2~§7）

运行：
    pip3 install -r requirements.txt
    python3 run_pipeline.py

可选（启用 LLM 摘要，不设则自动走抽取式兜底）：
    export RD_LLM_API_KEY="sk-..."
    export RD_LLM_BASE_URL="https://api.openai.com/v1"   # 兼容 OpenAI 格式即可
    export RD_LLM_MODEL="gpt-4o-mini"

输出：
    控制台打印各阶段统计与最终事件卡片
    output.json 落盘完整结构化结果
"""

import os
import re
import json
import html
import math
import time
from datetime import datetime, timezone

import config

try:
    import feedparser
except ImportError:
    raise SystemExit("缺少依赖 feedparser，请先运行: pip3 install -r requirements.txt")

import ssl
import urllib.request

# SSL 证书：优先用 certifi 根证书；缺失则降级为不验证（仅 Demo 本地用途）
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL_CTX = ssl._create_unverified_context()

# 部分源会拒绝默认 UA，统一伪装成浏览器
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


# ----------------------------------------------------------------------------
# 阶段一：采集（对应方案 §2）
# ----------------------------------------------------------------------------
def _parse_feed(url):
    """用带根证书的 urllib 拉取原始内容，再交给 feedparser 解析。"""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as resp:
        raw = resp.read()
    return feedparser.parse(raw)


def fetch_all():
    """从所有信源抓取条目，单源失败自动跳过。"""
    articles = []
    for src in config.SOURCES:
        try:
            print(f"  [采集] {src['name']} ({src['level']}) ...", end=" ", flush=True)
            feed = _parse_feed(src["url"])
            n = 0
            for entry in feed.entries[: config.MAX_PER_SOURCE]:
                title = clean_text(entry.get("title", ""))
                summary = clean_text(entry.get("summary", entry.get("description", "")))
                if not title:
                    continue
                articles.append({
                    "source": src["name"],
                    "level": src["level"],
                    "src_module": src["module"],
                    "title": title,
                    "content": summary,
                    "url": entry.get("link", ""),
                    "published": entry.get("published", entry.get("updated", "")),
                })
                n += 1
            print(f"{n} 条")
        except Exception as e:
            print(f"跳过（{type(e).__name__}）")
    return articles


# ----------------------------------------------------------------------------
# 阶段二：清洗（对应方案 §3.1）
# ----------------------------------------------------------------------------
def clean_text(text):
    """去 HTML 标签、转义符、压缩空白。"""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)   # 去标签
    text = html.unescape(text)             # 解转义
    text = re.sub(r"\s+", " ", text)       # 压空白
    return text.strip()


# ----------------------------------------------------------------------------
# 阶段三：分类（规则兜底，对应方案 §4.3）
# ----------------------------------------------------------------------------
def classify(article):
    """关键词打分选最高分模块；全 0 时回退到信源默认模块。"""
    text = article["title"] + " " + article["content"]
    scores = {}
    for module, kws in config.CLASSIFY_RULES.items():
        scores[module] = sum(1 for kw in kws if kw.lower() in text.lower())
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return article["src_module"], 0.5   # 低置信，规则未命中→用信源默认
    total = sum(scores.values())
    confidence = round(scores[best] / total, 2) if total else 0.5
    return best, confidence


# ----------------------------------------------------------------------------
# 阶段四：去重 / 事件聚类（轻量 Jaccard 近似，对应方案 §3.2 / §5.2）
# ----------------------------------------------------------------------------
def tokenize(text):
    """中英文混合分词：英文按词，中文按 2-gram。"""
    text = text.lower()
    en = re.findall(r"[a-z0-9]+", text)
    cn = re.findall(r"[\u4e00-\u9fff]", text)
    bigrams = ["".join(cn[i:i + 2]) for i in range(len(cn) - 1)]
    return set(en) | set(bigrams)


def similarity(a, b):
    """Jaccard 相似度，近似方案中的向量余弦。"""
    ta = tokenize(a["title"] + " " + a["content"])
    tb = tokenize(b["title"] + " " + b["content"])
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def dedup_exact(articles):
    """精确去重（方案 §3.2）：相同 URL / 相同标题视为同一篇，仅保留来源等级最高者。"""
    seen = {}
    for art in articles:
        key = art["url"] or art["title"]
        prev = seen.get(key)
        if prev is None or config.LEVEL_WEIGHT.get(art["level"], 0) > config.LEVEL_WEIGHT.get(prev["level"], 0):
            seen[key] = art
    return list(seen.values())


def cluster(articles):
    """增量聚类：同一事件的多源报道聚成一个事件簇（含多源合并）。"""
    events = []
    for art in articles:
        placed = False
        for ev in events:
            if ev["module"] != art["module"]:
                continue
            sim = max(similarity(art, m) for m in ev["members"])
            if sim >= config.CLUSTER_THRESHOLD:
                ev["members"].append(art)
                placed = True
                break
        if not placed:
            events.append({"module": art["module"], "members": [art]})
    return events


# ----------------------------------------------------------------------------
# 阶段五：摘要（LLM 可选 + 抽取式兜底，对应方案 §6）
# ----------------------------------------------------------------------------
def summarize(event):
    """优先调用 LLM；无 API Key 时走抽取式兜底，保证任何环境可跑通。"""
    members = event["members"]
    main = max(members, key=lambda m: config.LEVEL_WEIGHT.get(m["level"], 0.3))
    api_key = os.getenv("RD_LLM_API_KEY")
    if api_key:
        try:
            return summarize_llm(event, main, api_key)
        except Exception as e:
            print(f"    [摘要] LLM 调用失败，回退抽取式：{type(e).__name__}")
    return summarize_extractive(event, main)


def summarize_extractive(event, main):
    """抽取式兜底：取正文前若干句作摘要，不杜撰。"""
    text = main["content"] or main["title"]
    # 先保护小数（如 2.21%、3.13），避免在小数点处错误断句
    text = re.sub(r"(\d)\.(\d)", r"\1__DOT__\2", text)
    sentences = re.split(r"(?<=[。！？!?])\s*|(?<=[a-z])\.\s+", text)
    sentences = [s.replace("__DOT__", ".").strip() for s in sentences if s.strip()]
    summary = sentences[:3] if sentences else [main["title"]]
    return {
        "title": main["title"],
        "summary": summary,
        "why_matters": "（抽取式兜底未生成解读；接入 LLM 后输出「为何重要」）",
        "method": "extractive",
    }


def summarize_llm(event, main, api_key):
    """调用兼容 OpenAI 格式的 LLM，使用防幻觉 Prompt（方案 §6.2）。"""
    base_url = os.getenv("RD_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = os.getenv("RD_LLM_MODEL", "gpt-4o-mini")
    sources_text = "\n".join(
        f"[{i+1}]（{m['level']}）{m['source']}：{m['title']}。{m['content']}"
        for i, m in enumerate(event["members"])
    )
    prompt = (
        "你是「热读」的资深财经科技编辑。基于【仅以下多源原文】生成结构化摘要，"
        "服务投资人/创业者/产品经理。\n"
        "严格规则：1.只用原文出现的事实与数字，禁止编造；2.数字必须与原文一致，"
        "原文没有则留空；3.区分事实与解读：summary 只写事实，why_matters 是解读但不得引入新事实；"
        "4.标题可设问增强吸引力但不得夸大。\n"
        "输出严格 JSON：{\"title\":\"\",\"summary\":[\"\",\"\",\"\"],\"why_matters\":\"\"}\n\n"
        f"【多源原文】\n{sources_text}"
    )
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    parsed["method"] = "llm"
    return parsed


# ----------------------------------------------------------------------------
# 阶段六：重要性评分（对应方案 §7.1）
# ----------------------------------------------------------------------------
def score(event):
    """importance = 0.4·来源权重 + 0.3·多源交叉 + 0.2·热度 + 0.1·时效。
    多源交叉按【不同信源】计数，避免同源多条虚增。"""
    members = event["members"]
    level_w = max(config.LEVEL_WEIGHT.get(m["level"], 0.3) for m in members)
    distinct_sources = len({m["source"] for m in members})
    cross = min(distinct_sources / 5, 1.0)
    hotness = 0.5            # Demo 无社交信号，给中性值
    recency = 1.0           # Demo 视为当日，给满
    importance = 0.4 * level_w + 0.3 * cross + 0.2 * hotness + 0.1 * recency
    return round(importance * 100, 1)


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main():
    t0 = time.time()
    print("=" * 60)
    print("「热读」内容管线 Demo —— 开始运行")
    print("=" * 60)

    # 1 采集
    print("\n[1/6] 采集 RSS ...")
    articles = fetch_all()
    print(f"  → 采集到 {len(articles)} 条原始条目")
    if not articles:
        print("  未采集到任何内容（可能是网络/源不可用）。退出。")
        return

    # 2 清洗（采集时已清洗）+ 3 分类
    print("\n[2/6] 分类（规则兜底）...")
    for art in articles:
        art["module"], art["cls_conf"] = classify(art)
    dist = {}
    for art in articles:
        dist[art["module"]] = dist.get(art["module"], 0) + 1
    print("  → 模块分布：" + "  ".join(f"{k}:{v}" for k, v in dist.items()))

    # 4 去重 / 聚类
    print("\n[3/6] 去重 + 事件聚类 ...")
    before = len(articles)
    articles = dedup_exact(articles)
    print(f"  → 精确去重：{before} → {len(articles)} 条（移除 {before - len(articles)} 条完全重复）")
    events = cluster(articles)
    multi = sum(1 for e in events if len({m["source"] for m in e["members"]}) > 1)
    print(f"  → {len(articles)} 条聚为 {len(events)} 个事件（其中 {multi} 个为多源交叉）")

    # 5 摘要 + 6 评分
    mode = "LLM" if os.getenv("RD_LLM_API_KEY") else "抽取式兜底"
    print(f"\n[4/6] 生成摘要（{mode}）+ [5/6] 重要性评分 ...")
    results = []
    for ev in events:
        summary = summarize(ev)
        importance = score(ev)
        distinct = len({m["source"] for m in ev["members"]})
        results.append({
            "module": ev["module"],
            "importance": importance,
            "source_count": distinct,
            "sources": [{"name": m["source"], "level": m["level"], "url": m["url"]}
                        for m in ev["members"]],
            **summary,
        })

    # 排序 + 输出（对应方案 §7.2 榜单）
    print("\n[6/6] 排序并输出 ...")
    results.sort(key=lambda x: x["importance"], reverse=True)

    out_path = os.path.join(os.path.dirname(__file__), "output.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 控制台展示 TOP 榜单
    print("\n" + "=" * 60)
    print("今日热榜 TOP 10（按 importance 排序）")
    print("=" * 60)
    for i, r in enumerate(results[:10], 1):
        print(f"\n#{i}  [{r['module']}]  热度 {r['importance']}  ·  {r['source_count']} 源")
        print(f"    {r['title']}")
        for s in r["summary"][:3]:
            print(f"      · {s}")
        srcs = "、".join(f"{s['name']}({s['level']})" for s in r["sources"][:4])
        print(f"      来源：{srcs}")

    print("\n" + "=" * 60)
    print(f"完成：{len(events)} 个事件，耗时 {time.time() - t0:.1f}s")
    print(f"完整结构化结果已写入：{out_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()
