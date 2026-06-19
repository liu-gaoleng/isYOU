//
//  Fakes.swift
//  测试替身：内存假仓库，避免单测触网。
//

import Foundation
@testable import ReDu

/// 可编排的假内容仓库：记录调用参数 + 返回预置数据，支持错误注入。
final class FakeContentRepository: ContentRepositoryProtocol {
    var briefByLimit: (Int) -> [EventCard] = { _ in [] }
    var rankingResult: [EventCard] = []
    var feedResult: FeedPage = FeedPage(items: [], nextCursor: nil)
    var detailResult: EventDetail?
    var briefError: Error?

    private(set) var requestedBriefLimits: [Int] = []

    func dailyBrief(date: String?, module: ContentModule?, limit: Int) async throws -> [EventCard] {
        requestedBriefLimits.append(limit)
        if let briefError { throw briefError }
        return briefByLimit(limit)
    }

    func feed(cursor: String?, module: ContentModule?, limit: Int) async throws -> FeedPage {
        feedResult
    }

    func ranking(module: ContentModule?, limit: Int) async throws -> [EventCard] {
        rankingResult
    }

    func eventDetail(id: Int) async throws -> EventDetail {
        guard let detailResult else { throw APIError.invalidResponse }
        return detailResult
    }
}

/// 构造卡片的便捷工厂。
func makeCard(
    id: Int,
    module: String,
    importance: Double = 1.0,
    title: String? = nil
) -> EventCard {
    EventCard(
        id: id,
        module: module,
        title: title ?? "标题\(id)",
        cardSummary: "摘要\(id)",
        importance: importance,
        hotness: 0,
        sourceCount: 1,
        tags: [],
        lastUpdate: Date(timeIntervalSince1970: 1_700_000_000)
    )
}
