//
//  PaywallViewModelTests.swift
//  4.1 IAP：会员开通编排（档位 + 购买 + 核销 + 恢复）单测。
//

import XCTest
@testable import ReDu

@MainActor
final class PaywallViewModelTests: XCTestCase {
    func test_load_mergesPlansAndPrices() async {
        let billing = FakeBillingRepository()
        billing.plansResult = [
            makePlan(plan: "monthly", productId: "p.m", days: 30),
            makePlan(plan: "yearly", productId: "p.y", days: 365),
        ]
        let store = FakeStoreService()
        store.products = [
            StoreProduct(id: "p.m", displayName: "月", displayPrice: "￥18"),
            // p.y 缺价：模拟商品未上架，应该回退到 "—"
        ]
        let vm = PaywallViewModel(billing: billing, store: store)

        await vm.load()

        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.rows.count, 2)
        XCTAssertEqual(vm.rows[0].id, "p.m")
        XCTAssertEqual(vm.rows[0].displayPrice, "￥18")
        XCTAssertEqual(vm.rows[1].id, "p.y")
        XCTAssertEqual(vm.rows[1].displayPrice, "—")
    }

    func test_load_emptyPlans_setsEmpty() async {
        let billing = FakeBillingRepository()
        billing.plansResult = []
        let vm = PaywallViewModel(billing: billing, store: FakeStoreService())

        await vm.load()

        XCTAssertEqual(vm.state, .empty)
        XCTAssertTrue(vm.rows.isEmpty)
    }

    func test_purchase_success_verifiesAndFlagsPurchased() async {
        let billing = FakeBillingRepository()
        billing.plansResult = [makePlan()]
        billing.verifyResult = makeMembership()
        let store = FakeStoreService()
        store.products = [StoreProduct(id: "com.redu.app.member.monthly", displayName: "月", displayPrice: "￥18")]
        store.purchaseOutcome = .success(jws: "JWS-OK")
        let vm = PaywallViewModel(billing: billing, store: store)
        await vm.load()

        let status = await vm.purchase(vm.rows[0])

        XCTAssertEqual(store.purchasedIDs, ["com.redu.app.member.monthly"])
        XCTAssertEqual(billing.verifiedJWS, ["JWS-OK"])
        XCTAssertEqual(status?.isMember, true)
        XCTAssertTrue(vm.didPurchase)
        XCTAssertNil(vm.purchaseError)
    }

    func test_purchase_userCancelled_noVerify_noError() async {
        let billing = FakeBillingRepository()
        billing.plansResult = [makePlan()]
        let store = FakeStoreService()
        store.purchaseOutcome = .userCancelled
        let vm = PaywallViewModel(billing: billing, store: store)
        await vm.load()

        let status = await vm.purchase(vm.rows[0])

        XCTAssertNil(status)
        XCTAssertTrue(billing.verifiedJWS.isEmpty)
        XCTAssertFalse(vm.didPurchase)
        XCTAssertNil(vm.purchaseError)
    }

    func test_purchase_pending_setsHint_noVerify() async {
        let billing = FakeBillingRepository()
        billing.plansResult = [makePlan()]
        let store = FakeStoreService()
        store.purchaseOutcome = .pending
        let vm = PaywallViewModel(billing: billing, store: store)
        await vm.load()

        let status = await vm.purchase(vm.rows[0])

        XCTAssertNil(status)
        XCTAssertTrue(billing.verifiedJWS.isEmpty)
        XCTAssertNotNil(vm.purchaseError)
    }

    func test_purchase_verifyFailure_surfacesError() async {
        let billing = FakeBillingRepository()
        billing.plansResult = [makePlan()]
        billing.verifyError = APIError.http(status: 400)
        let store = FakeStoreService()
        store.purchaseOutcome = .success(jws: "JWS")
        let vm = PaywallViewModel(billing: billing, store: store)
        await vm.load()

        let status = await vm.purchase(vm.rows[0])

        XCTAssertNil(status)
        XCTAssertEqual(billing.verifiedJWS, ["JWS"])
        XCTAssertNotNil(vm.purchaseError)
        XCTAssertFalse(vm.didPurchase)
    }

    func test_restore_noEntitlement_setsHint() async {
        let billing = FakeBillingRepository()
        let store = FakeStoreService()
        store.currentJWS = []
        let vm = PaywallViewModel(billing: billing, store: store)

        let status = await vm.restore()

        XCTAssertNil(status)
        XCTAssertTrue(billing.restoredJWS.isEmpty)
        XCTAssertNotNil(vm.purchaseError)
    }

    func test_restore_success_callsBackendWithAllJWS() async {
        let billing = FakeBillingRepository()
        billing.restoreResult = makeMembership()
        let store = FakeStoreService()
        store.currentJWS = ["A", "B"]
        let vm = PaywallViewModel(billing: billing, store: store)

        let status = await vm.restore()

        XCTAssertEqual(billing.restoredJWS, [["A", "B"]])
        XCTAssertEqual(status?.isMember, true)
        XCTAssertTrue(vm.didPurchase)
    }
}

@MainActor
final class AuthStoreMembershipTests: XCTestCase {
    func test_applyMembership_updatesUser_withoutNetwork() async {
        let repo = FakeAuthRepository()
        let token = MembershipTokenStore()
        token.save("tok")
        repo.currentUserResult = UserProfile(
            id: 1, email: "u@example.com", displayName: "U",
            createdVia: "apple", memberTier: "free", isMember: false, memberExpireAt: nil
        )
        let store = AuthStore(repo: repo, tokenStore: token)

        await store.restore()
        XCTAssertEqual(store.user?.id, 1)
        XCTAssertFalse(store.isMember)

        let status = makeMembership(isMember: true, tier: "member")
        await store.applyMembership(status)

        XCTAssertTrue(store.isMember)
        XCTAssertEqual(store.user?.memberTier, "member")
        XCTAssertEqual(store.user?.memberExpireAt, status.memberExpireAt)
    }

    func test_applyMembership_anonymous_remainsAnonymous() async {
        let repo = FakeAuthRepository()
        let store = AuthStore(repo: repo, tokenStore: MembershipTokenStore())

        await store.applyMembership(makeMembership(isMember: true))

        XCTAssertFalse(store.isAuthenticated)
    }
}

/// 仅供本测试使用的内存 TokenStoring（避免与其它测试文件重复定义）。
final class MembershipTokenStore: TokenStoring {
    private var value: String?
    var token: String? { value }
    func save(_ token: String) { value = token }
    func clear() { value = nil }
}
