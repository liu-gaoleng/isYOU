//
//  RankRowView.swift
//  热榜行：对齐原型 .rk（序号 + 标题 + 热度条 + 热度值）。
//

import SwiftUI

struct RankRowView: View {
    let rank: Int
    let card: EventCard

    private var isTop3: Bool { rank <= 3 }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Text("\(rank)")
                .font(.system(size: 15, weight: .bold, design: .monospaced))
                .foregroundStyle(isTop3 ? DSColor.accent : DSColor.ink3)
                .frame(width: 20)

            VStack(alignment: .leading, spacing: 6) {
                Text(card.title ?? "（无标题）")
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(DSColor.ink)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                // 热度条
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(DSColor.line)
                        Capsule()
                            .fill(
                                LinearGradient(
                                    colors: [DSColor.accent, DSColor.up],
                                    startPoint: .leading, endPoint: .trailing
                                )
                            )
                            .frame(width: geo.size.width * CGFloat(max(0.05, min(1.0, card.hotness))))
                    }
                }
                .frame(height: 3)

                HStack(spacing: 8) {
                    Text("热度 \(HotText.percent(card.hotness))")
                        .font(.system(size: 10.5, design: .monospaced))
                        .foregroundStyle(DSColor.up)
                    if let firstTag = card.tags.first {
                        Text("#\(firstTag)")
                            .font(.system(size: 10.5, design: .monospaced))
                            .foregroundStyle(DSColor.accent)
                    }
                }
            }
        }
        .padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .contentShape(Rectangle())
    }
}
