//
//  AppDelegate.swift
//  阶段 4.2：APNs 推送系统回调适配器（UIApplicationDelegateAdaptor 注入）。
//
//  职责（薄壳，业务下沉到 PushNotificationCoordinator / AppRouter）：
//  - didFinishLaunching → 注册成为 UNUserNotificationCenter 的 delegate；
//  - didRegisterForRemoteNotifications(withDeviceToken:) → token 转 hex 上报后端；
//  - didFailToRegisterForRemoteNotifications(withError:) → 仅记日志，不阻塞 App；
//  - userNotificationCenter:didReceive: → 解析 userInfo.event_id → AppRouter 跳详情；
//  - userNotificationCenter:willPresent: → App 前台时也允许 banner+sound。
//

import Foundation
#if canImport(UIKit)
import UIKit
#endif
import UserNotifications

#if canImport(UIKit)
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    /// 由 ReDuApp 在 init 时回填，供 didReceive 解析 deep link 后调跳转。
    weak var router: AppRouter?

    /// DEBUG / Release 切换 APNs 环境（与 entitlements aps-environment 对齐）。
    private var apnsEnvironment: String {
        #if DEBUG
        return "sandbox"
        #else
        return "production"
        #endif
    }

    // MARK: - UIApplicationDelegate

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func application(
        _ application: UIApplication,
        didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data
    ) {
        Task { @MainActor in
            await PushNotificationCoordinator.shared.handleRegistration(
                deviceToken: deviceToken,
                environment: apnsEnvironment
            )
        }
    }

    func application(
        _ application: UIApplication,
        didFailToRegisterForRemoteNotificationsWithError error: Error
    ) {
        print("[push] registerForRemoteNotifications 失败: \(error)")
    }

    // MARK: - UNUserNotificationCenterDelegate

    /// App 前台时收到通知：仍显示横幅 + 播放声音（默认会被吞掉）。
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .sound, .badge])
    }

    /// 用户点了通知 → 解析 userInfo.event_id → AppRouter.route(to:)
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        defer { completionHandler() }
        let userInfo = response.notification.request.content.userInfo
        guard let route = Self.parseRoute(from: userInfo) else { return }
        Task { @MainActor in
            if case let .eventDetail(id, _) = route {
                AnalyticsTracker.shared.track(.pushOpen, props: ["event_id": AnyCodable(id)])
            }
            router?.route(to: route, tab: .home)
        }
    }

    // MARK: - userInfo 解析

    /// 从 APNs payload 顶层键 ``event_id`` 解析出 ``AppRoute.eventDetail``。
    /// 兼容 Int 与可转 Int 的 String 两种类型（payload 在 JSON 序列化时可能任一）。
    static func parseRoute(from userInfo: [AnyHashable: Any]) -> AppRoute? {
        let eventID: Int?
        if let i = userInfo["event_id"] as? Int {
            eventID = i
        } else if let s = userInfo["event_id"] as? String, let i = Int(s) {
            eventID = i
        } else {
            eventID = nil
        }
        guard let id = eventID else { return nil }
        return .eventDetail(id: id, title: nil)
    }
}
#endif
