//
//  EventCardView.swift
//  要闻卡片：对齐原型 .card 样式（模块 chip + 标题 + 摘要 + 元信息）。
//

import SwiftUI

struct EventCardView: View {
    let card: EventCard

    private var module: ContentModule { ContentModule.parse(card.module) }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // 元信息行：模块 chip + 信源数 + 时间
            HStack(spacing: 7) {
                Text(module.displayName)
                    .font(.system(size: 10, weight: .bold))
                    .padding(.horizontal, 8)
                    .padding(.vertical, 2)
                    .background(module.tint.opacity(0.14))
                    .foregroundStyle(module.tint)
                    .clipShape(Capsule())

                if card.sourceCount > 1 {
                    Text("\(card.sourceCount) 源")
                        .font(.system(size: 10.5))
                        .foregroundStyle(DSColor.ink3)
                }

                Spacer()

                Text(DateText.relative(card.lastUpdate))
                    .font(.system(size: 10.5, design: .monospaced))
                    .foregroundStyle(DSColor.ink3)
            }

            Text(card.title ?? "（无标题）")
                .font(.system(size: 14, weight: .bold))
                .foregroundStyle(DSColor.ink)
                .lineSpacing(2)
                .multilineTextAlignment(.leading)

            if let summary = card.cardSummary, !summary.isEmpty {
                Text(summary)
                    .font(.system(size: 12))
                    .foregroundStyle(DSColor.ink2)
                    .lineLimit(3)
                    .lineSpacing(2)
            }

            if !card.tags.isEmpty {
                HStack(spacing: 6) {
                    ForEach(card.tags.prefix(3), id: \.self) { tag in
                        Text("#\(tag)")
                            .font(.system(size: 10.5))
                            .foregroundStyle(DSColor.accent)
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 15)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DSColor.card)
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .stroke(DSColor.line, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }
}
