//
//  ReDuApp.swift
//  「热读」iOS 客户端入口。
//
//  MVVM + SwiftUI + NavigationStack；最低 iOS 16。
//

import SwiftUI

@main
struct ReDuApp: App {
    @StateObject private var auth = AuthStore()
    @StateObject private var router = AppRouter()
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(auth)
                .environmentObject(router)
                .preferredColorScheme(.dark) // 对齐原型：深石墨底
                .task { await auth.restore() }
                .task { await listenTransactionUpdates() }
                .task { await bootstrapPushNotifications() }
                .onAppear { appDelegate.router = router }
        }
    }

    /// 监听 StoreKit 交易更新（续订 / Ask to Buy / 退款），逐笔上送后端核销以同步会员态。
    private func listenTransactionUpdates() async {
        let store = StoreService.shared
        let billing = BillingRepository.shared
        for await jws in store.transactionUpdates() {
            if let status = try? await billing.verify(signedTransaction: jws) {
                await auth.applyMembership(status)
            }
        }
    }

    /// 阶段 4.2：申请通知权限 → 注册远端推送（token 通过 AppDelegate 异步回调）。
    /// 未登录时也注册，token 上送会因 401 被丢弃；登录后下次进 App 自动重试。
    @MainActor
    private func bootstrapPushNotifications() async {
        let granted = await PushNotificationCoordinator.shared.requestAuthorization()
        if granted {
            PushNotificationCoordinator.shared.registerForRemoteNotifications()
        }
    }
}
