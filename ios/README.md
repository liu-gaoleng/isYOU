# 热读 iOS 客户端（ReDu）

M2 首期：读当日内容最小闭环（今日简报首页 + 四大模块频道 + 事件详情）。

> 技术栈：Swift 5.x / SwiftUI / MVVM / URLSession async-await / NavigationStack / SPM；最低 iOS 16。详见 [../iOS-App技术选型.md](../iOS-App技术选型.md)。

## 目录结构

```
ios/
├── ReDu.xcodeproj/           # Xcode 工程（file-system synchronized group，objectVersion 77）
└── ReDu/
    ├── App/                  # 入口 ReDuApp + 底部 Tab RootTabView
    ├── Core/
    │   ├── Network/          # APIClient / Endpoint / DTO / ContentRepository
    │   ├── AppRoute.swift    # NavigationStack 路由
    │   └── LoadState.swift   # 通用加载态
    ├── DesignSystem/         # DSColor / 卡片 / 热榜行 / 状态视图 / 格式化（对齐 prototype 视觉）
    ├── Features/
    │   ├── Home/             # 2.2 今日简报首页
    │   ├── Channel/          # 2.3 四大模块频道
    │   └── Detail/           # 事件详情页
    └── Resources/            # Info.plist / Assets.xcassets
```

## 运行前置

1. **安装完整 Xcode 16**（当前机器仅 Command Line Tools，无法编译 SwiftUI）：
   App Store 安装后执行 `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer`。
2. **启动后端**（内容引擎 FastAPI）：
   ```
   cd /Users/bytedance/liu/isYOU
   set -a; . ./.env; set +a
   content_engine/.venv/bin/uvicorn content_engine.api.app:app --host 0.0.0.0 --port 8000
   ```
   客户端默认连 `http://127.0.0.1:8000`（见 `Core/Network/Endpoint.swift` 的 `APIEnv`）。
   - 模拟器可直接用 `127.0.0.1`；**真机**需改成 Mac 的局域网 IP（如 `http://192.168.x.x:8000`）。
   - `Info.plist` 已为 `127.0.0.1` / `localhost` 配置 ATS 明文豁免；上线换 HTTPS 域名后移除。

## 编译运行

```
open ios/ReDu.xcodeproj
```
选择 iPhone 模拟器（iOS 16+），Cmd+R 运行。

## 已对齐的后端接口

| 客户端调用 | 后端接口 | 说明 |
|---|---|---|
| 今日简报 | `GET /api/v1/daily-brief` | 按 importance 倒序 |
| 热榜 TOP10 | `GET /api/v1/ranking` | 全站 + 分模块 |
| 信息流分页 | `GET /api/v1/feed` | cursor 游标加载更多 |
| 事件详情 | `GET /api/v1/event/{id}` | 含信源列表 |

## 首期未做（后续里程碑）

- 付费墙 / 会员态截断（依赖后端账号 API §3.1–3.3）
- 登录（Sign in with Apple §2.6）、我的（收藏/历史 §2.5）
- 分享（§2.7）、推送、IAP（M3）
