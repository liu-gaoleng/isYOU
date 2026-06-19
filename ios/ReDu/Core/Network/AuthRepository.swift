//
//  AuthRepository.swift
//  账号 / 收藏 / 历史 / 设置数据仓库：把鉴权相关 Endpoint 收敛成语义化方法。
//

import Foundation

// MARK: - 请求体（Encodable，对齐后端 schemas）

/// Sign in with Apple 登录请求体，对齐 schemas.AppleLoginRequest。
private struct AppleLoginBody: Encodable {
    let identityToken: String
    let displayName: String?

    enum CodingKeys: String, CodingKey {
        case identityToken = "identity_token"
        case displayName = "display_name"
    }
}

/// dev 测试登录请求体，对齐 schemas.DevLoginRequest。
private struct DevLoginBody: Encodable {
    let appleUserId: String
    let email: String?
    let displayName: String?
    let asMember: Bool

    enum CodingKeys: String, CodingKey {
        case appleUserId = "apple_user_id"
        case email
        case displayName = "display_name"
        case asMember = "as_member"
    }
}

/// 推送设置更新请求体，对齐 schemas.PushSettingsUpdate（全部可选）。
private struct PushSettingsUpdateBody: Encodable {
    let dailyPush: Bool?
    let pushTime: String?
    let breakingPush: Bool?

    enum CodingKeys: String, CodingKey {
        case dailyPush = "daily_push"
        case pushTime = "push_time"
        case breakingPush = "breaking_push"
    }
}

protocol AuthRepositoryProtocol {
    // 登录 / 用户
    func loginWithApple(identityToken: String, displayName: String?) async throws -> LoginResponse
    func devLogin(appleUserID: String, email: String?, displayName: String?, asMember: Bool) async throws -> LoginResponse
    func currentUser() async throws -> UserProfile

    // 收藏
    func addFavorite(eventID: Int) async throws -> FavoriteState
    func removeFavorite(eventID: Int) async throws -> FavoriteState
    func listFavorites() async throws -> [FavoriteCard]

    // 历史
    func recordHistory(eventID: Int) async throws
    func listHistory() async throws -> [HistoryCard]
    func clearHistory() async throws

    // 设置
    func getSettings() async throws -> PushSettings
    func updateSettings(dailyPush: Bool?, pushTime: String?, breakingPush: Bool?) async throws -> PushSettings
}

final class AuthRepository: AuthRepositoryProtocol {
    static let shared = AuthRepository()

    private let client: APIClientProtocol
    private let encoder: JSONEncoder

    init(client: APIClientProtocol = APIClient.shared) {
        self.client = client
        self.encoder = JSONEncoder()
    }

    // MARK: 登录 / 用户

    func loginWithApple(identityToken: String, displayName: String?) async throws -> LoginResponse {
        let body = try encoder.encode(AppleLoginBody(identityToken: identityToken, displayName: displayName))
        return try await client.send(.appleLogin(body: body), as: LoginResponse.self)
    }

    func devLogin(appleUserID: String, email: String?, displayName: String?, asMember: Bool) async throws -> LoginResponse {
        let body = try encoder.encode(
            DevLoginBody(appleUserId: appleUserID, email: email, displayName: displayName, asMember: asMember)
        )
        return try await client.send(.devLogin(body: body), as: LoginResponse.self)
    }

    func currentUser() async throws -> UserProfile {
        try await client.send(.me, as: UserProfile.self)
    }

    // MARK: 收藏

    func addFavorite(eventID: Int) async throws -> FavoriteState {
        try await client.send(.addFavorite(eventID: eventID), as: FavoriteState.self)
    }

    func removeFavorite(eventID: Int) async throws -> FavoriteState {
        try await client.send(.removeFavorite(eventID: eventID), as: FavoriteState.self)
    }

    func listFavorites() async throws -> [FavoriteCard] {
        try await client.send(.listFavorites, as: [FavoriteCard].self)
    }

    // MARK: 历史

    func recordHistory(eventID: Int) async throws {
        try await client.send(.recordHistory(eventID: eventID))
    }

    func listHistory() async throws -> [HistoryCard] {
        try await client.send(.listHistory, as: [HistoryCard].self)
    }

    func clearHistory() async throws {
        try await client.send(.clearHistory)
    }

    // MARK: 设置

    func getSettings() async throws -> PushSettings {
        try await client.send(.getSettings, as: PushSettings.self)
    }

    func updateSettings(dailyPush: Bool?, pushTime: String?, breakingPush: Bool?) async throws -> PushSettings {
        let body = try encoder.encode(
            PushSettingsUpdateBody(dailyPush: dailyPush, pushTime: pushTime, breakingPush: breakingPush)
        )
        return try await client.send(.updateSettings(body: body), as: PushSettings.self)
    }
}
