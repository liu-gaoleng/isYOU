//
//  EventDetailView.swift
//  事件详情页（首期基础版）：标题 + 标签 + 详情摘要 + 多源标注 + 原文链接 + 免责声明。
//  付费墙/会员态待后端账号 API 就绪后接入（清单 2.4 后续）。
//

import SwiftUI

struct EventDetailView: View {
    let eventID: Int
    let fallbackTitle: String?

    @StateObject private var vm = EventDetailViewModel()
    @Environment(\.openURL) private var openURL

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            content
        }
        .navigationBarTitleDisplayMode(.inline)
        .task { await vm.load(id: eventID) }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.state {
        case .idle, .loading:
            LoadingView()
        case .failed(let msg):
            ErrorStateView(message: msg) { Task { await vm.load(id: eventID) } }
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
