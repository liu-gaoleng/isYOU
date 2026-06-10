# Tasks

- [x] Task 1: 重写 LLM Prompt 与 JSON schema
  - [x] SubTask 1.1: 把 Prompt 中的 JSON 输出契约改为 `{"title":"","card_summary":"","detail_summary":"","why_matters":""}`，明确字符长度规范（card ≤120 中文字符；detail 300–500 中文字符；不杜撰；事实/解读分区）
  - [x] SubTask 1.2: 在 Prompt 顶部加「目标读者：互联网/PM/投资人/创业者」一行，引导模型用专业但口语化的表述

- [x] Task 2: 重构 `_summary_llm` 与 `_summary_extractive` 与 `run()`
  - [x] SubTask 2.1: `_summary_llm` 解析新 JSON，返回 dict 含 `card_summary`/`detail_summary`/`why_matters`/`title`/`method`/`llm_meta`；不再返回 `summary` 列表
  - [x] SubTask 2.2: `_summary_extractive` 用 `extractive_summary(content, max_sentences=3)` 拼出 detail 文本，再用 `_to_card_summary` 截断成 card；返回相同结构
  - [x] SubTask 2.3: `run()` 直接写 `ev.card_summary`/`ev.detail_summary` 来自 summary dict；并对 LLM 返回的 card 做 `_to_card_summary` 长度护栏（>120 字时截断）
  - [x] SubTask 2.4: `EventContent.summary` 列保留：LLM 路径下写 `[card_summary, detail_summary]`，抽取式路径下用 `extractive_summary` 列表，保证 `event_contents` 表非空数组（兼容既有结构）

- [x] Task 3: 验证修复有效
  - [x] SubTask 3.1: 仓库根目录 `pytest`，27/27 全绿不退化
  - [x] SubTask 3.2: 在 PG 上做一次「不依赖 LLM」的端到端：清空一条事件的 card/detail，用 `python -c` 调 `summarize.run()` 走抽取式兜底，确认 events 表 card/detail 仍正确写入

# Task Dependencies
- Task 2 depends on Task 1（先定 schema 再改代码）
- Task 3 depends on Task 2
