//
//  PushNotificationCoordinator.swift
//  阶段 4.2：APNs 权限申请 + token 上报 + 解绑。
//
//  这是 AppDelegate / AuthStore 共用的薄业务层，便于注入 Fake 仓库做单测：
//  AppDelegate 只把系统回调透传给这个 coordinator，coordinator 把 Data 转 hex
//  并调 ``DeviceRepository``，对外暴露 ``handleRegistration(deviceToken:)`` 等接口。
//
//  token 持久化（UserDefaults，hex 字符串非敏感）解决两个真实场景：
//  - 冷启动后直接登出：内存态 lastRegisteredToken 为 nil 会跳过解绑，设备继续收到
//    可能属于别的账号的推送（隐私问题）；
//  - 未登录启动时上报 401 失败：登录成功后 ``syncRegistrationAfterLogin`` 用持久化
//    token 补报换绑，无需杀进程重进。
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
    private let defaults: UserDefaults
    private static let tokenDefaultsKey = "push.lastDeviceTokenHex"

    /// 最近一次注册的 hex token（UserDefaults 持久化，重启不丢）。
    /// 与上报成败解耦：只要 APNs 给过 token 就记下，供登录后补报 / 登出解绑。
    private(set) var lastRegisteredToken: String? {
        get { defaults.string(forKey: Self.tokenDefaultsKey) }
        set {
            if let newValue {
                defaults.set(newValue, forKey: Self.tokenDefaultsKey)
            } else {
                defaults.removeObject(forKey: Self.tokenDefaultsKey)
            }
        }
    }

    init(
        repository: DeviceRepositoryProtocol = DeviceRepository.shared,
        defaults: UserDefaults = .standard
    ) {
        self.repository = repository
        self.defaults = defaults
    }

    /// 当前构建对应的 APNs 环境（与 entitlements aps-environment 对齐）。
    /// AppDelegate 注册回调与登录后补报统一走这里，避免两处 #if DEBUG 漂移。
    static var currentAPNSEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
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
        await report(token: token, environment: environment)
    }

    /// 登录成功后调用（未登录首启时 token 上报必 401 被丢弃）：
    /// - 已有持久化 token → 补报（后端 upsert 会把 token 换绑到当前账号）；
    /// - 无 token（如首次授权前）→ 重新走系统注册，触发 AppDelegate 回调完成上报。
    func syncRegistrationAfterLogin(
        environment: String = PushNotificationCoordinator.currentAPNSEnvironment
    ) async {
        if let token = lastRegisteredToken {
            await report(token: token, environment: environment)
        } else {
            let granted = await requestAuthorization()
            if granted {
                registerForRemoteNotifications()
            }
        }
    }

    /// 登出 / 关闭推送时调用：通知后端把当前 token 软删，避免继续收推送。
    /// 用持久化 token，冷启动后直接登出也能正确解绑。
    func unregisterCurrentDevice() async {
        guard let token = lastRegisteredToken else { return }
        try? await repository.unregister(token: token)
        lastRegisteredToken = nil
    }

    /// 上送 token 到后端；失败仅记日志（启动/登录后均会重试，不阻塞主流程）。
    private func report(token: String, environment: String) async {
        let bundleId = Bundle.main.bundleIdentifier
        do {
            _ = try await repository.register(
                token: token, bundleId: bundleId, environment: environment
            )
        } catch {
            print("[push] device token 上报失败: \(error)")
        }
    }
}
