//
//  ProfileView.swift
//  「我的」页（清单 2.5）：登录态展示 + 收藏 / 阅读历史 / 推送设置 / 登出。
//  未登录时引导登录。
//

import SwiftUI

struct ProfileView: View {
    @EnvironmentObject private var auth: AuthStore
    @StateObject private var vm = ProfileViewModel()
    @State private var path: [AppRoute] = []
    @State private var showLogin = false

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                DSColor.bg.ignoresSafeArea()
                if auth.isAuthenticated {
                    authenticated
                } else {
                    guest
                }
            }
            .navigationTitle("我的")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: AppRoute.self) { route in
                switch route {
                case let .eventDetail(id, title):
                    EventDetailView(eventID: id, fallbackTitle: title)
                case .favorites:
                    FavoritesListView(vm: vm)
                case .history:
                    HistoryListView(vm: vm)
                case .membership:
                    MembershipView()
                }
            }
        }
        .sheet(isPresented: $showLogin) {
            LoginView().environmentObject(auth)
        }
    }

    // MARK: 未登录

    private var guest: some View {
        VStack(spacing: 16) {
            Image(systemName: "person.crop.circle")
                .font(.system(size: 56))
                .foregroundStyle(DSColor.ink3)
            Text("登录后同步收藏与会员权益")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(DSColor.ink2)
            Button { showLogin = true } label: {
                Text("登录 / 注册")
                    .font(.system(size: 15, weight: .bold))
                    .padding(.horizontal, 36)
                    .padding(.vertical, 12)
                    .background(DSColor.accent)
                    .foregroundStyle(DSColor.bg)
                    .clipShape(Capsule())
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 已登录

    private var authenticated: some View {
        ScrollView {
            VStack(spacing: 16) {
                userCard
                entries
                settingsCard
                logoutButton
            }
            .padding(16)
        }
        .task {
            await vm.loadSettings()
        }
    }

    private var userCard: some View {
        HStack(spacing: 14) {
            Image(systemName: "person.crop.circle.fill")
                .font(.system(size: 46))
                .foregroundStyle(DSColor.accent)
            VStack(alignment: .leading, spacing: 6) {
                Text(auth.user?.displayName ?? auth.user?.email ?? "热读用户")
                    .font(.system(size: 17, weight: .heavy))
                    .foregroundStyle(DSColor.ink)
                memberBadge
            }
            Spacer()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DSColor.card)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    @ViewBuilder
    private var memberBadge: some View {
        if auth.isMember {
            Text("会员")
                .font(.system(size: 11, weight: .bold))
                .padding(.horizontal, 10)
                .padding(.vertical, 3)
                .background(DSColor.accentSoft)
                .foregroundStyle(DSColor.accent)
                .clipShape(Capsule())
        } else {
            Text("普通用户")
                .font(.system(size: 12))
                .foregroundStyle(DSColor.ink3)
        }
    }

    private var entries: some View {
        VStack(spacing: 0) {
            NavigationLink(value: AppRoute.membership) {
                entryRow(icon: "crown.fill", title: "我的会员")
            }
            .buttonStyle(.plain)
            Divider().overlay(DSColor.line)
            NavigationLink(value: AppRoute.favorites) {
                entryRow(icon: "bookmark.fill", title: "我的收藏")
            }
            .buttonStyle(.plain)
            Divider().overlay(DSColor.line)
            NavigationLink(value: AppRoute.history) {
                entryRow(icon: "clock.arrow.circlepath", title: "阅读历史")
            }
            .buttonStyle(.plain)
        }
        .background(DSColor.card)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func entryRow(icon: String, title: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: icon)
                .font(.system(size: 16))
                .foregroundStyle(DSColor.accent)
                .frame(width: 22)
            Text(title)
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(DSColor.ink)
            Spacer()
            Image(systemName: "chevron.right")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(DSColor.ink3)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 15)
    }

    private var settingsCard: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("推送设置")
                .font(.system(size: 13, weight: .heavy))
                .foregroundStyle(DSColor.ink2)
                .padding(.horizontal, 16)
                .padding(.top, 14)
                .padding(.bottom, 6)

            settingsToggle(
                title: "每日早报推送",
                isOn: Binding(
                    get: { vm.settings.dailyPush },
                    set: { v in Task { await vm.updateSettings(dailyPush: v) } }
                )
            )
            Divider().overlay(DSColor.line)
            pushTimeRow
            Divider().overlay(DSColor.line)
            settingsToggle(
                title: "重大事件即时推送",
                isOn: Binding(
                    get: { vm.settings.breakingPush },
                    set: { v in Task { await vm.updateSettings(breakingPush: v) } }
                )
            )
        }
        .padding(.bottom, 6)
        .background(DSColor.card)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 14))
        .disabled(!vm.settingsLoaded)
    }

    private func settingsToggle(title: String, isOn: Binding<Bool>) -> some View {
        Toggle(isOn: isOn) {
            Text(title)
                .font(.system(size: 15))
                .foregroundStyle(DSColor.ink)
        }
        .tint(DSColor.accent)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
    }

    private var pushTimeRow: some View {
        HStack {
            Text("早报推送时间")
                .font(.system(size: 15))
                .foregroundStyle(DSColor.ink)
            Spacer()
            DatePicker(
                "",
                selection: Binding(
                    get: { PushTime.date(from: vm.settings.pushTime) },
                    set: { d in Task { await vm.updateSettings(pushTime: PushTime.string(from: d)) } }
                ),
                displayedComponents: .hourAndMinute
            )
            .labelsHidden()
            .disabled(!vm.settings.dailyPush)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
    }

    private var logoutButton: some View {
        Button(role: .destructive) {
            auth.logout()
            path = []
        } label: {
            Text("退出登录")
                .font(.system(size: 15, weight: .bold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 13)
                .background(DSColor.card)
                .foregroundStyle(DSColor.up)
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
                .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .padding(.top, 8)
    }
}

/// 推送时间字符串 "HH:mm" 与 Date 的互转（仅取时分）。
enum PushTime {
    private static let formatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "HH:mm"
        return f
    }()

    static func date(from raw: String) -> Date {
        formatter.date(from: raw) ?? (formatter.date(from: "08:00") ?? Date())
    }

    static func string(from date: Date) -> String {
        formatter.string(from: date)
    }
}

// MARK: - 收藏列表

struct FavoritesListView: View {
    @ObservedObject var vm: ProfileViewModel

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            switch vm.favoritesState {
            case .idle, .loading:
                LoadingView()
            case .failed(let msg):
                ErrorStateView(message: msg) { Task { await vm.loadFavorites() } }
            case .empty:
                EmptyStateView(message: "还没有收藏的内容")
            case .loaded:
                list
            }
        }
        .navigationTitle("我的收藏")
        .navigationBarTitleDisplayMode(.inline)
        .task { await vm.loadFavorites() }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                ForEach(vm.favorites) { item in
                    NavigationLink(value: AppRoute.eventDetail(id: item.id, title: item.title)) {
                        EventCardView(card: item.card)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(16)
        }
        .refreshable { await vm.loadFavorites() }
    }
}

// MARK: - 阅读历史

struct HistoryListView: View {
    @ObservedObject var vm: ProfileViewModel

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            switch vm.historyState {
            case .idle, .loading:
                LoadingView()
            case .failed(let msg):
                ErrorStateView(message: msg) { Task { await vm.loadHistory() } }
            case .empty:
                EmptyStateView(message: "还没有阅读记录")
            case .loaded:
                list
            }
        }
        .navigationTitle("阅读历史")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if case .loaded = vm.historyState {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("清空") { Task { await vm.clearHistory() } }
                        .foregroundStyle(DSColor.ink3)
                }
            }
        }
        .task { await vm.loadHistory() }
    }

    private var list: some View {
        ScrollView {
            LazyVStack(spacing: 10) {
                ForEach(vm.history) { item in
                    NavigationLink(value: AppRoute.eventDetail(id: item.id, title: item.title)) {
                        EventCardView(card: item.card)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(16)
        }
        .refreshable { await vm.loadHistory() }
    }
}
