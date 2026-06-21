//
//  EventDetailView.swift
//  事件详情页（清单 2.4/2.7）：标题 + 标签 + 详情摘要 + 付费深度解读（会员态截断）+
//  多源标注 + 原文链接 + 免责声明；工具栏含收藏与分享。
//

import SwiftUI

struct EventDetailView: View {
    let eventID: Int
    let fallbackTitle: String?

    @EnvironmentObject private var auth: AuthStore
    @StateObject private var vm = EventDetailViewModel()
    @Environment(\.openURL) private var openURL
    @State private var showLogin = false
    @State private var showPaywall = false

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            content
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar { toolbarContent }
        .sheet(isPresented: $showLogin) {
            LoginView().environmentObject(auth)
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView { status in
                Task {
                    await auth.applyMembership(status)
                    await vm.load(id: eventID, isAuthenticated: auth.isAuthenticated)
                }
            }
        }
        .task { await vm.load(id: eventID, isAuthenticated: auth.isAuthenticated) }
    }

    @ToolbarContentBuilder
    private var toolbarContent: some ToolbarContent {
        ToolbarItemGroup(placement: .navigationBarTrailing) {
            if vm.detail != nil {
                favoriteButton
                shareButton
            }
        }
    }

    private var favoriteButton: some View {
        Button {
            if auth.isAuthenticated {
                Task { await vm.toggleFavorite(id: eventID) }
            } else {
                showLogin = true
            }
        } label: {
            Image(systemName: vm.isFavorited ? "bookmark.fill" : "bookmark")
                .foregroundStyle(vm.isFavorited ? DSColor.accent : DSColor.ink2)
        }
        .disabled(vm.favoriteToggling)
    }

    @ViewBuilder
    private var shareButton: some View {
        if let detail = vm.detail {
            ShareLink(item: shareText(detail)) {
                Image(systemName: "square.and.arrow.up")
                    .foregroundStyle(DSColor.ink2)
            }
            .simultaneousGesture(TapGesture().onEnded {
                AnalyticsTracker.shared.track(
                    .share,
                    props: ["event_id": AnyCodable(eventID)]
                )
            })
        }
    }

    /// 分享文案：标题 + 卡片摘要 + 品牌署名。
    private func shareText(_ detail: EventDetail) -> String {
        var parts: [String] = []
        parts.append(detail.title ?? fallbackTitle ?? "热读要闻")
        if let summary = detail.cardSummary, !summary.isEmpty {
            parts.append(summary)
        }
        parts.append("—— 来自「热读」")
        return parts.joined(separator: "\n\n")
    }

    @ViewBuilder
    private var content: some View {
        switch vm.state {
        case .idle, .loading:
            LoadingView()
        case .failed(let msg):
            ErrorStateView(message: msg) { Task { await vm.load(id: eventID, isAuthenticated: auth.isAuthenticated) } }
        case .empty:
            EmptyStateView(message: "内容不存在")
        case .loaded:
            if let detail = vm.detail {
                loaded(detail)
            } else {
                EmptyStateView(message: "内容不存在")
            }
        }
    }

    private func loaded(_ detail: EventDetail) -> some View {
        let module = ContentModule.parse(detail.module)
        return ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                // 模块 + 标签
                HStack(spacing: 7) {
                    Text(module.displayName)
                        .font(.system(size: 11, weight: .bold))
                        .padding(.horizontal, 9)
                        .padding(.vertical, 3)
                        .background(module.tint.opacity(0.14))
                        .foregroundStyle(module.tint)
                        .clipShape(Capsule())
                    Text("\(detail.sourceCount) 个信源")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(DSColor.ink3)
                    Spacer()
                    Text(DateText.relative(detail.lastUpdate))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(DSColor.ink3)
                }

                Text(detail.title ?? fallbackTitle ?? "（无标题）")
                    .font(.system(size: 22, weight: .heavy))
                    .foregroundStyle(DSColor.ink)
                    .lineSpacing(4)

                if let summary = detail.cardSummary, !summary.isEmpty {
                    Text(summary)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(DSColor.ink2)
                        .lineSpacing(3)
                }

                Divider().overlay(DSColor.line)

                if let body = detail.detailSummary, !body.isEmpty {
                    Text(body)
                        .font(.system(size: 15))
                        .foregroundStyle(DSColor.ink)
                        .lineSpacing(6)
                }

                if let deep = detail.deepContent {
                    deepContentSection(deep)
                }

                if !detail.tags.isEmpty {
                    HStack(spacing: 8) {
                        ForEach(detail.tags, id: \.self) { tag in
                            Text("#\(tag)")
                                .font(.system(size: 12))
                                .foregroundStyle(DSColor.accent)
                        }
                    }
                }

                sourcesSection(detail.sources)

                if module == .finance {
                    disclaimer
                }
            }
            .padding(20)
        }
    }

    // MARK: - 付费深度解读（会员态截断 / 付费墙）

    @ViewBuilder
    private func deepContentSection(_ deep: DeepContent) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 6) {
                Image(systemName: "sparkles")
                    .font(.system(size: 13))
                    .foregroundStyle(DSColor.accent)
                Text("深度解读")
                    .font(.system(size: 14, weight: .heavy))
                    .foregroundStyle(DSColor.accent)
            }

            if deep.isLocked {
                lockedContent(deep)
            } else if let content = deep.content, !content.isEmpty {
                Text(content)
                    .font(.system(size: 15))
                    .foregroundStyle(DSColor.ink)
                    .lineSpacing(6)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DSColor.card2)
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(DSColor.accentSoft, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private func lockedContent(_ deep: DeepContent) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            if let preview = deep.preview, !preview.isEmpty {
                Text(preview)
                    .font(.system(size: 15))
                    .foregroundStyle(DSColor.ink2)
                    .lineSpacing(6)
                    .overlay(alignment: .bottom) {
                        // 底部渐隐，暗示内容被截断。
                        LinearGradient(
                            colors: [DSColor.card2.opacity(0), DSColor.card2],
                            startPoint: .top, endPoint: .bottom
                        )
                        .frame(height: 36)
                    }
            }

            VStack(spacing: 8) {
                Image(systemName: "lock.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(DSColor.accent)
                Text(deep.paywall?.cta ?? "开通会员，解锁完整深度解读")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(DSColor.ink2)
                    .multilineTextAlignment(.center)
                Button {
                    paywallAction()
                } label: {
                    Text(auth.isAuthenticated ? "开通会员" : "登录后开通")
                        .font(.system(size: 14, weight: .bold))
                        .padding(.horizontal, 28)
                        .padding(.vertical, 10)
                        .background(DSColor.accent)
                        .foregroundStyle(DSColor.bg)
                        .clipShape(Capsule())
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.top, 4)
        }
    }

    private func paywallAction() {
        if auth.isAuthenticated {
            showPaywall = true
        } else {
            showLogin = true
        }
    }

    @ViewBuilder
    private func sourcesSection(_ sources: [EventSourceItem]) -> some View {
        if !sources.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                Text("信源出处")
                    .font(.system(size: 13, weight: .heavy))
                    .foregroundStyle(DSColor.ink2)
                ForEach(sources, id: \.url) { src in
                    Button {
                        if let url = URL(string: src.url) { openURL(url) }
                    } label: {
                        HStack(spacing: 8) {
                            Text(src.level)
                                .font(.system(size: 10, weight: .bold, design: .monospaced))
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(DSColor.accentSoft)
                                .foregroundStyle(DSColor.accent)
                                .clipShape(Capsule())
                            Text(src.name)
                                .font(.system(size: 13))
                                .foregroundStyle(DSColor.ink)
                            Spacer()
                            Image(systemName: "arrow.up.right.square")
                                .font(.system(size: 12))
                                .foregroundStyle(DSColor.ink3)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(src.url.isEmpty)
                }
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DSColor.card)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(DSColor.line, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
    }

    private var disclaimer: some View {
        Text("免责声明：本内容由信源聚合与摘要生成，不构成任何投资建议。市场有风险，决策需谨慎。")
            .font(.system(size: 11))
            .foregroundStyle(DSColor.ink3)
            .lineSpacing(3)
            .padding(.top, 4)
    }
}
