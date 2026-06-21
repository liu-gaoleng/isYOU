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

// MARK: - 4.1 IAP 测试替身

/// 可编排的假账号仓库，支持 currentUser 注入；其余方法默认抛错。
final class FakeAuthRepository: AuthRepositoryProtocol {
    var currentUserResult: UserProfile?
    var currentUserError: Error?

    func loginWithApple(identityToken: String, displayName: String?) async throws -> LoginResponse {
        throw APIError.invalidResponse
    }
    func devLogin(appleUserID: String, email: String?, displayName: String?, asMember: Bool) async throws -> LoginResponse {
        throw APIError.invalidResponse
    }
    func currentUser() async throws -> UserProfile {
        if let e = currentUserError { throw e }
        guard let u = currentUserResult else { throw APIError.invalidResponse }
        return u
    }
    func addFavorite(eventID: Int) async throws -> FavoriteState { throw APIError.invalidResponse }
    func removeFavorite(eventID: Int) async throws -> FavoriteState { throw APIError.invalidResponse }
    func listFavorites() async throws -> [FavoriteCard] { [] }
    func recordHistory(eventID: Int) async throws {}
    func listHistory() async throws -> [HistoryCard] { [] }
    func clearHistory() async throws {}
    func getSettings() async throws -> PushSettings { .default }
    func updateSettings(dailyPush: Bool?, pushTime: String?, breakingPush: Bool?) async throws -> PushSettings { .default }
}

/// 可编排的假 Billing 仓库。
final class FakeBillingRepository: BillingRepositoryProtocol {
    var plansResult: [PlanItem] = []
    var verifyResult: MembershipStatus?
    var restoreResult: MembershipStatus?
    var verifyError: Error?

    private(set) var verifiedJWS: [String] = []
    private(set) var restoredJWS: [[String]] = []

    func plans() async throws -> [PlanItem] { plansResult }

    func verify(signedTransaction: String) async throws -> MembershipStatus {
        verifiedJWS.append(signedTransaction)
        if let e = verifyError { throw e }
        return verifyResult ?? Self.freeStatus
    }

    func restore(signedTransactions: [String]) async throws -> MembershipStatus {
        restoredJWS.append(signedTransactions)
        return restoreResult ?? Self.freeStatus
    }

    func membership() async throws -> MembershipStatus { Self.freeStatus }

    static let freeStatus = MembershipStatus(
        isMember: false, memberTier: "free", memberExpireAt: nil,
        plan: nil, autoRenew: false, subscriptionStatus: nil
    )
}

/// 可编排的假 StoreKit 服务。
final class FakeStoreService: StoreServiceProtocol {
    var products: [StoreProduct] = []
    var purchaseOutcome: PurchaseOutcome = .userCancelled
    var purchaseError: Error?
    var currentJWS: [String] = []

    private(set) var purchasedIDs: [String] = []

    func loadProducts(ids: [String]) async throws -> [StoreProduct] {
        products.filter { ids.contains($0.id) }
    }

    func purchase(productID: String) async throws -> PurchaseOutcome {
        purchasedIDs.append(productID)
        if let e = purchaseError { throw e }
        return purchaseOutcome
    }

    func currentEntitlementJWS() async -> [String] { currentJWS }
}

/// 构造会员态便捷工厂。
func makeMembership(
    isMember: Bool = true,
    tier: String = "member",
    expireAt: Date? = Date(timeIntervalSince1970: 1_900_000_000),
    plan: String? = "monthly"
) -> MembershipStatus {
    MembershipStatus(
        isMember: isMember,
        memberTier: tier,
        memberExpireAt: expireAt,
        plan: plan,
        autoRenew: true,
        subscriptionStatus: isMember ? "active" : nil
    )
}

/// 构造档位便捷工厂。
func makePlan(plan: String = "monthly", productId: String = "com.redu.app.member.monthly", days: Int = 30) -> PlanItem {
    PlanItem(plan: plan, productId: productId, periodDays: days)
}
