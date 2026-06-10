# 重构 LLM 摘要 Prompt 直接产出 card_summary 与 detail_summary

## Why
当前 [summarize.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/summarize.py) 的 LLM Prompt 仍按「3 句 summary[] + why_matters」结构产出，再由 Python 拼接成 `detail_summary`、再截断生成 `card_summary`，导致 iOS 卡片摘要质量受限于自动截断。直接让 LLM 按 iOS 卡片流 / 详情页的目标长度产出双摘要，可显著提升卡片首屏可读性。

## What Changes
- **MODIFIED LLM Prompt**：JSON schema 从 `{title, summary[], why_matters}` 改为 `{title, card_summary, detail_summary, why_matters}`，明确两个摘要的字符长度与写作要求
- **MODIFIED summarize.py**：`_summary_llm()` 直接拿 `card_summary` / `detail_summary`，不再走 Python 端的拼接 + 截断
- **MODIFIED 抽取式兜底**：`_summary_extractive()` 同时产出 `card_summary`（≤120 字截断）+ `detail_summary`（抽取式 3 句拼接）
- **MODIFIED run() 主流程**：直接把 LLM/抽取式产出的双摘要写入 `events.card_summary` / `events.detail_summary`
- **保留兼容**：`event_contents.summary[]` 列仍写入（兜底时拆 detail 为列表，LLM 路径下用 `[card, detail]`）；`why_matters` 不变；不动 schema、不动迁移
- **不破坏既有管线**：collect/clean/classify/cluster/score 不动；测试维持 27/27 通过

## Impact
- Affected specs: 阶段 1.4 摘要分级（[内容引擎实施计划.md](file:///Users/bytedance/liu/isYOU/内容引擎实施计划.md)）
- Affected code:
  - [summarize.py](file:///Users/bytedance/liu/isYOU/content_engine/stages/summarize.py) — Prompt + `_summary_llm` + `_summary_extractive` + `run()`

## ADDED Requirements

### Requirement: LLM 直接产出 iOS 双摘要
The system SHALL 让 LLM 在一次调用中同时产出符合 iOS 卡片流（≤120 中文字符）与详情页（300–500 中文字符）长度规范的两条摘要。

#### Scenario: LLM 调用成功
- **WHEN** `summarize.run()` 处理一个 `clustered` 事件，且 `settings.llm.enabled=True`
- **THEN** LLM 返回 JSON `{title, card_summary, detail_summary, why_matters}`，`card_summary` 长度 ≤120 字符、`detail_summary` 长度 ≥150 且 ≤600 字符
- **AND** `events.card_summary` / `events.detail_summary` 直接写入 LLM 产出，不再走 Python 截断

#### Scenario: LLM 失败回退抽取式
- **WHEN** LLM 调用抛异常或解析失败
- **THEN** 抽取式兜底同时产出 `detail_summary`（取 main 文章前 3-4 句）与 `card_summary`（用 `_to_card_summary` 从 detail 截断到 ≤120 字）
- **AND** `events.card_summary` / `events.detail_summary` 仍非空

#### Scenario: 字段长度护栏
- **WHEN** LLM 返回的 `card_summary` 超过 120 字符
- **THEN** 系统在落库前调用 `_to_card_summary` 强制截断到 ≤120 字，避免 iOS 卡片溢出

## MODIFIED Requirements

### Requirement: 摘要分级输出（取代 spec adjust-plan-for-ios-first 的同名要求）
The system SHALL 在事件摘要阶段同时产出短卡片摘要与长详情摘要，且 LLM 路径下两条摘要由模型一次性给出，而非由 Python 后处理拼接 / 截断。

#### Scenario: 一篇事件同时具备双摘要（生效阶段无变化）
- **WHEN** `summarize.run()` 处理一个 `clustered` 事件
- **THEN** 写入 `events.card_summary`（≤120 中文字符）与 `events.detail_summary`（300–500 中文字符），状态置为 `summarized`

## REMOVED Requirements
（无）
