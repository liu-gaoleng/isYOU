//
//  RootTabView.swift
//  底部 Tab 容器：今日 / 频道 / 我的（占位）。
//

import SwiftUI

struct RootTabView: View {
    enum Tab: Hashable {
        case home, channel, profile
    }

    @State private var selection: Tab = .home

    var body: some View {
        TabView(selection: $selection) {
            HomeView()
                .tabItem { Label("今日", systemImage: "newspaper") }
                .tag(Tab.home)

            ChannelView()
                .tabItem { Label("频道", systemImage: "square.grid.2x2") }
                .tag(Tab.channel)

            ProfilePlaceholderView()
                .tabItem { Label("我的", systemImage: "person") }
                .tag(Tab.profile)
        }
        .tint(DSColor.accent)
    }
}

/// 「我的」占位页：M2 首期仅做读内容闭环，账号/收藏后续接入。
struct ProfilePlaceholderView: View {
    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Image(systemName: "person.crop.circle")
                    .font(.system(size: 48))
                    .foregroundStyle(DSColor.ink3)
                Text("登录 / 收藏 / 历史")
                    .font(.headline)
                    .foregroundStyle(DSColor.ink2)
                Text("即将上线")
                    .font(.caption)
                    .foregroundStyle(DSColor.ink3)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(DSColor.bg)
            .navigationTitle("我的")
        }
    }
}

#Preview {
    RootTabView()
        .preferredColorScheme(.dark)
}
