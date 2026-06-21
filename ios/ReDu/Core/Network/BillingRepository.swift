//
//  BillingRepository.swift
//  会员订阅数据仓库：档位拉取 + StoreKit2 交易上送核销 + 会员态查询。
//  与后端 content_engine/api/routers/billing.py 对齐。
//

import Foundation

/// 上送已签名交易的请求体，对齐 schemas.VerifyReceiptRequest。
private struct VerifyReceiptBody: Encodable {
    let signedTransaction: String

    enum CodingKeys: String, CodingKey {
        case signedTransaction = "signed_transaction"
    }
}

protocol BillingRepositoryProtocol {
    /// 订阅档位（无需登录）。
    func plans() async throws -> [PlanItem]
    /// 上送一笔 StoreKit2 已签名交易，核销并返回最新会员态。
    func verify(signedTransaction: String) async throws -> MembershipStatus
    /// 恢复购买：批量上送交易，取最新有效者核销。
    func restore(signedTransactions: [String]) async throws -> MembershipStatus
    /// 查询当前会员态。
    func membership() async throws -> MembershipStatus
}

final class BillingRepository: BillingRepositoryProtocol {
    static let shared = BillingRepository()

    private let client: APIClientProtocol
    private let encoder: JSONEncoder

    init(client: APIClientProtocol = APIClient.shared) {
        self.client = client
        self.encoder = JSONEncoder()
    }

    func plans() async throws -> [PlanItem] {
        try await client.send(.billingPlans, as: [PlanItem].self)
    }

    func verify(signedTransaction: String) async throws -> MembershipStatus {
        let body = try encoder.encode(VerifyReceiptBody(signedTransaction: signedTransaction))
        return try await client.send(.billingVerify(body: body), as: MembershipStatus.self)
    }

    func restore(signedTransactions: [String]) async throws -> MembershipStatus {
        let body = try encoder.encode(signedTransactions.map { VerifyReceiptBody(signedTransaction: $0) })
        return try await client.send(.billingRestore(body: body), as: MembershipStatus.self)
    }

    func membership() async throws -> MembershipStatus {
        try await client.send(.membership, as: MembershipStatus.self)
    }
}
