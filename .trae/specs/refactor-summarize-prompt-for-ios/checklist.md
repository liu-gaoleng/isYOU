# Checklist

- [x] LLM Prompt JSON 契约已改为 `{title, card_summary, detail_summary, why_matters}`
- [x] Prompt 中显式声明 card ≤120 中文字符、detail 300–500 中文字符的长度规范
- [x] Prompt 中明确目标读者为互联网/PM/投资人/创业者
- [x] `_summary_llm()` 解析新 JSON，返回 dict 含 card/detail 字段，且对 card 长度做 `_to_card_summary` 兜底护栏
- [x] `_summary_extractive()` 同时产出 card_summary 与 detail_summary
- [x] `run()` 直接把 dict 中的 card_summary / detail_summary 写到 `events` 表对应列
- [x] `EventContent.summary` 列仍非空（保留兼容）
- [x] pytest 27/27 全绿（未引入回归）
- [x] PG 端一次抽取式兜底端到端：events.card_summary 与 events.detail_summary 均非空
