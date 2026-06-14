//
//  DTO.swift
//  与后端 content_engine/api/schemas.py 一一对齐的传输模型（Codable）。
//

import Foundation

/// 卡片流单卡，对齐 schemas.EventCard。
struct EventCard: Codable, Identifiable, Hashable {
    let id: Int
    let module: String
    let title: String?
    let cardSummary: String?
    let importance: Double
    let hotness: Double
    let sourceCount: Int
    let tags: [String]
    let lastUpdate: Date

    enum CodingKeys: String, CodingKey {
        case id, module, title, tags
        case cardSummary = "card_summary"
        case importance, hotness
        case sourceCount = "source_count"
        case lastUpdate = "last_update"
    }
}

/// 信源条目，对齐 schemas.EventSourceItem。
struct EventSourceItem: Codable, Hashable {
    let name: String
    let level: String
    let url: String
}

/// 事件详情，对齐 schemas.EventDetail。
struct EventDetail: Codable, Identifiable, Hashable {
    let id: Int
    let module: String
    let title: String?
    let cardSummary: String?
    let detailSummary: String?
    let tags: [String]
    let importance: Double
    let hotness: Double
    let sourceCount: Int
    let sources: [EventSourceItem]
    let firstSeen: Date
    let lastUpdate: Date

    enum CodingKeys: String, CodingKey {
        case id, module, title, tags, importance, hotness, sources
        case cardSummary = "card_summary"
        case detailSummary = "detail_summary"
        case sourceCount = "source_count"
        case firstSeen = "first_seen"
        case lastUpdate = "last_update"
    }
}

/// 信息流分页响应，对齐 schemas.FeedPage。
struct FeedPage: Codable {
    let items: [EventCard]
    let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items
        case nextCursor = "next_cursor"
    }
}
