# 「热读」后端 API 接口设计

> 配套文档：PRD.md｜MVP.md｜内容管线方案.md｜原型 prototype/index.html｜数据样例 pipeline_demo/output.json
> 版本 v1.0 ｜ 状态：待评审 ｜ 适用：App 客户端 ⇄ 后端 ⇄ CMS

---

## 0. 通用约定

### 0.1 基础规范
| 项 | 约定 |
|---|---|
| 协议 | HTTPS only |
| BaseURL | `https://api.redu.app/v1`（CMS：`/v1/admin`） |
| 数据格式 | JSON（UTF-8） |
| 命名 | 字段 `snake_case`，时间用 ISO8601（`2026-06-08T08:00:00Z`） |
| 鉴权 | `Authorization: Bearer <access_token>`（JWT） |
| 分页 | `?page=1&size=20`，响应含 `pagination` |
| 幂等 | 写操作支持 `Idempotency-Key` 头（支付/下单） |

### 0.2 统一响应包络
```json
{
  "code": 0,
  "message": "ok",
  "data": { },
  "request_id": "req_abc123"
}
```
- `code = 0` 成功；非 0 为业务错误码。HTTP 状态码同时语义化（200/400/401/403/404/429/500）。

### 0.3 错误码规范
| code | HTTP | 含义 |
|---|---|---|
| 0 | 200 | 成功 |
| 1001 | 401 | 未登录 / token 失效 |
| 1002 | 403 | 无权限（如非会员访问付费内容） |
| 1003 | 404 | 资源不存在 |
| 1004 | 400 | 参数错误 |
| 1005 | 429 | 限流 |
| 2001 | 403 | 需要会员（付费墙触发，附升级引导） |
| 5000 | 500 | 服务端错误 |

### 0.4 分页对象
```json
"pagination": { "page": 1, "size": 20, "total": 153, "has_more": true }
```

---

## 1. 客户端 API（App）

### 1.1 认证模块 `/auth`

#### POST `/auth/login` 登录 / 注册（验证码 / 第三方）
请求：
```json
{ "type": "phone|apple|wechat", "phone": "138...", "code": "1234", "oauth_token": "..." }
```
响应：
```json
{ "access_token": "jwt...", "refresh_token": "...", "expires_in": 7200,
  "user": { "id": "u_001", "nickname": "投资人A", "avatar": "https://...",
            "membership": { "tier": "free|member|pro", "expire_at": null } } }
```

#### POST `/auth/refresh` 刷新令牌
请求 `{ "refresh_token": "..." }` → 返回新 `access_token`。

#### POST `/auth/logout` 登出（失效 refresh_token）

---

### 1.2 今日简报（首页）`/home`

#### GET `/home/briefing` 今日简报聚合（对应原型①）
> 一次返回首页所需全部区块，减少首屏请求数。

Query：`date`（可选，默认今日）
响应：
```json
{
  "date": "2026-06-08",
  "slogan": "每天 10 分钟，读懂科技、金融、AI、宏观四个赛道最值得关注的事",
  "total_count": 78,
  "market_ticker": [
    { "name": "上证指数", "value": "3,210.5", "change_pct": -0.62, "direction": "down" },
    { "name": "纳斯达克", "value": "17,890", "change_pct": 1.21, "direction": "up" },
    { "name": "比特币", "value": "68,200", "change_pct": -2.05, "direction": "down" }
  ],
  "top_headline": {
    "event_id": "evt_1001", "rank": 1, "module": "金融",
    "title": "降准落地：万亿资金涌入，谁先吃到这波红利？",
    "summary": "...", "importance": 92.0, "source_count": 5
  },
  "hot_list": [ /* 见 1.4 榜单条目结构，聚合 TOP10 */ ],
  "feed": [ /* 见 1.5 事件卡片结构，分模块要闻流 */ ]
}
```

---

### 1.3 频道模块 `/channels`

#### GET `/channels/{module}` 频道页（对应原型②/②-b/②-c/②-d）
Path：`module ∈ {tech, finance, ai, macro}`
Query：`page`、`size`
响应：
```json
{
  "module": "ai",
  "updated_at": "2026-06-08T09:41:00Z",
  "top_headline": { /* 该频道 Top1 强对比头条 */ },
  "hot_list": [ /* 该频道 TOP10 热榜 */ ],
  "feed": [ /* 要闻解读卡片流 */ ],
  "pagination": { "page": 1, "size": 20, "total": 56, "has_more": true }
}
```

---

### 1.4 热榜 `/ranking`

#### GET `/ranking` 热榜（首页聚合 / 分频道）
Query：`scope=global|tech|finance|ai|macro`、`limit`（默认 10）
响应：
```json
{
  "scope": "ai", "updated_at": "2026-06-08T09:41:00Z",
  "items": [
    { "rank": 1, "event_id": "evt_2001", "module": "AI",
      "title": "英伟达冲上4万亿：AI泡沫还是新王朝？",
      "hotness": 982000, "trend": "up|new|flat", "rank_change": 2,
      "source_count": 4 }
  ]
}
```
> `trend`：`up`(▲ 上升) / `new`(新上榜) / `flat`(—)，由当前快照对比上次快照计算。

---

### 1.5 事件详情 `/events`

#### GET `/events/{event_id}` 事件详情（对应原型③）
> **付费墙核心接口**：根据会员态返回完整或截断内容。

响应（会员 / 非会员差异在 `deep_content` 与 `is_locked`）：
```json
{
  "event_id": "evt_2001", "module": "AI",
  "title": "英伟达冲上4万亿：AI泡沫还是新王朝？",
  "summary": ["事实句1", "事实句2", "事实句3"],
  "why_matters": "对创业者意味着算力成本结构的重构……",
  "facts": [ { "text": "英伟达市值突破4万亿美元", "source_ref": [1] } ],
  "sources": [
    { "name": "彭博", "level": "S", "url": "https://..." },
    { "name": "财联社", "level": "A", "url": "https://..." }
  ],
  "source_count": 4,
  "published_at": "2026-06-08T08:00:00Z",
  "disclaimer": "本文不构成投资建议",
  "deep_content": { "is_locked": true, "preview": "深度解读前两段……",
                    "paywall": { "required_tier": "member",
                                 "cta": "开通会员，解锁完整深度解读" } },
  "user_state": { "is_favorited": false }
}
```
- 会员：`deep_content.is_locked=false` 且返回完整 `content`。
- 非会员：仅返回 `preview` + `paywall`，触发 `code 2001` 语义（HTTP 仍 200，内容内嵌付费墙）。

#### POST `/events/{event_id}/favorite` 收藏 / 取消
请求 `{ "action": "add|remove" }` → `{ "is_favorited": true }`

#### GET `/events/{event_id}/related` 相关推荐（对应原型「更多·你可能也关注」）
响应：`{ "items": [ /* 事件卡片结构 */ ] }`

---

### 1.6 我的 `/me`

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/me` | 个人信息 + 会员态 |
| GET | `/me/favorites` | 收藏列表（分页） |
| GET | `/me/history` | 阅读历史（分页） |
| GET | `/me/settings` / PUT 同路径 | 推送开关、推送时间等偏好 |

---

### 1.7 会员与支付 `/membership`

#### GET `/membership/plans` 套餐列表
```json
{ "plans": [
  { "id": "member_month", "tier": "member", "name": "会员月卡", "price": 30,
    "period": "month", "store_product_id": "com.redu.member.month" },
  { "id": "member_year", "tier": "member", "name": "会员年卡", "price": 298,
    "period": "year", "daily_equiv": 0.8, "badge": "最划算" }
] }
```

#### POST `/membership/orders` 创建订单
请求 `{ "plan_id": "member_year", "platform": "ios|android" }`（带 `Idempotency-Key`）
响应 `{ "order_id": "ord_001", "store_product_id": "...", "amount": 298 }`

#### POST `/membership/verify` 支付凭证校验（IAP 回执 / 安卓回执）
请求 `{ "order_id": "ord_001", "receipt": "<base64>" }`
响应 `{ "status": "success", "membership": { "tier": "member", "expire_at": "2027-06-08T..." } }`

#### POST `/membership/restore` 恢复购买（iOS 必备）

---

### 1.8 推送与设备 `/push`
| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/push/register` | 上报设备 token（APNs/FCM/厂商） |
| PUT | `/push/preferences` | 早报开关、推送时间 |

---

## 2. CMS 运营后台 API `/v1/admin`

> 鉴权独立（运营账号 + RBAC 角色：编辑 / 审核 / 管理员）。对应内容管线方案 §8.2 质检后台。

### 2.1 内容审核
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/events` | 事件列表（按 `status / module / 时间` 过滤）`status: reviewing/published/rejected` |
| GET | `/admin/events/{id}` | 详情（含原文、AI 产出、校验结果） |
| PUT | `/admin/events/{id}` | 编辑标题/摘要/解读/模块/标签 |
| POST | `/admin/events/{id}/review` | 审核：`{ "action": "approve|reject", "reason": "" }` |
| POST | `/admin/events/{id}/publish` | 发布 / 撤回 |
| POST | `/admin/events/{id}/pin` | 置顶 / 取消（首页头条位） |
| POST | `/admin/events/merge` | 合并事件 `{ "target_id", "source_ids": [] }` |
| POST | `/admin/events/{id}/split` | 拆分错聚事件 |
| POST | `/admin/events/{id}/push` | 触发推送 |

### 2.2 校验结果（对应方案 §8.1 防幻觉护栏）
GET `/admin/events/{id}/validation` 返回机器卡点结果：
```json
{ "checks": [
  { "type": "citation", "pass": true },
  { "type": "number_consistency", "pass": false, "detail": "摘要数字 4万亿 原文未匹配" },
  { "type": "compliance", "pass": true },
  { "type": "disclaimer", "pass": true }
], "overall": "need_review" }
```

### 2.3 信源管理（对应方案 §2.2）
| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/admin/sources` | 信源列表 / 新增 |
| PUT / DELETE | `/admin/sources/{id}` | 改权重·等级·启停 / 删除 |

### 2.4 运营数据
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/admin/stats/pipeline` | 管线指标：采集量、去重率、分类准确、人工打回率、端到端时延 |
| GET | `/admin/stats/business` | 业务：DAU、留存、推送打开率、付费转化 |

---

## 3. 数据模型映射（API ⇄ 管线产物 ⇄ 库表）

| API 字段 | 来源（管线方案 / output.json） | 库表（MVP §对应） |
|---|---|---|
| `event_id` | events.id | `events` |
| `module` | 阶段三分类 | `events.module` |
| `title / summary / why_matters / facts` | 阶段五摘要产物 | `event_contents` |
| `sources[] / source_count` | 阶段四聚类多源（按不同信源） | `event_articles` + `sources` |
| `importance / hotness / rank / trend` | 阶段六评分 + Redis ZSet | `events.importance` / Redis |
| `deep_content` | 付费深度解读 | `event_contents.deep_content` |
| `membership / order` | 会员订单系统 | `users` / `orders` |

> 与 Demo 的对齐：`output.json` 中的 `module/importance/source_count/sources/title/summary/why_matters` 已对应客户端 1.5 详情接口的主体字段；生产环境补充 `event_id / facts.source_ref / deep_content / user_state`。

---

## 4. 关键设计说明

1. **首页聚合接口**（`/home/briefing`）：首屏一次取齐，降低弱网下的请求数与白屏，符合「10 分钟读完」的体验目标。
2. **付费墙在详情接口内完成**：服务端按会员态裁剪 `deep_content`，前端无需判断权限，避免越权风险。
3. **热榜走 Redis**：`/ranking` 直读 ZSet，趋势 `trend` 由快照差分，支撑实时刷新。
4. **CMS 与客户端共库不共接口**：审核态（reviewing/rejected）内容绝不进客户端接口，从源头保证「脏内容不外发」。
5. **合规字段前置**：金融/宏观类强制 `disclaimer`，由后端统一注入，避免前端漏显。

---
*文档结束 · v1.0*
