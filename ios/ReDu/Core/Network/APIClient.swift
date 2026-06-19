//
//  APIClient.swift
//  统一网络客户端：URLSession + async/await + Codable 解码。
//  后端时间字段为 ISO8601（含微秒/时区），用自定义解码策略兜底。
//

import Foundation

/// 网络错误类型。
enum APIError: LocalizedError, Equatable {
    case invalidResponse
    case unauthorized
    case http(status: Int)
    case decoding(Error)
    case transport(Error)

    var errorDescription: String? {
        switch self {
        case .invalidResponse: return "无效的服务响应"
        case .unauthorized: return "登录已过期，请重新登录"
        case .http(let status): return "服务异常（\(status)）"
        case .decoding: return "数据解析失败"
        case .transport: return "网络连接失败"
        }
    }

    static func == (lhs: APIError, rhs: APIError) -> Bool {
        switch (lhs, rhs) {
        case (.invalidResponse, .invalidResponse),
             (.unauthorized, .unauthorized):
            return true
        case let (.http(a), .http(b)):
            return a == b
        case (.decoding, .decoding), (.transport, .transport):
            return true
        default:
            return false
        }
    }
}

/// API 客户端协议，便于测试时注入 mock。
protocol APIClientProtocol {
    func send<T: Decodable>(_ endpoint: Endpoint, as type: T.Type) async throws -> T
    /// 无响应体（204）或仅关心成功与否的端点。
    func send(_ endpoint: Endpoint) async throws
}

/// 默认实现：进程内单例。
final class APIClient: APIClientProtocol {
    static let shared = APIClient()

    private let session: URLSession
    private let decoder: JSONDecoder
    private let tokenStore: TokenStoring

    init(session: URLSession = .shared, tokenStore: TokenStoring = TokenStore.shared) {
        self.session = session
        self.tokenStore = tokenStore
        let decoder = JSONDecoder()
        // 后端时间可能带微秒，标准 .iso8601 解析会失败，这里用自定义格式器兜底。
        decoder.dateDecodingStrategy = .custom { d in
            let container = try d.singleValueContainer()
            let raw = try container.decode(String.self)
            if let date = ISO8601DateParser.parse(raw) {
                return date
            }
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "无法解析日期: \(raw)"
            )
        }
        self.decoder = decoder
    }

    /// 执行请求并校验状态码，返回响应体数据。
    private func perform(_ endpoint: Endpoint) async throws -> Data {
        var request = endpoint.makeRequest()
        // 需鉴权端点统一注入 Bearer token。
        if endpoint.requiresAuth, let token = tokenStore.token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let data: Data
        let response: URLResponse
        do {
            (data, response) = try await session.data(for: request)
        } catch {
            throw APIError.transport(error)
        }
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        if http.statusCode == 401 {
            throw APIError.unauthorized
        }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError.http(status: http.statusCode)
        }
        return data
    }

    func send<T: Decodable>(_ endpoint: Endpoint, as type: T.Type) async throws -> T {
        let data = try await perform(endpoint)
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decoding(error)
        }
    }

    func send(_ endpoint: Endpoint) async throws {
        _ = try await perform(endpoint)
    }
}

/// ISO8601 日期解析：兼容带/不带小数秒、带/不带时区。
enum ISO8601DateParser {
    private static let withFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    /// 后端可能返回不带时区的 naive datetime（如 2026-06-14T08:00:00），按 UTC 兜底。
    private static let naive: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        return f
    }()

    static func parse(_ raw: String) -> Date? {
        if let d = withFractional.date(from: raw) { return d }
        if let d = plain.date(from: raw) { return d }
        // 去掉小数秒后再试 naive
        let trimmed = raw.contains(".") ? String(raw.prefix(while: { $0 != "." })) : raw
        return naive.date(from: trimmed)
    }
}
