//
//  RootTabView.swift
//  底部 Tab 容器：今日 / 频道 / 我的（占位）。
//

import SwiftUI

struct RootTabView: View {
    enum Tab: Hashable {
        case home, channel, profile
    }

    @EnvironmentObject private var router: AppRouter
    @State private var selection: Tab = .home

    var body: some View {
        TabView(selection: $selection) {
            HomeView()
                .tabItem { Label("今日", systemImage: "newspaper") }
                .tag(Tab.home)

            ChannelView()
                .tabItem { Label("频道", systemImage: "square.grid.2x2") }
                .tag(Tab.channel)

            ProfileView()
                .tabItem { Label("我的", systemImage: "person") }
                .tag(Tab.profile)
        }
        .tint(DSColor.accent)
        // 阶段 4.2：APNs 点击通知触发跨 Tab 切换。
        .onChange(of: router.pendingTab) { tab in
            guard let tab else { return }
            selection = tab
            router.pendingTab = nil
        }
    }
}

#Preview {
    RootTabView()
        .environmentObject(AuthStore())
        .environmentObject(AppRouter())
        .preferredColorScheme(.dark)
}
