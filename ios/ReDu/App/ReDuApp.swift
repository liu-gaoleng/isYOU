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

    var body: some Scene {
        WindowGroup {
            RootTabView()
                .environmentObject(auth)
                .preferredColorScheme(.dark) // 对齐原型：深石墨底
                .task { await auth.restore() }
                .task { await listenTransactionUpdates() }
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
}
