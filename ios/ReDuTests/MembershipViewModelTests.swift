//
//  MembershipViewModelTests.swift
//  §4.4 会员页 VM 状态机 + 文案派生分支覆盖。
//

import XCTest
@testable import ReDu

@MainActor
final class MembershipViewModelTests: XCTestCase {

    private func makeStatus(
        isMember: Bool,
        memberTier: String = "free",
        expireAt: Date? = nil,
        plan: String? = nil,
        autoRenew: Bool = false
    ) -> MembershipStatus {
        MembershipStatus(
            isMember: isMember,
            memberTier: memberTier,
            memberExpireAt: expireAt,
            plan: plan,
            autoRenew: autoRenew,
            subscriptionStatus: nil
        )
    }

    // MARK: - display 状态机

    func test_display_nilStatus_isNonMember() {
        let vm = MembershipViewModel()
        XCTAssertEqual(vm.display, .nonMember)
        XCTAssertEqual(vm.primaryCTA, "开通会员")
        XCTAssertFalse(vm.showsManageSubscription)
    }

    func test_display_nonMemberWithoutPastExpire_isNonMember() {
        let vm = MembershipViewModel()
        vm.status = makeStatus(isMember: false, memberTier: "free")
        XCTAssertEqual(vm.display, .nonMember)
    }

    func test_display_nonMemberWithPastExpire_isExpired() {
        let past = Date(timeIntervalSinceNow: -86_400 * 10)
        let vm = MembershipViewModel()
        vm.status = makeStatus(isMember: false, memberTier: "expired", expireAt: past, plan: "monthly")
        if case let .expired(d) = vm.display {
            XCTAssertEqual(Int(d.timeIntervalSinceNow), Int(past.timeIntervalSinceNow))
        } else {
            XCTFail("应为 .expired")
        }
        XCTAssertEqual(vm.primaryCTA, "重新开通")
        XCTAssertTrue(vm.showsManageSubscription)
    }

    func test_display_activeFar_isActiveNotExpiringSoon() {
        let future = Date(timeIntervalSinceNow: 86_400 * 30)
        let vm = MembershipViewModel()
        vm.status = makeStatus(isMember: true, memberTier: "member", expireAt: future, plan: "yearly", autoRenew: true)
        if case let .active(days, soon) = vm.display {
            XCTAssertGreaterThanOrEqual(days, 29)
            XCTAssertLessThanOrEqual(days, 31)
            XCTAssertFalse(soon)
        } else {
            XCTFail("应为 .active")
        }
        XCTAssertEqual(vm.primaryCTA, "查看续费方案")
        XCTAssertTrue(vm.showsManageSubscription)
    }

    func test_display_activeNear_isExpiringSoon() {
        let in3Days = Date(timeIntervalSinceNow: 86_400 * 3)
        let vm = MembershipViewModel()
        vm.status = makeStatus(isMember: true, memberTier: "member", expireAt: in3Days, plan: "monthly", autoRenew: false)
        if case let .active(days, soon) = vm.display {
            XCTAssertTrue(soon)
            XCTAssertLessThanOrEqual(days, 4)
        } else {
            XCTFail("应为 .active")
        }
        XCTAssertEqual(vm.primaryCTA, "立即续费")
    }

    // MARK: - 文案派生

    func test_planTitle_mapsKnownPlans() {
        let vm = MembershipViewModel()
        vm.status = makeStatus(isMember: true, plan: "monthly")
        XCTAssertEqual(vm.planTitle, "月度会员")
        vm.status = makeStatus(isMember: true, plan: "quarterly")
        XCTAssertEqual(vm.planTitle, "季度会员")
        vm.status = makeStatus(isMember: true, plan: "yearly")
        XCTAssertEqual(vm.planTitle, "年度会员")
        vm.status = makeStatus(isMember: true, plan: nil)
        XCTAssertEqual(vm.planTitle, "—")
    }

    func test_autoRenewSubtitle_membershipOnly() {
        let vm = MembershipViewModel()
        // 非会员：空串
        vm.status = makeStatus(isMember: false)
        XCTAssertEqual(vm.autoRenewSubtitle, "")

        // 会员 + 自动续费
        vm.status = makeStatus(isMember: true, memberTier: "member", expireAt: Date().addingTimeInterval(86_400 * 30), autoRenew: true)
        XCTAssertEqual(vm.autoRenewSubtitle, "已开启自动续费")

        // 会员 + 未开
        vm.status = makeStatus(isMember: true, memberTier: "member", expireAt: Date().addingTimeInterval(86_400 * 30), autoRenew: false)
        XCTAssertEqual(vm.autoRenewSubtitle, "未开启自动续费")
    }

    // MARK: - daysRemaining 边界

    func test_daysRemaining_nilDate_returnsZero() {
        XCTAssertEqual(MembershipViewModel.daysRemaining(until: nil), 0)
    }

    func test_daysRemaining_past_returnsNegative() {
        let past = Date(timeIntervalSinceNow: -86_400 * 2)
        XCTAssertLessThanOrEqual(MembershipViewModel.daysRemaining(until: past), -1)
    }

    func test_daysRemaining_fixedNow_isStable() {
        // 用固定 now 避免依赖系统时钟，验证日历向上取整逻辑。
        let now = Date(timeIntervalSince1970: 1_700_000_000) // 任意基准
        let in5Days = now.addingTimeInterval(86_400 * 5)
        XCTAssertEqual(MembershipViewModel.daysRemaining(until: in5Days, now: now), 5)
        let in7HalfDays = now.addingTimeInterval(86_400 * 7 + 3600 * 12)
        XCTAssertEqual(MembershipViewModel.daysRemaining(until: in7HalfDays, now: now), 8) // 7.5 天向上取整
    }
}
