# Spec: 阶段 2 — 分类 + 聚类生产化

> 配套：[内容引擎实施计划.md](../../../内容引擎实施计划.md) §3｜[内容管线方案.md](../../../内容管线方案.md) §4–§5｜[iOS-App技术选型.md](../../../iOS-App技术选型.md)
>
> 版本 v1.0 ｜ 状态：开发中 ｜ 创建日期：2026-06-10

---

## 1. 背景

阶段 1 已完成采集 + SimHash 精确去重 + Embedding（bge-small-zh-v1.5 / 512 维）+ pgvector 语义去重 + LLM 摘要双输出 + FastAPI `/api/v1` 三接口 + 41 信源（启用 28）。

但分类与聚类仍是 demo 算法：
- 分类：纯关键词打分（[classify.py](../../../content_engine/stages/classify.py)），命中 0 即回退到信源默认 module，置信度只是简单比例；
- 聚类：72h 时间窗内对 status=classified 的文章做 Jaccard ≥ 0.86 并簇（[cluster.py](../../../content_engine/stages/cluster.py)），未利用语义向量；
- 无人工待审队列，低置信内容直接发布，违背"可降级"铁律；
- 无离线 HDBSCAN 复核机制，串卡/拆卡漂移无法纠正。

阶段 2 的目标是把分类准确率提升到 ≥ 90%，串卡率压到 < 3%，并把"低置信进人工"机制接上。

---

## 2. 出口标准（4 项）

| # | 子任务 | 出口标准 |
|---|---|---|
| 2.1 | LLM 分类 + 规则兜底 | 规则置信度 < 0.6 时调 LLM 重判，输出 `{module, tags[], confidence}` |
| 2.2 | 低置信进人工待审 | LLM 后仍 < 0.6 不自动发布，置 `EventStatus.reviewing` |
| 2.3 | Embedding 增量聚类 | Jaccard 替换为质心检索 cos ≥ 0.86 ｜ 同事件成单卡 |
| 2.4 | HDBSCAN 离线复核 | 每日批跑：纠正串/拆 ｜ 串卡率 < 3% |

---

## 3. 设计决策

### 3.1 LLM 分类 Prompt 协议（2.1）

复用既有 OpenAI 兼容协议（`RD_LLM_*`），独立 Prompt：

- **输入**：title + content（截断至 800 字）+ 当前规则候选 module；
- **输出严格 JSON**：`{"module":"tech|finance|ai|macro","tags":[...],"confidence":0.0-1.0}`；
- **失败兜底**：网络/格式异常 → 沿用规则结果，confidence 不变。

阈值：`RD_CLS_LLM_THRESHOLD=0.6`（环境变量可调）。

### 3.2 待审队列（2.2）

- 复用 `EventStatus.reviewing` 状态（已存在），不新增表；
- cluster 阶段：成员 `cls_confidence < 阈值` 占比 > 50% 的事件，初始状态置 `reviewing`；
- summarize 阶段：跳过 reviewing 事件（人工 approve 后再生成摘要）；
- `/api/v1/feed` 与 `/daily-brief` 仅返回 `EventStatus.published / scored / summarized`，**自动过滤 reviewing/rejected**（已是默认行为）。

### 3.3 Embedding 增量聚类（2.3）

- 阶段 0 的 Jaccard 替换为：
  1. 取出 status=classified 的文章，按 fetched_at 顺序流式处理；
  2. 时间窗 72h 内、同 module 的现有事件，按 `centroid` 向量做 cos 检索；
  3. cos ≥ `RD_CLUSTER_THRESHOLD`（0.86）→ 并入并更新质心（增量平均）；否则新建事件，质心 = 文章 embedding；
- 文章无 embedding → fallback Jaccard（保留旧路径，避免阻塞）。

### 3.4 HDBSCAN 离线复核（2.4）

- 新增模块 [stages/recluster.py](../../../content_engine/stages/recluster.py)；
- 每日 cron 调用：取最近 7 天 `EventStatus.summarized/scored/published` 的事件成员向量；
- 用 `sklearn.cluster.HDBSCAN`（min_cluster_size=2, metric='cosine'）批量聚类；
- 与现有事件归属比对：
  - **拆**：同事件被分到 ≥2 个 HDBSCAN 簇且簇内距离 > 0.3 → 标 `events.needs_split=true`，等人工处理；
  - **并**：跨事件被分到同一 HDBSCAN 簇 → 标 `events.suggested_merge_id`；
- **不自动改 DB**，仅打标 + 输出报告，人工在 CMS 决定（铁律：可回溯）。

> 阶段 2.4 仅交付**报告输出**，不破坏现有事件归属。schema 增加 2 个可空字段（`needs_split` / `suggested_merge_id`）。

---

## 4. 改动清单

### 4.1 数据库

- 迁移 `0007_event_review_fields.py`：
  - `events.needs_split` `Boolean nullable=True default null`
  - `events.suggested_merge_id` `BigInt nullable=True`

### 4.2 代码

| 文件 | 改动 |
|---|---|
| [stages/classify.py](../../../content_engine/stages/classify.py) | 新增 `_classify_llm()` + `confidence < threshold` 自动调 LLM；失败回退规则；环境变量 `RD_CLS_LLM_THRESHOLD` |
| [stages/cluster.py](../../../content_engine/stages/cluster.py) | Jaccard → 质心 cos；缺 embedding 兜底走旧 Jaccard；并簇时增量更新质心；初始置 reviewing 规则 |
| [stages/recluster.py](../../../content_engine/stages/recluster.py)（新增） | HDBSCAN 离线复核 + 报告输出 |
| [models/schema.py](../../../content_engine/models/schema.py) | `Event.needs_split` `Event.suggested_merge_id` |
| [tests/test_classify_llm.py](../../../content_engine/tests/test_classify_llm.py)（新增） | 5 用例：规则高置信不调 LLM / 低置信调 LLM / LLM 异常回退 / 解析非法 JSON 回退 / 阈值边界 |
| [tests/test_cluster_embedding.py](../../../content_engine/tests/test_cluster_embedding.py)（新增） | 5 用例：cos≥阈值并簇 / cos<阈值新建 / 缺 embedding 走 Jaccard 兜底 / 跨 module 不合并 / 时间窗外不合并 |
| [tests/test_recluster.py](../../../content_engine/tests/test_recluster.py)（新增） | 3 用例：建议拆/建议并/无变化 |

### 4.3 配置

`.env.example` 新增：

```
# 阶段 2 分类
RD_CLS_LLM_THRESHOLD=0.6
```

---

## 5. 测试与验收

- pytest：原 41 用例 + 新增 13 = **54 用例全绿**；
- 全链路：`run_all` 跑通后，新事件中 reviewing 比例 < 30%（避免误进人工）；
- 抽样：手工标注 50 条文章，分类准确率 ≥ 90%；
- HDBSCAN 报告：≥ 1 条 `suggested_merge_id` 命中（验证算法连通）。

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 调用成本飙升 | 仅在置信度 < 阈值时调；同文章不重复调（status 推进即标记） |
| LLM 返回非合法 JSON | strict json mode + 异常回退规则结果 |
| HDBSCAN 内存占用 | 仅取 7 天窗口；min_cluster_size=2；超 5000 事件采样 |
| 增量质心漂移 | 阶段 2.4 离线复核纠偏；阶段 4.2 人工合并 |

---

*Spec 结束 · stage-2-classify-cluster v1.0*
