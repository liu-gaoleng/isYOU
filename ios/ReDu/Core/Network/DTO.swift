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
    let deepContent: DeepContent?
    let firstSeen: Date
    let lastUpdate: Date

    enum CodingKeys: String, CodingKey {
        case id, module, title, tags, importance, hotness, sources
        case cardSummary = "card_summary"
        case detailSummary = "detail_summary"
        case sourceCount = "source_count"
        case deepContent = "deep_content"
        case firstSeen = "first_seen"
        case lastUpdate = "last_update"
    }
}

/// 付费深度解读，对齐 schemas.DeepContent。
/// 会员：is_locked=false + 完整 content；非会员：is_locked=true + 截断 preview + paywall。
struct DeepContent: Codable, Hashable {
    let isLocked: Bool
    let content: String?
    let preview: String?
    let paywall: Paywall?

    struct Paywall: Codable, Hashable {
        let requiredTier: String?
        let cta: String?

        enum CodingKeys: String, CodingKey {
            case requiredTier = "required_tier"
            case cta
        }
    }

    enum CodingKeys: String, CodingKey {
        case isLocked = "is_locked"
        case content, preview, paywall
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

// MARK: - 账号 / 会员（对齐 schemas 阶段 3.1）

/// 当前登录用户信息，对齐 schemas.UserProfile。
struct UserProfile: Codable, Hashable {
    let id: Int
    let email: String?
    let displayName: String?
    let createdVia: String
    let memberTier: String
    let isMember: Bool
    let memberExpireAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, email
        case displayName = "display_name"
        case createdVia = "created_via"
        case memberTier = "member_tier"
        case isMember = "is_member"
        case memberExpireAt = "member_expire_at"
    }
}

/// 登录成功响应，对齐 schemas.LoginResponse。
struct LoginResponse: Codable {
    let accessToken: String
    let tokenType: String
    let expiresIn: Int
    let user: UserProfile

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case tokenType = "token_type"
        case expiresIn = "expires_in"
        case user
    }
}

// MARK: - 会员订阅 / IAP（对齐 schemas 阶段 3.2）

/// 订阅档位，对齐 schemas.PlanItem。
struct PlanItem: Codable, Identifiable, Hashable {
    let plan: String          // monthly / quarterly / yearly
    let productId: String
    let periodDays: Int

    var id: String { productId }

    enum CodingKeys: String, CodingKey {
        case plan
        case productId = "product_id"
        case periodDays = "period_days"
    }
}

/// 会员态，对齐 schemas.MembershipStatus。
struct MembershipStatus: Codable, Hashable {
    let isMember: Bool
    let memberTier: String
    let memberExpireAt: Date?
    let plan: String?
    let autoRenew: Bool
    let subscriptionStatus: String?   // active / expired / refunded

    enum CodingKeys: String, CodingKey {
        case isMember = "is_member"
        case memberTier = "member_tier"
        case memberExpireAt = "member_expire_at"
        case plan
        case autoRenew = "auto_renew"
        case subscriptionStatus = "subscription_status"
    }
}

// MARK: - 收藏 / 历史 / 设置（对齐 schemas 阶段 3.4）

/// 收藏状态切换结果，对齐 schemas.FavoriteState。
struct FavoriteState: Codable {
    let eventId: Int
    let isFavorited: Bool

    enum CodingKeys: String, CodingKey {
        case eventId = "event_id"
        case isFavorited = "is_favorited"
    }
}

/// 收藏列表项 = 卡片 + 收藏时间，对齐 schemas.FavoriteCard。
struct FavoriteCard: Codable, Identifiable, Hashable {
    let id: Int
    let module: String
    let title: String?
    let cardSummary: String?
    let importance: Double
    let hotness: Double
    let sourceCount: Int
    let tags: [String]
    let lastUpdate: Date
    let favoritedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, module, title, tags, importance, hotness
        case cardSummary = "card_summary"
        case sourceCount = "source_count"
        case lastUpdate = "last_update"
        case favoritedAt = "favorited_at"
    }

    /// 转成通用卡片，复用列表/卡片视图。
    var card: EventCard {
        EventCard(id: id, module: module, title: title, cardSummary: cardSummary,
                  importance: importance, hotness: hotness, sourceCount: sourceCount,
                  tags: tags, lastUpdate: lastUpdate)
    }
}

/// 阅读历史项 = 卡片 + 浏览时间，对齐 schemas.HistoryCard。
struct HistoryCard: Codable, Identifiable, Hashable {
    let id: Int
    let module: String
    let title: String?
    let cardSummary: String?
    let importance: Double
    let hotness: Double
    let sourceCount: Int
    let tags: [String]
    let lastUpdate: Date
    let viewedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, module, title, tags, importance, hotness
        case cardSummary = "card_summary"
        case sourceCount = "source_count"
        case lastUpdate = "last_update"
        case viewedAt = "viewed_at"
    }

    var card: EventCard {
        EventCard(id: id, module: module, title: title, cardSummary: cardSummary,
                  importance: importance, hotness: hotness, sourceCount: sourceCount,
                  tags: tags, lastUpdate: lastUpdate)
    }
}

/// 推送设置，对齐 schemas.PushSettings。
struct PushSettings: Codable, Equatable {
    var dailyPush: Bool
    var pushTime: String
    var breakingPush: Bool

    enum CodingKeys: String, CodingKey {
        case dailyPush = "daily_push"
        case pushTime = "push_time"
        case breakingPush = "breaking_push"
    }

    static let `default` = PushSettings(dailyPush: true, pushTime: "08:00", breakingPush: false)
}

// MARK: - 设备 token 注册（对齐 schemas 阶段 4.2）

/// 设备 token 注册结果，对齐 schemas.DeviceTokenInfo。
struct DeviceTokenInfo: Codable, Equatable {
    let token: String
    let environment: String
    let bundleId: String?
    let isActive: Bool
    let lastSeenAt: Date?

    enum CodingKeys: String, CodingKey {
        case token, environment
        case bundleId = "bundle_id"
        case isActive = "is_active"
        case lastSeenAt = "last_seen_at"
    }
}
