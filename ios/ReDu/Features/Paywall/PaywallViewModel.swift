//
//  PaywallViewModel.swift
//  会员开通编排：后端档位 + StoreKit 价格合并展示 → 购买 → 后端核销 → 刷新会员态。
//

import Foundation

@MainActor
final class PaywallViewModel: ObservableObject {
    /// 档位展示项：后端 plan 元数据 + StoreKit 价格。
    struct PlanRow: Identifiable, Equatable {
        let plan: PlanItem
        let product: StoreProduct?
        var id: String { plan.productId }
        var displayPrice: String { product?.displayPrice ?? "—" }
    }

    @Published private(set) var rows: [PlanRow] = []
    @Published private(set) var state: LoadState = .idle
    @Published var purchasing = false
    @Published var purchaseError: String?
    @Published var didPurchase = false

    private let billing: BillingRepositoryProtocol
    private let store: StoreServiceProtocol

    init(billing: BillingRepositoryProtocol = BillingRepository.shared,
         store: StoreServiceProtocol = StoreService.shared) {
        self.billing = billing
        self.store = store
    }

    /// 拉档位 + 价格。
    func load() async {
        state = .loading
        do {
            let plans = try await billing.plans()
            let products = (try? await store.loadProducts(ids: plans.map(\.productId))) ?? []
            let priceByID = Dictionary(uniqueKeysWithValues: products.map { ($0.id, $0) })
            rows = plans.map { PlanRow(plan: $0, product: priceByID[$0.productId]) }
            state = rows.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败，请重试")
        }
    }

    /// 购买指定档位：StoreKit 下单 → 后端核销。成功后置 didPurchase。
    /// 返回最新会员态（供调用方刷新全局态）。
    @discardableResult
    func purchase(_ row: PlanRow) async -> MembershipStatus? {
        guard !purchasing else { return nil }
        purchasing = true
        purchaseError = nil
        defer { purchasing = false }
        do {
            let outcome = try await store.purchase(productID: row.plan.productId)
            switch outcome {
            case .success(let jws):
                let status = try await billing.verify(signedTransaction: jws)
                didPurchase = status.isMember
                if status.isMember {
                    AnalyticsTracker.shared.track(
                        .purchaseSuccess,
                        props: [
                            "plan": AnyCodable(row.plan.plan),
                            "source": AnyCodable("purchase"),
                        ]
                    )
                } else {
                    purchaseError = "购买已提交，会员未生效，请稍后在「我的」恢复购买"
                }
                return status
            case .userCancelled:
                return nil
            case .pending:
                purchaseError = "购买待确认，请稍后在「我的」查看会员状态"
                return nil
            }
        } catch {
            purchaseError = errorText(error)
            return nil
        }
    }

    /// 恢复购买：汇总当前有效交易 → 后端核销。
    @discardableResult
    func restore() async -> MembershipStatus? {
        guard !purchasing else { return nil }
        purchasing = true
        purchaseError = nil
        defer { purchasing = false }
        let jwsList = await store.currentEntitlementJWS()
        guard !jwsList.isEmpty else {
            purchaseError = "未找到可恢复的购买记录"
            return nil
        }
        do {
            let status = try await billing.restore(signedTransactions: jwsList)
            didPurchase = status.isMember
            if !status.isMember { purchaseError = "未找到有效的会员订阅" }
            return status
        } catch {
            purchaseError = errorText(error)
            return nil
        }
    }

    private func errorText(_ error: Error) -> String {
        if let api = error as? APIError { return api.errorDescription ?? "购买失败" }
        if let store = error as? StoreError { return store.errorDescription ?? "购买失败" }
        return "购买失败，请稍后重试"
    }
}
