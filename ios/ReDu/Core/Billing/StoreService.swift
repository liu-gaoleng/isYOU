//
//  StoreService.swift
//  StoreKit 2 封装：商品拉取 / 购买 / 恢复 / 交易更新监听。
//  购买产出 JWSTransaction 字符串，交由后端 /billing/verify 离线验签核销。
//

import Foundation
import StoreKit

/// StoreKit 错误。
enum StoreError: LocalizedError, Equatable {
    case productNotFound
    case unverified
    case unknown

    var errorDescription: String? {
        switch self {
        case .productNotFound: return "未找到对应商品，请稍后重试"
        case .unverified: return "交易校验未通过"
        case .unknown: return "购买失败，请稍后重试"
        }
    }
}

/// 商品的展示视图（隔离 StoreKit 具体类型，便于测试）。
struct StoreProduct: Identifiable, Hashable {
    let id: String          // product id，对齐后端 PlanItem.productId
    let displayName: String
    let displayPrice: String
}

/// 购买结果。
enum PurchaseOutcome: Equatable {
    case success(jws: String)
    case userCancelled
    case pending
}

/// StoreKit 服务协议，便于 UI 层注入 mock。
protocol StoreServiceProtocol {
    /// 按 product id 拉取商品（用于展示价格）。
    func loadProducts(ids: [String]) async throws -> [StoreProduct]
    /// 购买指定商品，成功返回 JWSTransaction。
    func purchase(productID: String) async throws -> PurchaseOutcome
    /// 当前有效权益对应的已签名交易（恢复购买用）。
    func currentEntitlementJWS() async -> [String]
}

/// 默认实现：StoreKit 2。
final class StoreService: StoreServiceProtocol {
    static let shared = StoreService()

    func loadProducts(ids: [String]) async throws -> [StoreProduct] {
        let products = try await Product.products(for: ids)
        return products.map {
            StoreProduct(id: $0.id, displayName: $0.displayName, displayPrice: $0.displayPrice)
        }
    }

    func purchase(productID: String) async throws -> PurchaseOutcome {
        let products = try await Product.products(for: [productID])
        guard let product = products.first else { throw StoreError.productNotFound }
        let result = try await product.purchase()
        switch result {
        case .success(let verification):
            let transaction = try Self.checkVerified(verification)
            // 后端核销成功后才是真正到账，这里先 finish 关闭交易队列。
            await transaction.finish()
            return .success(jws: verification.jwsRepresentation)
        case .userCancelled:
            return .userCancelled
        case .pending:
            return .pending
        @unknown default:
            throw StoreError.unknown
        }
    }

    func currentEntitlementJWS() async -> [String] {
        var jwsList: [String] = []
        for await verification in Transaction.currentEntitlements {
            jwsList.append(verification.jwsRepresentation)
        }
        return jwsList
    }

    /// 交易更新流（续订 / Ask to Buy / 退款）：产出已验签交易的 JWS。
    /// 由 App 入口长期监听，逐笔上送后端核销。
    func transactionUpdates() -> AsyncStream<String> {
        AsyncStream { continuation in
            let task = Task.detached {
                for await verification in Transaction.updates {
                    guard case .verified(let transaction) = verification else { continue }
                    continuation.yield(verification.jwsRepresentation)
                    await transaction.finish()
                }
                continuation.finish()
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    private static func checkVerified<T>(_ result: VerificationResult<T>) throws -> T {
        switch result {
        case .unverified:
            throw StoreError.unverified
        case .verified(let safe):
            return safe
        }
    }
}
