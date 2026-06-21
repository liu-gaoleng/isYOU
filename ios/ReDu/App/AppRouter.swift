//
//  AppRouter.swift
//  跨 Tab / deep link 路由协调器（阶段 4.2：APNs 点击直达详情）。
//
//  设计原则：
//  - 不替换 Tab 内已有的 NavigationStack path（避免大改），改为发"待跳路由"
//    信号，由 Home 等容器在 onReceive 时把它压入自己的 path。
//  - 触发入口：AppDelegate 解析 UNNotificationContent.userInfo 后调
//    ``router.route(to:)``；其它任意 deep link 入口可复用。
//

import Combine
import SwiftUI

@MainActor
final class AppRouter: ObservableObject {
    /// 期望切换到的 Tab；nil 表示"维持当前 Tab"。
    @Published var pendingTab: RootTabView.Tab? = nil
    /// 期望弹出的路由；消费方读完应主动置 nil 防止重复触发。
    @Published var pendingRoute: AppRoute? = nil

    /// 触发跳转：可选切换 Tab + 压入路由。
    func route(to route: AppRoute, tab: RootTabView.Tab? = .home) {
        pendingTab = tab
        pendingRoute = route
    }

    /// 消费方调用后清零，避免下次重渲染重复 push。
    func clearPendingRoute() {
        pendingRoute = nil
    }
}
