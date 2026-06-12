# Spec: 阶段 3 — 摘要服务化 + 评分 + 榜单

> 配套：[内容引擎实施计划.md](../../../内容引擎实施计划.md) §4｜[内容管线方案.md](../../../内容管线方案.md) §6–§7｜[iOS-App技术选型.md](../../../iOS-App技术选型.md)
>
> 版本 v1.0 ｜ 状态：开发中 ｜ 创建日期：2026-06-10

---

## 1. 背景

阶段 1–2 已完成采集/去重/分类/聚类生产化：分类有 LLM 兜底、聚类用 embedding 质心、低置信进 reviewing。但摘要与评分仍是"裸调 + 占位"：

- **LLM 调用裸 urllib**：classify / summarize 各自直接 `urllib.urlopen`，无限流、无重试、无退避；一旦 429 / 网络抖动直接失败回退抽取式，浪费可恢复的调用；
- **无同事件缓存**：summarize 每轮对所有 clustered 事件全量重算，未变更事件重复烧 token；
- **评分占位**：[score.py](../../../content_engine/stages/score.py) 里 `hotness` 直接取 `event.hotness`（建簇时写死 0.5），未接真实热度信号；recency 已实现但 hotness 仍是占位；
- **无榜单**：`/daily-brief` 每次实时 `ORDER BY importance` 扫表，无 Redis ZSet，频道 TOP10 无法 O(logN) 获取。

阶段 3 目标：把 LLM 调用收敛到统一带韧性的客户端、同事件不重复生成、评分接真实信号、榜单落 Redis ZSet（可降级回 DB）。

---

## 2. 出口标准（4 项）

| # | 子任务 | 出口标准 |
|---|---|---|
| 3.1 | LLM 调用服务化 | 统一 `LLMClient`：令牌桶限流 + 最多 N 次重试 + 指数退避（含 429/5xx/超时）；classify + summarize 都改走它 |
| 3.2 | 同事件缓存 + 留痕 | 事件内容指纹未变（成员集合 + 标题）则跳过重新生成；每次 LLM 调用 usage/model/version 全量写 `llm_meta` |
| 3.3 | 评分接入真实信号 | `hotness` = 时间衰减加权的多源热度（来源数 + B 级社交占比 + 更新频次）；`recency = exp(-Δt/τ)` 已有，公式沿用方案 §7.1 |
| 3.4 | Redis 榜单 | 写 ZSet：`rank:all` + `rank:{module}` 各一份，score=`importance`；读接口 O(logN) 取 TOP-N；Redis 不可用自动降级回 DB |

---

## 3. 设计决策

### 3.1 LLMClient（services/llm_client.py）

统一封装 OpenAI 兼容 `POST /chat/completions`，供 classify / summarize 复用：

- **令牌桶限流**：`RD_LLM_RATE_PER_SEC`（默认 3 QPS），进程内单例 `threading.Lock` 保护；
- **重试 + 退避**：可重试错误（HTTP 429/500/502/503/504、`URLError`、`TimeoutError`）最多 `RD_LLM_MAX_RETRIES`（默认 3）次，退避 `base * 2**attempt`（base=`RD_LLM_BACKOFF_BASE`，默认 1.0s）+ 抖动；
- **非可重试错误**（4xx 非 429、JSON 解析失败）直接抛 `LLMError`，由调用方兜底；
- **返回**：`LLMResponse(content: str, usage: dict, model: str)`；
- **未配置**（无 api_key）：`enabled=False`，调用方走抽取式/规则兜底，不实例化网络请求。

classify._classify_llm / summarize._summary_llm 改为调用 `llm_client.chat_json(prompt, temperature)`，删除各自的 urllib 样板。

### 3.2 同事件缓存 + 留痕

- **内容指纹**：`content_fingerprint(event) = sha1(sorted(member article_ids) + main_title)`；
- summarize 取最新版 EventContent，如果其 `llm_meta.fingerprint == 当前指纹` 且事件状态已 summarized+ → **跳过**（不重算，状态不回退）；
- 指纹存进 `llm_meta.fingerprint`；同时 `llm_meta` 增加 `prompt_version`（常量 `SUMMARY_PROMPT_VERSION`）便于回放与 A/B；
- 抽取式路径也写 fingerprint（method=extractive 时 llm_meta 仍记录指纹 + version，usage=None）。

### 3.3 评分接入真实信号（hotness）

替换 `score.py` 里 `hotness = event.hotness` 占位为真实计算：

```
hotness = 0.5 * cross_source        # 跨源数 min(distinct/5, 1)
        + 0.3 * social_ratio        # B 级（社交/自媒体）成员占比，代表"舆论热度"
        + 0.2 * freshness_velocity  # 24h 内成员数 / 总成员数，代表"还在持续发酵"
```

- 落库回写 `event.hotness`（供 `/feed` 卡片展示）；
- `importance` 公式不变（方案 §7.1）：`0.4*level + 0.3*cross + 0.2*hotness + 0.1*recency`；
- 纯函数 `compute_hotness(members, now)` 便于单测。

### 3.4 Redis 榜单（services/ranking.py）

- score 阶段结束后，把 `scored/published` 事件写入 Redis ZSet：
  - 全站：`rank:all`
  - 分模块：`rank:tech` `rank:finance` `rank:ai` `rank:macro`
  - member=event_id，score=importance；
  - 每次重建用 pipeline（先 `DEL` 再 `ZADD`，保持与 DB 一致）；
  - 仅保留 TOP `RD_RANK_KEEP`（默认 500），`ZREMRANGEBYRANK` 裁剪；
- 读接口 `top(module, n)`：`ZREVRANGE`，返回 event_id 列表；
- **降级**：Redis 连接失败 → 记 WARNING，写入静默跳过；读取时回退到 DB `ORDER BY importance`；
- 新增 API `GET /api/v1/ranking?module=&limit=`：优先读 Redis ZSet 取 id → DB 批量取 EventCard；Redis 不可用回退纯 DB。

---

## 4. 改动清单

### 4.1 新增文件

| 文件 | 作用 |
|---|---|
| [services/llm_client.py](../../../content_engine/services/llm_client.py) | 统一 LLM 客户端（限流 + 重试 + 退避 + 留痕用量） |
| [services/ranking.py](../../../content_engine/services/ranking.py) | Redis ZSet 榜单写入 / 读取 / 降级 |
| [tests/test_llm_client.py](../../../content_engine/tests/test_llm_client.py) | 限流 / 重试退避 / 不可重试错误 / 未配置 |
| [tests/test_score_signals.py](../../../content_engine/tests/test_score_signals.py) | compute_hotness 各信号 + 边界 |
| [tests/test_ranking.py](../../../content_engine/tests/test_ranking.py) | ZSet 写读（fakeredis / mock）+ 降级 |

### 4.2 改动文件

| 文件 | 改动 |
|---|---|
| [config/settings.py](../../../content_engine/config/settings.py) | LLMSettings 加 rate_per_sec / max_retries / backoff_base / timeout；新增 RankingSettings（keep_top / enabled） |
| [stages/classify.py](../../../content_engine/stages/classify.py) | `_classify_llm` 改走 llm_client |
| [stages/summarize.py](../../../content_engine/stages/summarize.py) | `_summary_llm` 改走 llm_client；加同事件缓存跳过 + fingerprint + prompt_version |
| [stages/score.py](../../../content_engine/stages/score.py) | `compute_hotness` 真实信号；score 结束写 Redis 榜单 |
| [api/routers/brief.py](../../../content_engine/api/routers/brief.py) | 新增 `/ranking` 接口 |
| [api/schemas.py](../../../content_engine/api/schemas.py) | 复用 EventCard（榜单返回卡片列表） |
| [.env.example](../../../.env.example) | 新增 LLM 韧性 + 榜单变量 |

### 4.3 配置项

```
# 阶段 3.1 LLM 调用韧性
RD_LLM_RATE_PER_SEC=3
RD_LLM_MAX_RETRIES=3
RD_LLM_BACKOFF_BASE=1.0
RD_LLM_TIMEOUT=60
# 阶段 3.4 Redis 榜单
RD_RANK_ENABLED=true
RD_RANK_KEEP=500
```

---

## 5. 测试与验收

- pytest：阶段 2 的 58 用例 + 新增约 13 = **71 用例全绿**；
- ruff All checks passed；
- 全链路：`run_all` 跑通；二次跑 summarize 时同事件缓存命中（跳过数 > 0）；
- score 后 `hotness` 不再恒为 0.5（落入 0–1 区间且有方差）；
- Redis 榜单：`ZCARD rank:all > 0`，`/api/v1/ranking` 返回 TOP-N；停掉 Redis 时 `/ranking` 仍能回退 DB 返回。

---

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 限流锁拖慢批处理 | 令牌桶非阻塞 + 仅 LLM 路径生效；抽取式/规则不受影响 |
| Redis 不可用导致接口 500 | 写入/读取双向降级，连接失败静默回退 DB |
| 同事件缓存误跳过（漏更新） | 指纹纳入成员集合 + 标题；成员变化必然触发重算 |
| hotness 信号缺数据（早期单源） | 各信号都做 min/clip，单源时 hotness 仍有界（≈0.1–0.3） |
| 退避导致整体耗时拉长 | max_retries 默认 3 + 指数退避上限；可重试错误才退避 |

---

*Spec 结束 · stage-3-summary-ranking v1.0*
