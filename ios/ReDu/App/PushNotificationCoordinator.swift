//
//  PushNotificationCoordinator.swift
//  阶段 4.2：APNs 权限申请 + token 上报 + 解绑。
//
//  这是 AppDelegate / AuthStore 共用的薄业务层，便于注入 Fake 仓库做单测：
//  AppDelegate 只把系统回调透传给这个 coordinator，coordinator 把 Data 转 hex
//  并调 ``DeviceRepository``，对外暴露 ``handleRegistration(deviceToken:)`` 等接口。
//

import Foundation
#if canImport(UIKit)
import UIKit
#endif
import UserNotifications

@MainActor
final class PushNotificationCoordinator {
    static let shared = PushNotificationCoordinator()

    private let repository: DeviceRepositoryProtocol
    /// 最近一次成功上报的 hex token，用于 logout 时解绑（不持久化，重启即丢）。
    private(set) var lastRegisteredToken: String?

    init(repository: DeviceRepositoryProtocol = DeviceRepository.shared) {
        self.repository = repository
    }

    /// 把 APNs 下发的 Data 转成 64 字符 hex 字符串（小写）。
    /// 与 Apple 文档一致：每字节 2 位十六进制。
    static func hexString(from deviceToken: Data) -> String {
        deviceToken.map { String(format: "%02x", $0) }.joined()
    }

    /// 申请通知授权（alert + sound + badge）。返回授权结果。
    @discardableResult
    func requestAuthorization() async -> Bool {
        let center = UNUserNotificationCenter.current()
        do {
            return try await center.requestAuthorization(options: [.alert, .sound, .badge])
        } catch {
            return false
        }
    }

    /// 通知系统注册远端推送（必须在主线程）。
    func registerForRemoteNotifications() {
#if canImport(UIKit)
        UIApplication.shared.registerForRemoteNotifications()
#endif
    }

    /// AppDelegate.didRegisterForRemoteNotificationsWithDeviceToken 回调时调用：
    /// 把 token 转 hex 后上送后端 /me/devices。
    /// - Parameters:
    ///   - deviceToken: APNs 下发的原始 Data
    ///   - environment: ``production`` / ``sandbox``，需与 entitlements 一致
    func handleRegistration(deviceToken: Data, environment: String = "production") async {
        let token = Self.hexString(from: deviceToken)
        lastRegisteredToken = token
        let bundleId = Bundle.main.bundleIdentifier
        do {
            _ = try await repository.register(
                token: token, bundleId: bundleId, environment: environment
            )
        } catch {
            // 上报失败仅记录到 console；下次 App 启动时仍会再次注册重试。
            print("[push] device token 上报失败: \(error)")
        }
    }

    /// 登出 / 关闭推送时调用：通知后端把当前 token 软删，避免继续收推送。
    func unregisterCurrentDevice() async {
        guard let token = lastRegisteredToken else { return }
        try? await repository.unregister(token: token)
        lastRegisteredToken = nil
    }
}
