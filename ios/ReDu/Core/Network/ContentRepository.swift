//
//  ContentRepository.swift
//  业务数据仓库：把 Endpoint 调用收敛成语义化方法，供 ViewModel 使用。
//

import Foundation

protocol ContentRepositoryProtocol {
    func dailyBrief(date: String?, module: ContentModule?, limit: Int) async throws -> [EventCard]
    func feed(cursor: String?, module: ContentModule?, limit: Int) async throws -> FeedPage
    func ranking(module: ContentModule?, limit: Int) async throws -> [EventCard]
    func eventDetail(id: Int) async throws -> EventDetail
    /// 热点透视：查询状态/结果（会员专属，401/403 由 VM 映射引导态）。
    func insight(eventID: Int) async throws -> EventInsight
    /// 热点透视：触发生成（幂等，进行中重复调用返回现状）。
    func triggerInsight(eventID: Int) async throws -> EventInsight
}

final class ContentRepository: ContentRepositoryProtocol {
    static let shared = ContentRepository()

    private let client: APIClientProtocol

    init(client: APIClientProtocol = APIClient.shared) {
        self.client = client
    }

    func dailyBrief(date: String?, module: ContentModule?, limit: Int) async throws -> [EventCard] {
        try await client.send(
            .dailyBrief(date: date, module: module?.rawValue, limit: limit),
            as: [EventCard].self
        )
    }

    func feed(cursor: String?, module: ContentModule?, limit: Int) async throws -> FeedPage {
        try await client.send(
            .feed(cursor: cursor, module: module?.rawValue, limit: limit),
            as: FeedPage.self
        )
    }

    func ranking(module: ContentModule?, limit: Int) async throws -> [EventCard] {
        try await client.send(
            .ranking(module: module?.rawValue, limit: limit),
            as: [EventCard].self
        )
    }

    func eventDetail(id: Int) async throws -> EventDetail {
        try await client.send(.eventDetail(id: id), as: EventDetail.self)
    }

    func insight(eventID: Int) async throws -> EventInsight {
        try await client.send(.eventInsight(id: eventID), as: EventInsight.self)
    }

    func triggerInsight(eventID: Int) async throws -> EventInsight {
        try await client.send(.triggerInsight(id: eventID), as: EventInsight.self)
    }
}
