#「热读」iOS 真机端到端冒烟 Runbook

> 用途：在 Mac + iPhone 真机上跑完一遍 4.1–4.3 的核心闭环（账号 / IAP / APNs / 埋点），所有验证项都不依赖 admin Web 后台。
> 适用阶段：[上线剩余工作清单.md](./上线剩余工作清单.md) §5.1「全链路真机联调」前置自查；§4.2 推送 4.3 埋点上线前。
> 维护：每条命令/路径都来自当前仓库实物，引用见行级链接。

---

## 0. 涉及主体与一句话职责

| # | 主体 | 职责 | 进入方式 |
|---|---|---|---|
| A | **Mac 开发机** | 起 FastAPI + Postgres/Redis + Celery + Xcode 部署 | 本地终端 + Xcode |
| B | **iPhone 真机** | 跑 App、点收藏分享、走 IAP、收推送 | Xcode → Run（真机） |
| C | **Apple Developer 后台** | 申请 .p8 APNs Key、配置 Bundle ID、配置 IAP 产品 | https://developer.apple.com/account |
| D | **App Store Connect** | 创建 Sandbox 测试账号、登记 IAP 商品 | https://appstoreconnect.apple.com |
| E | **Postgres 容器** | 直查埋点 / device_tokens / push_records 验证 | `docker exec -it rd_postgres psql -U rd -d redu` |

**不参与**（已确认仓库当前缺失，不要找）：
- 没有 admin Web 后台（[`prototype/admin.html`](./prototype/admin.html) 仅原型，连 mock_server）
- 没有 TestFlight / Fastlane 配置
- 没有 `GET /analytics/events` 查询接口（埋点只能 psql 直查）
- 没有 `.storekit` Configuration（沙盒 IAP 走真链路）

---

## 1. 前置准备清单（含出处与获取途径）

> 全部准备物按"先 Apple 侧、再 Mac 本地"的顺序列。打钩后再开始执行 §3 步骤。

### 1.1 Apple 侧（一次性，半小时左右）

| # | 准备物 | 出处 / 获取方式 | 验证方法 |
|---|---|---|---|
| **P1** | **Apple Developer 账号**（个人 99 USD/年） | https://developer.apple.com/programs/enroll/ | 能登 developer.apple.com 并看到 Certificates/Identifiers/Profiles |
| **P2** | **Bundle ID 注册** `app.redu.ios` | developer.apple.com → Identifiers → `+` → App IDs；Capabilities 勾选 **Sign in with Apple** + **Push Notifications** + **In-App Purchase** | 列表能看到 `app.redu.ios`，三项 Capability 均为"Enabled" |
| **P3** | **APNs Auth Key（.p8）** | developer.apple.com → Keys → `+` → 勾选 Apple Push Notifications service (APNs) → Continue → Register → **下载 `AuthKey_XXXX.p8`（仅这一次能下，过期即废）** | Mac 本地存好该文件路径，记下：① **Key ID**（10 位）② **Team ID**（账号右上角） |
| **P4** | **IAP 三档产品**（月/季/年） | App Store Connect → 我的 App → 创建 App（用 `app.redu.ios`）→ 功能 → App 内购买项目 → `+` → 自动续期订阅 → 创建以下 3 个 product_id：<br/>• `com.redu.app.member.monthly`<br/>• `com.redu.app.member.quarterly`<br/>• `com.redu.app.member.yearly`<br/>（id 来自 [`.env.example`](./.env.example) `RD_BILLING_PRODUCT_MONTHLY/QUARTERLY/YEARLY`） | App Store Connect 三个商品均显示「准备提交」或「已批准」 |
| **P5** | **Sandbox 测试账号** | App Store Connect → 用户与访问 → 沙盒测试员 → `+` → 用一个**全新邮箱**（不能是你日常 Apple ID 用过的） → 填假地区/出生日期 → 保存 | 邮箱本身不需收验证邮件；登录验证在 §3.3 步骤 |
| **P6** | **Provisioning Profile**（开发） | Xcode → Settings → Accounts → Apple ID 登入 → 选 Team → "Download Manual Profiles"；首次真机部署 Xcode 会自动创建 Development Profile | iPhone Run 时无签名错误 |

> 提示：P3 的 `.p8` 文件**只能下载一次**，丢了只能 revoke 重申。下完立刻另存到加密目录如 `~/Documents/keys/`，**不要 commit 进 Git**。

### 1.2 Mac 本地（已就绪，可直接走）

| # | 准备物 | 出处 / 在仓库哪里 | 状态 |
|---|---|---|---|
| **M1** | Docker Desktop | https://www.docker.com/products/docker-desktop/ | 装好并运行 |
| **M2** | Python 3.11 + `.venv` | 已存在：[`content_engine/.venv`](./content_engine/.venv) | 已就绪 |
| **M3** | Postgres / Redis 容器编排 | [`docker-compose.yml`](./docker-compose.yml) 容器名 `rd_postgres` / `rd_redis` | 由 [`scripts/dev_up.sh`](./scripts/dev_up.sh) 一键拉起 |
| **M4** | `.env`（仓库根） | 模板 [`.env.example`](./.env.example)；实际 [`.env`](./.env) 已存在 | 见 §1.3 补全 |
| **M5** | Xcode 16+ | https://apps.apple.com/cn/app/xcode/id497799835（暂未安装则后续步骤的 §3 跳过即可，先走 §2 完善 .env / §4 干运行验证） | 装好并打开 |
| **M6** | iOS 工程 | [`ios/ReDu.xcodeproj`](./ios/ReDu.xcodeproj) | 已就绪 |
| **M7** | Mac 局域网 IP | 命令：`ipconfig getifaddr en0` | 跑时记录 |
| **M8** | iPhone 真机 | iOS 16+，与 Mac 同 Wi‑Fi | 准备好 |

### 1.3 `.env` 必填变量（来自 [.env.example](./.env.example) 模板）

打开仓库根的 `.env`，逐条比对补齐：

```env
# —— 数据库（dev_up.sh 默认即可）
DATABASE_URL=postgresql+psycopg://rd:rd@localhost:5432/redu

# —— Dev 通道：不走 Apple Sign-In 也能签 JWT，方便冒烟
RD_AUTH_DEV_LOGIN_ENABLED=true

# —— 管理看板鉴权（自己随便起一个长串）
RD_ADMIN_TOKEN=<paste-a-random-string>

# —— APNs（P3 拿到的）
RD_APNS_TEAM_ID=<Apple 账号右上角 10 位 Team ID>
RD_APNS_KEY_ID=<.p8 文件名上的 10 位 Key ID>
RD_APNS_BUNDLE_ID=app.redu.ios
RD_APNS_PRIVATE_KEY_PATH=/Users/<you>/Documents/keys/AuthKey_XXXX.p8
RD_APNS_ENVIRONMENT=sandbox    # Xcode Debug 安装走 sandbox

# —— IAP 三档产品 id（与 P4 在 App Store Connect 注册的一致）
RD_BILLING_PRODUCT_MONTHLY=com.redu.app.member.monthly
RD_BILLING_PRODUCT_QUARTERLY=com.redu.app.member.quarterly
RD_BILLING_PRODUCT_YEARLY=com.redu.app.member.yearly
```

变量含义出处：[`content_engine/config/settings.py`](./content_engine/config/settings.py#L322-L355)（APNs 段）、[`.env.example`](./.env.example#L97-L111)（推送）、[`.env.example`](./.env.example#L78-L80)（IAP）。

---

## 2. Mac 端：起后端

### 2.1 一键起 Docker 依赖 + 跑迁移

```bash
cd /Users/bytedance/liu/isYOU
bash scripts/dev_up.sh
```

脚本来源：[`scripts/dev_up.sh`](./scripts/dev_up.sh)。完成后控制台会打印当前 Alembic head 版本（应是 `0015_analytics_events`）。

### 2.2 起 FastAPI（端口 **8000**，真机会连）

新开终端：

```bash
cd /Users/bytedance/liu/isYOU
set -a; . ./.env; set +a
content_engine/.venv/bin/python -m uvicorn content_engine.api.app:app \
  --host 0.0.0.0 --port 8000
```

健康检查（另起终端）：

```bash
curl -fsS http://127.0.0.1:8000/healthz   # 期望 {"status":"ok"}
```

`/healthz` 定义在 [`content_engine/api/app.py`](./content_engine/api/app.py#L37-L40)，不查 DB，仅探活。

### 2.3 起 Celery worker + beat（APNs 派发依赖）

再开两个终端：

```bash
# 终端 A：worker
content_engine/.venv/bin/celery -A content_engine.tasks.celery_app worker -l info

# 终端 B：beat（每分钟扫一次 push_settings 命中 HH:MM 派发）
content_engine/.venv/bin/celery -A content_engine.tasks.celery_app beat -l info
```

调度表来源：[`content_engine/tasks/celery_app.py`](./content_engine/tasks/celery_app.py#L46-L67)。
派发实现：[`content_engine/tasks/push_tasks.py`](./content_engine/tasks/push_tasks.py#L80)。

> 没配 `.p8` / `RD_APNS_*` 时 [`apns.configured == False`](./content_engine/tasks/push_tasks.py#L148-L156)，任务会走"干运行"只写 `push_records` 不实际发推送——可以用来先验证 §3.4 之外的链路。

### 2.4 记录 Mac 局域网 IP

```bash
ipconfig getifaddr en0    # 例如 192.168.1.50
```

下面 iOS 端要把这个 IP 填进 `Endpoint.swift`。

---

## 3. iOS 端：装到真机

### 3.1 改 BASE_URL（**必改**）

编辑 [`ios/ReDu/Core/Network/Endpoint.swift`](./ios/ReDu/Core/Network/Endpoint.swift#L10-L15)：

```swift
static let baseURL = URL(string: "http://192.168.1.50:8000")!   // 改成你的 Mac 局域网 IP
```

ATS 已配了 `127.0.0.1` / `localhost` 明文豁免（[`Info.plist`](./ios/ReDu/Resources/Info.plist#L41-L57)），HTTP 直连 OK；冒烟完务必改回去或换 HTTPS 域名。

### 3.2 Xcode 部署

1. 打开 `ios/ReDu.xcodeproj`
2. ReDu Target → Signing & Capabilities → Team 选 P1 的开发者 Team
3. Bundle Identifier 保持 `app.redu.ios`（与 P2 注册一致）
4. 顶部 destination 选连着的 iPhone → Cmd+R
5. 第一次装会弹"开发者未受信任"：iPhone 设置 → 通用 → VPN 与设备管理 → 信任

### 3.3 iPhone 登 Sandbox 账号（IAP 必备）

- iPhone → 设置 → App Store → 滚到底 → 沙盒账户 → 登录 P5 创建的 Sandbox 账号
- **不要登成你日常的主 Apple ID**，否则 IAP 会真扣钱

---

## 4. 真机冒烟 4 条链路（按顺序）

> 每条链路给【操作】+【验证 SQL】。验证全部走 psql：
> ```bash
> docker exec -it rd_postgres psql -U rd -d redu
> ```

### 4.1 启动 + 埋点

**操作**：
1. iPhone 上首启 App → 通知权限弹窗点"允许"
2. 进首页 → 切几次 Tab
3. 把 App 切到后台（home / 上滑）— 触发 `flushNow`，缓冲会立刻上送

**验证**：

```sql
SELECT name, COUNT(*), MAX(created_at) AS latest
FROM analytics_events
WHERE created_at > NOW() - INTERVAL '5 min'
GROUP BY name
ORDER BY 1;
```

期望至少 `app_open`。事件名白名单见 [`content_engine/api/schemas.py`](./content_engine/api/schemas.py#L320-L329)。

### 4.2 详情 + 收藏 + 分享 埋点

**操作**：
1. 点首页任一卡片进详情 → 触发 `event_view`
2. 点收藏⭐ → 触发 `favorite { event_id, action: "add" }`
3. 再点一次取消 → `action: "remove"`
4. 点右上分享 → ShareLink 拉系统分享面板（任选一项即可）→ 触发 `share`
5. 切后台一次（强制 flush）

**验证**：

```sql
SELECT name, props, created_at
FROM analytics_events
WHERE name IN ('event_view','favorite','share')
ORDER BY created_at DESC
LIMIT 10;
```

期望见到 `event_view` × N、`favorite` 两条（add/remove）、`share` × 1。

### 4.3 IAP（沙盒）链路

**操作**：
1. 触发付费墙：详情页非会员点"解锁全文" / 我的页点"成为会员"
2. 触发 `paywall_view`
3. 选月会员 → Sandbox 弹框 → 输入 P5 沙盒账号密码确认
4. 等"购买成功" toast / 会员态切换 → 触发 `purchase_success`

**验证**：

```sql
-- IAP 核销记录（应为 Sandbox 环境）
SELECT id, product_id, environment, original_transaction_id, created_at
FROM iap_transactions
ORDER BY id DESC LIMIT 5;

-- 用户会员态（30 天后到期）
SELECT id, email, member_tier, member_expire_at
FROM users
ORDER BY id DESC LIMIT 5;

-- 付费埋点
SELECT name, props, created_at
FROM analytics_events
WHERE name = 'purchase_success'
ORDER BY created_at DESC LIMIT 5;
```

期望：`iap_transactions.environment='Sandbox'`、`users.member_tier='member'`、`member_expire_at` 落在月会员档约 30 天后。

### 4.4 APNs 推送 + 点击直达

**前提**：已完成 P3 `.p8`、`.env` 中 `RD_APNS_*` 全填、Celery beat/worker 在跑。

#### 4.4.1 验证 device token 已注册

应用启动允许通知后，iOS 自动把 token POST 到后端（实现见 [`content_engine/api/routers/me.py`](./content_engine/api/routers/me.py#L277-L329)）。

```sql
SELECT user_id, LEFT(token, 16) AS token_prefix, env, created_at
FROM device_tokens
ORDER BY id DESC LIMIT 5;
```

期望：`env='sandbox'`、token 非空。
没看到？检查：① iOS 端是否登录了 ② 通知权限是否允许 ③ uvicorn 日志里有没有 `POST /api/v1/me/devices`。

#### 4.4.2 把 push_time 调到 "now + 2 分钟"

例如当前 09:41，调到 09:43：

```sql
UPDATE push_settings
SET daily_push = TRUE, push_time = '09:43'
WHERE user_id = <上面查到的 user_id>;
```

#### 4.4.3 等 beat 命中（也可手动触发）

等 1‑2 分钟。如果不想等，强制立即触发一次（注意：仍按用户 `push_time` 命中，所以要保证当前时间正好等于你设的 HH:MM）：

```bash
cd /Users/bytedance/liu/isYOU
set -a; . ./.env; set +a
content_engine/.venv/bin/python -c \
  "from content_engine.tasks.push_tasks import dispatch_daily_briefs; print(dispatch_daily_briefs())"
```

#### 4.4.4 收推送 + 点击

- **App 后台**：iPhone 锁屏 / 桌面看到 banner → 点开 → 直达"今日热读"那篇详情
- **App 前台**：通知中心可见

**验证**：

```sql
-- 派发记录（含 APNs 返回状态）
SELECT id, biz_id, user_id, status, apns_status, sent_at, error
FROM push_records
ORDER BY id DESC LIMIT 5;

-- 点击埋点
SELECT name, props, created_at
FROM analytics_events
WHERE name = 'push_open'
ORDER BY created_at DESC LIMIT 5;
```

期望：`push_records.status='success'`、`apns_status=200`；点击后 `analytics_events.name='push_open'` 出现一条，`props.event_id` 与 banner 那篇一致。

---

## 5. 综合收尾

### 5.1 埋点全量盘点

```sql
SELECT name, COUNT(*), MAX(created_at) AS latest
FROM analytics_events
WHERE created_at > NOW() - INTERVAL '1 hour'
GROUP BY name
ORDER BY 1;
```

期望命中 8 个事件中至少 6 个：`app_open / event_view / paywall_view / purchase_success / push_open / favorite / share`。`search` 暂未落地（[`上线剩余工作清单.md`](./上线剩余工作清单.md#L74) §4.3 末尾已说明），缺一条正常。

### 5.2 看板 JSON（无 UI）

```bash
curl -s http://127.0.0.1:8000/api/v1/admin/metrics/overview \
  -H "X-Admin-Token: $(grep RD_ADMIN_TOKEN .env | cut -d= -f2)"
```

来源 [`content_engine/api/routers/metrics.py`](./content_engine/api/routers/metrics.py)。**注意**：此看板**只含内容/管线/审核维度，不含埋点**——埋点必须 psql。

### 5.3 复原（冒烟完务必做）

1. iOS [`Endpoint.swift`](./ios/ReDu/Core/Network/Endpoint.swift#L10-L15) `baseURL` 改回 `http://127.0.0.1:8000` 或正式域名
2. SQL 改回正常 `push_time`（一般 08:00）：
   ```sql
   UPDATE push_settings SET push_time='08:00' WHERE user_id=<冒烟用户 id>;
   ```
3. 真机 App 卸载或保留均可
4. `.env` 中如果是给临时冒烟改的，记得回滚（特别是 `RD_APNS_ENVIRONMENT`、`RD_AUTH_DEV_LOGIN_ENABLED`）

---

## 6. 常见问题排查

| 现象 | 排查 |
|---|---|
| iOS 启动后所有接口失败 | Mac 与 iPhone 是否同 Wi‑Fi；`Endpoint.swift baseURL` 是否填了 Mac 的 IP（不是 `127.0.0.1`）；Mac 防火墙是否阻挡 8000 端口 |
| 没收到通知 | `apns.configured` 是否 True（缺 `.p8` / Key ID / Team ID 之一就走干运行）；`push_records.error` 看 APNs 报错；`device_tokens.env` 是 `sandbox` 还是 `production`（要与 [`AppDelegate.swift`](./ios/ReDu/App/AppDelegate.swift#L25-L31) Debug 编译一致即 sandbox） |
| IAP 弹"无法连接 App Store" | 是否登了 P5 Sandbox 账号；P4 的 product_id 是否与 `.env` `RD_BILLING_PRODUCT_*` 完全一致 |
| 埋点查不到 | iOS 端 buffer 阈值 10 条（[`AnalyticsTracker.swift`](./ios/ReDu/Core/Analytics/AnalyticsTracker.swift#L65)）；触发 6 条以上后切后台 `flushNow` 强刷；uvicorn 日志看 `POST /api/v1/analytics/events` 是否进来 |
| Apple Sign-In 失败 | [`ReDu.entitlements`](./ios/ReDu/ReDu.entitlements) 是否含 `com.apple.developer.applesignin`；P2 Bundle ID 是否开了 Sign in with Apple Capability |

---

## 7. 仓库目前缺失的能力（提前预期）

- **没有 TestFlight 流程**：真机部署只能 Xcode Run；要内测要先配 Fastlane / Xcode Cloud（[`iOS-App技术选型.md`](./iOS-App技术选型.md) §6 提及但未落地）
- **没有运营 admin Web**：会员手动开通只能 psql `UPDATE users SET member_tier='member', member_expire_at=NOW()+INTERVAL '365 day' WHERE id=...;`，或用 dev 通道 [`/api/v1/auth/dev-login`](./content_engine/api/routers/auth.py) 传 `{"as_member": true}` 签出"远期会员"
- **没有 search 事件**：埋点 8 个事件里 `search` 是预留，等搜索页落地再接（清单 §2.x 未开发）
- **没有 GET /analytics/events**：埋点查询只能 psql
- **`.p8` 文件不在仓库**：上 §1.1 P3 自行下载并配置 `RD_APNS_PRIVATE_KEY_PATH`

---

> 本文档定位为"冒烟手册"，与 [上线剩余工作清单.md](./上线剩余工作清单.md) §5.1 全链路真机联调互为补充。冒烟跑完后，在清单 §4.2 / §4.3 / §5.1 对应行打上「真机已验」标记并记录日期。
