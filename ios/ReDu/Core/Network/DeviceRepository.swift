//
//  DeviceRepository.swift
//  阶段 4.2：APNs 设备 token 上报 / 解绑。
//
//  - register(token:bundleId:environment:) → POST /api/v1/me/devices
//  - unregister(token:)                    → DELETE /api/v1/me/devices/{token}
//
//  上报 token 由 AppDelegate 在 didRegisterForRemoteNotificationsWithDeviceToken
//  收到 Data 后转成 hex 字符串再调本仓库；解绑由 AuthStore.logout 等场景触发。
//

import Foundation

/// 设备 token 注册请求体（蛇形命名匹配后端 schemas.DeviceRegisterRequest）。
private struct DeviceRegisterBody: Encodable {
    let token: String
    let bundleId: String?
    let environment: String

    enum CodingKeys: String, CodingKey {
        case token, environment
        case bundleId = "bundle_id"
    }
}

protocol DeviceRepositoryProtocol {
    func register(token: String, bundleId: String?, environment: String) async throws -> DeviceTokenInfo
    func unregister(token: String) async throws
}

final class DeviceRepository: DeviceRepositoryProtocol {
    static let shared = DeviceRepository()

    private let client: APIClientProtocol
    private let encoder: JSONEncoder

    init(client: APIClientProtocol = APIClient.shared) {
        self.client = client
        self.encoder = JSONEncoder()
    }

    func register(token: String, bundleId: String?, environment: String) async throws -> DeviceTokenInfo {
        let body = try encoder.encode(
            DeviceRegisterBody(token: token, bundleId: bundleId, environment: environment)
        )
        return try await client.send(.registerDevice(body: body), as: DeviceTokenInfo.self)
    }

    func unregister(token: String) async throws {
        try await client.send(.unregisterDevice(token: token))
    }
}
