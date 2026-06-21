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
        let coordinator = PushNotificationCoordinator(repository: fake)
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
        let coordinator = PushNotificationCoordinator(repository: fake)

        await coordinator.handleRegistration(deviceToken: Data([0x01, 0x02]), environment: "production")

        // 上报失败也要缓存，下次还有机会解绑/重试
        XCTAssertEqual(coordinator.lastRegisteredToken, "0102")
        XCTAssertEqual(fake.registerCalls.count, 1)
    }

    // MARK: - unregisterCurrentDevice

    func test_unregister_callsRepositoryAndClearsCache() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake)
        await coordinator.handleRegistration(deviceToken: Data([0xaa, 0xbb]), environment: "sandbox")

        await coordinator.unregisterCurrentDevice()

        XCTAssertEqual(fake.unregisterCalls, ["aabb"])
        XCTAssertNil(coordinator.lastRegisteredToken)
    }

    func test_unregister_noopWhenNoToken() async {
        let fake = FakeDeviceRepository()
        let coordinator = PushNotificationCoordinator(repository: fake)

        await coordinator.unregisterCurrentDevice()

        XCTAssertTrue(fake.unregisterCalls.isEmpty)
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
