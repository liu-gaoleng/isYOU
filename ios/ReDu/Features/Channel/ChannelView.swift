//
//  ChannelView.swift
//  四大模块频道（清单 2.3）：顶部 segment 切换 + 各频道 TOP10 + 要闻卡流 + 加载更多。
//

import SwiftUI

struct ChannelView: View {
    @StateObject private var vm = ChannelViewModel()
    @State private var path: [AppRoute] = []

    var body: some View {
        NavigationStack(path: $path) {
            ZStack {
                DSColor.bg.ignoresSafeArea()
                VStack(spacing: 0) {
                    segments
                    content
                }
            }
            .navigationTitle("频道")
            .navigationBarTitleDisplayMode(.inline)
            .navigationDestination(for: AppRoute.self) { route in
                switch route {
                case let .eventDetail(id, title):
                    EventDetailView(eventID: id, fallbackTitle: title)
                }
            }
            .task { if vm.state == .idle { await vm.load() } }
        }
    }

    private var segments: some View {
        HStack(spacing: 20) {
            ForEach(ContentModule.allCases) { module in
                let isOn = module == vm.selected
                Button {
                    Task { await vm.switchTo(module) }
                } label: {
                    VStack(spacing: 8) {
                        Text(module.displayName)
                            .font(.system(size: 14, weight: isOn ? .heavy : .semibold))
                            .foregroundStyle(isOn ? DSColor.ink : DSColor.ink3)
                        Rectangle()
                            .fill(isOn ? DSColor.accent : .clear)
                            .frame(height: 2.5)
                            .clipShape(Capsule())
                    }
                }
                .buttonStyle(.plain)
            }
            Spacer()
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
        .overlay(alignment: .bottom) {
            Rectangle().fill(DSColor.line).frame(height: 1)
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
            EmptyStateView(message: "该频道暂无内容")
        case .loaded:
            loadedList
        }
    }

    private var loadedList: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                if !vm.ranking.isEmpty {
                    sectionTitle("\(vm.selected.displayName)热榜", accent: "TOP \(vm.ranking.count)")
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

                sectionTitle("要闻", accent: "")
                ForEach(vm.cards) { card in
                    NavigationLink(value: AppRoute.eventDetail(id: card.id, title: card.title)) {
                        EventCardView(card: card)
                    }
                    .buttonStyle(.plain)
                    .padding(.bottom, 10)
                    .task { await vm.loadMoreIfNeeded(current: card) }
                }

                if vm.isLoadingMore {
                    HStack {
                        Spacer()
                        ProgressView().tint(DSColor.accent)
                        Spacer()
                    }
                    .padding(.vertical, 16)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 4)
            .padding(.bottom, 24)
        }
        .refreshable { await vm.refresh() }
    }

    private func sectionTitle(_ title: String, accent: String) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 16, weight: .heavy))
                .foregroundStyle(DSColor.ink)
            Spacer()
            if !accent.isEmpty {
                Text(accent)
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(DSColor.ink3)
            }
        }
        .padding(.top, 18)
        .padding(.bottom, 12)
    }
}
