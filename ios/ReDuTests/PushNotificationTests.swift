//
//  PushNotificationTests.swift
//  4.2 APNs：PushNotificationCoordinator hex 转换 + token 上报；AppDelegate.parseRoute 解析；
//  AppRouter pendingTab/pendingRoute 行为。
//
//  注：UIApplication.registerForRemoteNotifications 涉及系统调用无法在单测真实触发，
//  这里只覆盖纯逻辑层（hex、handleRegistration、parseRoute、route 状态），
//  其余系统集成由人工冒烟 + CI 真机/Simulator 跑通。
//

import XCTest
@testable import ReDu

@MainActor
final class PushNotificationTests: XCTestCase {

    /// 每个用例独立的 UserDefaults（token 持久化的隔离面，防跨用例污染）。
    private func makeDefaults() -> UserDefaults {
        let name = "push-tests-\(UUID().uuidString)"
        let d = UserDefaults(suiteName: name)!
        d.removePersistentDomain(forName: name)
        return d
    }

    // MARK: - hex 转换

    func test_hexString_lowercaseFullWidth() {
        let data = Data([0x00, 0x0f, 0xab, 0xff])
        XCTAssertEqual(PushNotificationCoordinator.hexString(from: data), "000fabff")
    }

    func test_hexString_emptyData() {
        XCTAssertEqual(PushNotificationCoordinator.hexString(from: Data()), "")
    }

    // MARK: - handleRegistration 上报

    func test_handleRegistration_uploadsHexTokenAndCachesIt() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())
        let data = Data([0xde, 0xad, 0xbe, 0xef])

        await coordinator.handleRegistration(deviceToken: data, environment: "sandbox")

        XCTAssertEqual(fake.registerCalls.count, 1)
        XCTAssertEqual(fake.registerCalls.first?.token, "deadbeef")
        XCTAssertEqual(fake.registerCalls.first?.environment, "sandbox")
        XCTAssertEqual(coordinator.lastRegisteredToken, "deadbeef")
    }

    func test_handleRegistration_swallowsErrorButStillCachesToken() async {
        let fake = FakeDeviceRepository()
        fake.registerError = APIError.invalidResponse
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())

        await coordinator.handleRegistration(deviceToken: Data([0x01, 0x02]), environment: "production")

        // 上报失败也要缓存，下次还有机会解绑/重试
        XCTAssertEqual(coordinator.lastRegisteredToken, "0102")
        XCTAssertEqual(fake.registerCalls.count, 1)
    }

    // MARK: - token 持久化（冒烟修复 #6）

    func test_tokenPersistedAcrossInstances() async {
        // 模拟冷启动：新实例（同一 defaults）应读到之前注册的 token
        let defaults = makeDefaults()
        let first = PushNotificationCoordinator(repository: FakeDeviceRepository(), defaults: defaults)
        await first.handleRegistration(deviceToken: Data([0xca, 0xfe]), environment: "sandbox")

        let second = PushNotificationCoordinator(repository: FakeDeviceRepository(), defaults: defaults)
        XCTAssertEqual(second.lastRegisteredToken, "cafe")
    }

    // MARK: - unregisterCurrentDevice

    func test_unregister_callsRepositoryAndClearsCache() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())
        await coordinator.handleRegistration(deviceToken: Data([0xaa, 0xbb]), environment: "sandbox")

        await coordinator.unregisterCurrentDevice()

        XCTAssertEqual(fake.unregisterCalls, ["aabb"])
        XCTAssertNil(coordinator.lastRegisteredToken)
    }

    func test_unregister_afterColdStart_usesPersistedToken() async {
        // 冷启动后直接登出：内存为空但持久化 token 在，仍应正确解绑
        let defaults = makeDefaults()
        let first = PushNotificationCoordinator(repository: FakeDeviceRepository(), defaults: defaults)
        await first.handleRegistration(deviceToken: Data([0xbe, 0xef]), environment: "production")

        let fake2 = FakeDeviceRepository()
        let second = PushNotificationCoordinator(repository: fake2, defaults: defaults)
        await second.unregisterCurrentDevice()

        XCTAssertEqual(fake2.unregisterCalls, ["beef"])
        XCTAssertNil(second.lastRegisteredToken)
    }

    func test_unregister_noopWhenNoToken() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())

        await coordinator.unregisterCurrentDevice()

        XCTAssertTrue(fake.unregisterCalls.isEmpty)
    }

    // MARK: - syncRegistrationAfterLogin（登录后补报换绑）

    func test_syncAfterLogin_rereportsCachedToken() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())
        // 未登录首启：token 已注册但上报失败（401）
        fake.registerError = APIError.unauthorized
        await coordinator.handleRegistration(deviceToken: Data([0x01, 0x02]), environment: "sandbox")
        XCTAssertEqual(fake.registerCalls.count, 1)

        // 登录成功 → 补报（后端 upsert 换绑到当前账号）
        fake.registerError = nil
        await coordinator.syncRegistrationAfterLogin(environment: "sandbox")

        XCTAssertEqual(fake.registerCalls.count, 2)
        XCTAssertEqual(fake.registerCalls.last?.token, "0102")
        XCTAssertEqual(fake.registerCalls.last?.environment, "sandbox")
    }

    func test_syncAfterLogin_failureDoesNotClearToken() async {
        let fake = FakeDeviceRepository()
        fake.registerError = APIError.invalidResponse
        let coordinator = PushNotificationCoordinator(repository: fake, defaults: makeDefaults())
        await coordinator.handleRegistration(deviceToken: Data([0x03, 0x04]), environment: "production")

        await coordinator.syncRegistrationAfterLogin(environment: "production")

        // 补报失败保留 token，登出解绑 / 下次登录仍可用
        XCTAssertEqual(coordinator.lastRegisteredToken, "0304")
    }

    // MARK: - AppDelegate.parseRoute（仅 iOS 平台）

    #if canImport(UIKit)
    func test_parseRoute_intEventID() {
        let route = AppDelegate.parseRoute(from: ["event_id": 123])
        XCTAssertEqual(route, .eventDetail(id: 123, title: nil))
    }

    func test_parseRoute_stringEventID() {
        let route = AppDelegate.parseRoute(from: ["event_id": "456"])
        XCTAssertEqual(route, .eventDetail(id: 456, title: nil))
    }

    func test_parseRoute_missingOrInvalid() {
        XCTAssertNil(AppDelegate.parseRoute(from: [:]))
        XCTAssertNil(AppDelegate.parseRoute(from: ["event_id": "not-a-number"]))
        XCTAssertNil(AppDelegate.parseRoute(from: ["other": 1]))
    }
    #endif

    // MARK: - AppRouter

    func test_router_routeSetsPendingFields() {
        let router = AppRouter()
        XCTAssertNil(router.pendingTab)
        XCTAssertNil(router.pendingRoute)

        router.route(to: .eventDetail(id: 7, title: nil))

        XCTAssertEqual(router.pendingTab, .home)
        XCTAssertEqual(router.pendingRoute, .eventDetail(id: 7, title: nil))
    }

    func test_router_clearPendingRoute() {
        let router = AppRouter()
        router.route(to: .eventDetail(id: 7, title: nil))
        router.clearPendingRoute()
        XCTAssertNil(router.pendingRoute)
        // 切 Tab 信号保留，由 RootTabView 单独消费清零
        XCTAssertEqual(router.pendingTab, .home)
    }
}
