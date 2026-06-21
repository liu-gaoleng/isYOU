//
//  HomeView.swift
//  今日简报首页（清单 2.2）：品牌行 + slogan + 日期 + 今日热榜 TOP10 + 聚合卡片流。
//  下拉刷新；卡片可点开详情。
//

import SwiftUI

struct HomeView: View {
    @StateObject private var vm = HomeViewModel()
    @EnvironmentObject private var router: AppRouter
    @State private var path: [AppRoute] = []

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                DSColor.bg.ignoresSafeArea()
                content
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("热读")
                        .font(.system(size: 20, weight: .heavy))
                        .foregroundStyle(DSColor.accent)
                        .tracking(2)
                }
            }
            .navigationDestination(for: AppRoute.self) { route in
                switch route {
                case let .eventDetail(id, title):
                    EventDetailView(eventID: id, fallbackTitle: title)
                default:
                    EmptyView()
                }
            }
            .task { await vm.load() }
            // 阶段 4.2：APNs 点击直达——router 发出的待跳路由压入本 Tab 的 path。
            .onChange(of: router.pendingRoute) { route in
                guard let route else { return }
                path.append(route)
                router.clearPendingRoute()
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.state {
        case .idle, .loading:
            LoadingView()
        case .failed(let msg):
            ErrorStateView(message: msg) { Task { await vm.load() } }
        case .empty:
            EmptyStateView()
        case .loaded:
            loadedList
        }
    }

    private var loadedList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                header

                if !vm.ranking.isEmpty {
                    sectionTitle("今日热榜", accent: "TOP \(vm.ranking.count)")
                    VStack(spacing: 0) {
                        ForEach(Array(vm.ranking.enumerated()), id: \.element.id) { idx, card in
                            NavigationLink(value: AppRoute.eventDetail(id: card.id, title: card.title)) {
                                RankRowView(rank: idx + 1, card: card)
                            }
                            .buttonStyle(.plain)
                            if idx < vm.ranking.count - 1 {
                                Divider().overlay(DSColor.line)
                            }
                        }
                    }
                    .padding(.horizontal, 4)
                }

                sectionTitle("今日聚合", accent: "\(vm.totalCount) 条")
                ForEach(vm.sections) { section in
                    moduleHeader(section.module, count: section.cards.count)
                    ForEach(section.cards) { card in
                        NavigationLink(value: AppRoute.eventDetail(id: card.id, title: card.title)) {
                            EventCardView(card: card)
                        }
                        .buttonStyle(.plain)
                        .padding(.bottom, 10)
                    }
                }

                loadMoreFooter
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 24)
        }
        .refreshable { await vm.refresh() }
    }

    /// 模块分区小标题：左侧色条 + 模块名 + 该区条数。
    private func moduleHeader(_ module: ContentModule, count: Int) -> some View {
        HStack(spacing: 8) {
            RoundedRectangle(cornerRadius: 1.5)
                .fill(module.tint)
                .frame(width: 3, height: 14)
            Text(module.displayName)
                .font(.system(size: 14, weight: .heavy))
                .foregroundStyle(DSColor.ink)
            Text("\(count)")
                .font(.system(size: 10.5, design: .monospaced))
                .foregroundStyle(DSColor.ink3)
            Spacer()
        }
        .padding(.top, 16)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private var loadMoreFooter: some View {
        if vm.isLoadingMore {
            HStack {
                Spacer()
                ProgressView().tint(DSColor.accent)
                Spacer()
            }
            .padding(.vertical, 16)
        } else if vm.hasMore {
            Button {
                Task { await vm.loadMore() }
            } label: {
                Text("加载更多 ∨")
                    .font(.system(size: 12.5, weight: .bold))
                    .foregroundStyle(DSColor.ink3)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .overlay(
                        RoundedRectangle(cornerRadius: 10).stroke(DSColor.line, lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
            .padding(.top, 8)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(vm.dateTitle)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(DSColor.ink3)
                .tracking(1)
                .padding(.top, 4)

            // slogan 条
            HStack(spacing: 0) {
                Rectangle().fill(DSColor.accent).frame(width: 3)
                VStack(alignment: .leading, spacing: 6) {
                    Text("TODAY")
                        .font(.system(size: 10, weight: .heavy, design: .monospaced))
                        .foregroundStyle(DSColor.accent)
                        .tracking(2)
                    Text(vm.slogan)
                        .font(.system(size: 13.5, weight: .bold))
                        .foregroundStyle(DSColor.ink)
                        .lineSpacing(2)
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 13)
                Spacer(minLength: 0)
            }
            .background(DSColor.card)
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(DSColor.line, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .padding(.top, 14)
        }
    }

    private func sectionTitle(_ title: String, accent: String) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(DSColor.ink)
            Spacer()
            Text(accent)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(DSColor.ink3)
        }
        .padding(.top, 22)
        .padding(.bottom, 12)
    }
}
