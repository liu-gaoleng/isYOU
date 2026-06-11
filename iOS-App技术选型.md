# 「热读」iOS App 全栈技术选型

> 版本 v1.0 ｜ 状态：已采纳 ｜ 创建日期：2026-06-10
>
> 配套文档：[PRD.md](./PRD.md) ｜ [MVP.md](./MVP.md) ｜ [API设计.md](./API设计.md) ｜ [内容管线方案.md](./内容管线方案.md) ｜ [内容引擎实施计划.md](./内容引擎实施计划.md)
>
> 设计原则：**全部对齐 2025–2026 年国内一线互联网公司一线产品的主流技术栈**，不为短期便捷而妥协项目完善度；客户端 **iOS-first**（Sign in with Apple + APNs + StoreKit 2），安卓后续兼容。

---

## 0. 总览：三端 + 数据/基础设施

```
┌──────────────────────────────────────────────────────┐
│  iOS App         Swift 5.9+ / SwiftUI / Combine       │  ← 端
│  CMS 后台        React 18 + TypeScript + Vite + AntD  │  ← 端
└──────────────┬───────────────────────────────────────┘
               │ HTTPS / TLS 1.3
┌──────────────┴───────────────────────────────────────┐
│  后端 API       Python 3.12 + FastAPI + uvicorn       │  ← 服务
│  内容引擎       SQLAlchemy 2.0 + Alembic              │
│  消息队列       Celery + Redis                        │
└──────────────┬───────────────────────────────────────┘
               │ 内网
┌──────────────┴───────────────────────────────────────┐
│  PostgreSQL 16 + pgvector  ｜ Redis 7  ｜ OSS         │  ← 存储
│  Sentry / Prometheus / Grafana / SLS                  │  ← 可观测
│  阿里云 ECS / RDS / SLB / DNS  ｜ Docker + GitHub CI  │  ← 部署
└──────────────────────────────────────────────────────┘
```

---

## 1. iOS App 技术栈（核心）

### 1.1 语言 / IDE / 构建

| 维度 | 选型 | 备注 |
|---|---|---|
| 语言 | **Swift 5.9+** | Apple 推荐唯一选择；OC 仅在引入老 SDK 时使用 |
| IDE | **Xcode 16** | 最低 15.4 |
| Swift 包管理 | **Swift Package Manager (SPM)** | Apple 官方；CocoaPods 已不再维护新功能；Carthage 已淘汰 |
| 最低支持版本 | **iOS 16.0** | 覆盖 95%+ 在线设备；启用 SwiftUI NavigationStack / Charts / SwiftData 等新 API |
| 部署目标 | iPhone（通用：iPad 后续） | 暂不上 visionOS / macOS Catalyst |

### 1.2 UI 与架构

| 维度 | 选型 | 理由 |
|---|---|---|
| UI 框架 | **SwiftUI 主 + UIKit 兜底** | 新项目首选；少数复杂动画/相机/WebView 场景用 UIKit |
| 架构模式 | **MVVM + Combine** | SwiftUI 原生友好；TCA 学习成本高，团队熟悉后再考虑 |
| 路由 | **NavigationStack（iOS 16+）** | 替代旧 NavigationView，深链接友好 |
| 状态管理 | `@Observable`（Swift 5.9 macro） + `@State` / `@Environment` | iOS 17+ 优先；iOS 16 兼容 ObservableObject |
| 设计系统 | 自建 DSKit（基于 SwiftUI），对齐 [prototype/](./prototype) 视觉规范 | 避免引入 Material 等他厂体系 |

### 1.3 网络与数据

| 维度 | 选型 | 备注 |
|---|---|---|
| 网络 | **URLSession + async/await** | 系统原生，零三方依赖；Alamofire 仅在需要复杂拦截器时引入 |
| JSON | **Codable** | 系统原生 |
| 网络抽象 | 自建 `APIClient` 协议（`enum Endpoint` + `URLRequest` 工厂） | 与后端 [/api/v1/feed](./content_engine/api/routers/brief.py) 等接口对齐 |
| 缓存 | **URLCache + 自定义 ETag** + 内存 LRU | 列表分页缓存 |
| 持久化 | **SwiftData (iOS 17+) / Core Data (iOS 16 fallback)** | Realm 已是非主流；SQLite 直接用不推荐 |
| 安全存储 | **Keychain Services**（封装 KeychainAccess 或自写） | 存 Apple 用户 ID / token |
| 用户偏好 | **UserDefaults + AppStorage** | 模块开关、阅读历史等 |

### 1.4 登录、推送、支付（产品强需求）

| 模块 | 选型 | 备注 |
|---|---|---|
| 登录 | **Sign in with Apple**（`AuthenticationServices`） | App Store 强制（含其他第三方登录时） |
| 推送 | **APNs + UserNotifications + Notification Service Extension** | 不接极光/个推（合规与可控） |
| 订阅/支付 | **StoreKit 2** | iOS 15+ 标配，订阅生命周期管理 |
| 应用内更新 | App Store 标准流程 + 强制升级提示（自实现） | 不用 hot fix |

### 1.5 媒体、性能、可观测

| 维度 | 选型 |
|---|---|
| 图片 | **AsyncImage**（SwiftUI 原生）；复杂场景用 **Kingfisher** |
| 列表 | `LazyVStack` / `List` + 分页（cursor 与 [/feed](./content_engine/api/routers/brief.py) 对齐） |
| 动画 | SwiftUI `.animation` / `withAnimation` / matchedGeometryEffect |
| Markdown / 富文本 | `AttributedString`（iOS 15+）+ swift-markdown |
| 崩溃 / 性能 | **Sentry-Cocoa** | 与后端统一平台，行业头部方案 |
| 日志 | **OSLog**（系统原生 unified logging） |
| A/B 与埋点 | 自建简单埋点 → POST `/api/v1/events` | 暂不接火山引擎/神策，避免锁定 |

### 1.6 测试与质量

| 维度 | 选型 |
|---|---|
| 单元测试 | **XCTest** |
| UI 测试 | **XCUITest** |
| 快照测试 | **swift-snapshot-testing**（Point-Free） |
| 静态检查 | **SwiftLint** |
| 格式化 | **swift-format** |
| 覆盖率 | Xcode Code Coverage + Codecov 上报 |

### 1.7 工程目录约定

```
ios/
├── ReDu.xcodeproj
├── ReDu/
│   ├── App/                # AppDelegate、入口、深链接
│   ├── Features/           # 按业务功能纵切（Feed / Detail / Profile / Subscription）
│   ├── Core/
│   │   ├── Network/        # APIClient / Endpoint / DTO
│   │   ├── Persistence/    # SwiftData ModelContainer
│   │   ├── Auth/           # Sign in with Apple 流程
│   │   └── Push/           # APNs / 通知中心
│   ├── DesignSystem/       # 颜色 / 字号 / 组件库
│   └── Resources/          # Assets.xcassets / Localizable
└── ReDuTests/ ReDuUITests/
```

---

## 2. 后端 API 技术栈（已落地，作为现状记录）

| 维度 | 选型 | 现状 |
|---|---|---|
| 语言 | Python 3.12 | ✅ |
| Web 框架 | **FastAPI** + uvicorn（gunicorn 生产多 worker） | ✅ [content_engine/api](./content_engine/api) |
| ORM | **SQLAlchemy 2.0** + Alembic | ✅ [models/](./content_engine/models) [migrations/](./content_engine/migrations) |
| 配置 | pydantic-settings | ✅ |
| 数据校验 | pydantic v2 | ✅ |
| LLM SDK | OpenAI 兼容 HTTP（`RD_LLM_*`） | ✅ Provider 抽象 |
| Embedding | **sentence-transformers**（bge-small-zh-v1.5 / 512 维） | ✅ Local + Remote 双 Provider |
| 队列 | **Celery + Redis** | 阶段 4.3 接入（依赖已装） |
| 测试 | pytest + httpx | ✅ 41 用例 |
| 静态检查 | ruff | ✅ CI 启用 |
| 容器 | Docker（多阶段构建） | ✅ [Dockerfile](./Dockerfile) |

---

## 3. CMS 后台技术栈（未启动，建议）

> 目标：审稿/置顶/拉黑/信源管理/质检流水/数据看板。仅内部员工使用，不对外。

| 维度 | 选型 | 理由 |
|---|---|---|
| 语言 | **TypeScript 5.x** | 行业默认；强类型 |
| 框架 | **React 18** | 生态最全；字节、阿里、腾讯主流 |
| 构建 | **Vite 5** | 替代 webpack/CRA，启动快 |
| 路由 | **React Router 6** | 主流 |
| 状态 | **Zustand** + **TanStack Query** | 服务端状态/客户端状态分离；Redux 太重 |
| UI 库 | **Ant Design 5** | 国内中后台事实标准；快速搭表单/表格/审核流 |
| 脚手架 | **Ant Design Pro** 模板裁剪 | 自带权限/菜单/Mock |
| 表单 | AntD `Form` + zod | 复杂表单也能 hold 住 |
| 富文本 | **TipTap / ProseMirror** | 替代 wangEditor，业界主流 |
| 图表 | **Apache ECharts** + echarts-for-react | 国内中后台第一 |
| 国际化 | i18next（暂不开） | 留接口 |
| 打包/部署 | Vite 产物 → Nginx 静态托管 / OSS + CDN | |
| 测试 | Vitest + React Testing Library + Playwright（E2E 只跑核心流） | |
| Lint | ESLint + Prettier + lint-staged | |
| 包管理 | **pnpm** | 速度最快、节省磁盘 |

> 不选 Vue/Nuxt：CMS 不依赖 SEO；React 生态更适合复杂表单与大表格。

---

## 4. 数据 / 缓存 / 存储

| 用途 | 选型 | 备注 |
|---|---|---|
| 主数据库 | **PostgreSQL 16** + `pgvector` 扩展 | RDS 托管，1 主 1 从（流量起来后加只读副本） |
| 缓存 / 榜单 / 队列 broker | **Redis 7** | 阿里云 Tair / 腾讯云 Redis |
| 对象存储 | **阿里云 OSS / 腾讯云 COS** | 静态图片、APNs 证书加密备份、备份归档 |
| 全文检索（后续） | PostgreSQL `pg_trgm` + `tsvector` 起步；流量大切 **Meilisearch / OpenSearch** | 暂缓 |
| CDN | 阿里云 CDN / Cloudflare（境外） | 静态资源加速 |

---

## 5. AI / 算法依赖

| 模块 | 选型 |
|---|---|
| 嵌入模型（默认） | **bge-small-zh-v1.5**（512 维，本地推理） |
| 嵌入模型（远程可切） | OpenAI text-embedding-3-small / 智谱 embedding | 通过 [EmbeddingProvider](./content_engine/services/embedding.py) 抽象 |
| 摘要 LLM | OpenAI 兼容（默认 gpt-4o-mini）；国内可换 **DeepSeek / 通义千问 / 豆包** | 通过 `RD_LLM_*` 环境变量切换 |
| 向量索引 | pgvector `ivfflat` / `hnsw` | DB 内建，简化运维 |
| 离线复核 | scikit-learn HDBSCAN | 阶段 2.4 |

---

## 6. 部署 / 运维 / DevOps

| 维度 | 选型 | 备注 |
|---|---|---|
| 云厂商 | **阿里云**（首选）/ 腾讯云（备选） | 国内合规与备案配套 |
| 计算 | ECS（MVP 单机）→ ACK / TKE（K8s，规模期） | |
| 数据库 | **RDS PostgreSQL 16 + pgvector** | 不自建 |
| 缓存 | **云数据库 Redis 7** | 不自建 |
| 域名 | 阿里云 DNS + ICP 备案 | `api.redu.app` `cms.redu.app` |
| HTTPS | **Let's Encrypt / 阿里云免费 SSL** + 反向代理 Nginx | TLS 1.3 |
| 容器 | **Docker** + **docker-compose**（MVP）→ K8s（规模期） | ✅ 已就位 |
| 镜像仓库 | **ACR**（阿里云容器镜像）/ GHCR | |
| CI | **GitHub Actions** | ✅ ruff + pytest + docker build |
| CD | MVP 期 SSH + `docker compose pull && up -d`；规模期 ArgoCD GitOps | |
| 配置/密钥 | `.env`（MVP）→ **阿里云 KMS / 腾讯云 KMS** | 不存代码库 |
| iOS 分发 | **TestFlight**（内测）→ App Store Connect 正式 | Fastlane 自动化打包 |
| iOS 自动构建 | **Xcode Cloud** / GitHub Actions + macOS runner + fastlane | |

---

## 7. 可观测 / 安全 / 合规

| 维度 | 选型 |
|---|---|
| 错误追踪 | **Sentry**（iOS + 后端 + CMS 三端统一） |
| 日志 | OSLog（iOS）/ stdlog → **阿里云 SLS** 或 Loki + Grafana |
| 指标 | **Prometheus + Grafana** | 后端 QPS / 管线耗时 / source_health |
| 告警 | Alertmanager → **飞书 / 钉钉机器人** |
| APM | 阿里云 ARMS / OpenTelemetry → Tempo |
| 安全 | HTTPS 强制 / JWT（短 token + refresh）/ Sign in with Apple JWT 验签 / Keychain |
| 合规 | ICP 备案 + 隐私政策 + 用户协议 + iOS 隐私清单（PrivacyInfo.xcprivacy）+ 数据出境评估 |
| 代码扫描 | GitHub Code Scanning（CodeQL）+ Dependabot |

---

## 8. 工具链汇总（开发同学一目了然）

| 角色 | 工具 |
|---|---|
| iOS 开发 | Xcode 16 / Cursor / SwiftLint / Swift-format / Sentry-Cocoa / SPM |
| 前端开发 | VS Code / pnpm / Vite / ESLint / Prettier / AntD Pro |
| 后端开发 | VS Code（Pylance）/ uv 或 pip / ruff / pytest / Alembic / Docker |
| 协作 | GitHub（仓库/CI）/ Feishu Doc / Figma（设计） |
| 设计 | Figma / Sketch（mac） |
| API 联调 | Bruno / Postman / curl |

---

## 9. 关键决策摘要（拍板项）

| 决策 | 选择 | 替代方案及原因 |
|---|---|---|
| iOS UI 框架 | **SwiftUI** | UIKit 老但稳，新项目首选 SwiftUI |
| iOS 最低版本 | **iOS 16** | iOS 15 老用户少；SwiftData 17+ 用 Core Data 兜底 |
| iOS 包管理 | **SPM** | CocoaPods 已停更 |
| 客户端登录 | **Sign in with Apple（首发）** | 微信登录后续；规避 App Store 审核 |
| 后端语言 | **Python**（保留） | AI 生态最完善；Go/Node 性能更好但 AI 包不如 Python |
| 主数据库 | **PostgreSQL + pgvector** | MySQL 不带向量；自建 Milvus 太重 |
| CMS 框架 | **React + AntD** | Vue/Element 也行；React 生态更深 |
| 部署形态 | **Docker compose（MVP）→ K8s（规模期）** | 不上 Serverless（管线耗时长） |
| 云厂商 | **阿里云** | 备案+生态+RDS pgvector 支持齐全 |
| 错误追踪 | **Sentry** 三端统一 | Bugly 仅安卓友好 |

---

## 10. 落地节奏

1. **现阶段（M1）**：后端 ✅ 已完成阶段 0–1.6；先继续推阶段 2/3。
2. **阶段 2 中**：起 iOS 工程骨架（`ios/` 目录），打通 [/api/v1/feed](./content_engine/api/routers/brief.py) 真实联调；并行申请域名 ICP 备案。
3. **阶段 3 末**：买阿里云 ECS + RDS + Redis；docker compose 上线；iOS TestFlight 内测。
4. **阶段 4**：CMS 后台启动（React + AntD Pro），打通审稿与质检；接 Sentry / SLS / Prometheus。
5. **流量起来后**：切 K8s + HPA + 多机；引入只读副本与 CDN。

---

*文档结束 · iOS-App 全栈技术选型 v1.0*
