//
//  Endpoint.swift
//  接口定义：与后端 content_engine/api/routers（brief / auth / me）对齐。
//  前缀 /api/v1。
//

import Foundation

/// 后端环境配置。模拟器默认连本机 8000 端口的 FastAPI。
enum APIEnv {
    /// 后端基址。真机联调时改成局域网 IP 或线上域名。
    static let baseURL = URL(string: "http://127.0.0.1:8000")!
    /// API 版本前缀。
    static let prefix = "/api/v1"
}

/// HTTP 方法。
enum HTTPMethod: String {
    case get = "GET"
    case post = "POST"
    case put = "PUT"
    case delete = "DELETE"
}

/// 端点定义：把 path + query + method + body + 是否需鉴权收敛成可构造 URLRequest 的工厂。
enum Endpoint {
    // 只读内容
    case dailyBrief(date: String?, module: String?, limit: Int)
    case feed(cursor: String?, module: String?, limit: Int)
    case ranking(module: String?, limit: Int)
    case eventDetail(id: Int)
    case search(q: String, cursor: String?, module: String?, limit: Int)

    // 账号
    case appleLogin(body: Data)
    case devLogin(body: Data)
    case me

    // 会员订阅 / IAP
    case billingPlans
    case billingVerify(body: Data)
    case billingRestore(body: Data)
    case membership

    // 收藏 / 历史 / 设置（均需登录）
    case addFavorite(eventID: Int)
    case removeFavorite(eventID: Int)
    case listFavorites
    case recordHistory(eventID: Int)
    case listHistory
    case clearHistory
    case getSettings
    case updateSettings(body: Data)

    var method: HTTPMethod {
        switch self {
        case .dailyBrief, .feed, .ranking, .eventDetail, .search,
             .me, .billingPlans, .membership,
             .listFavorites, .listHistory, .getSettings:
            return .get
        case .appleLogin, .devLogin, .billingVerify, .billingRestore,
             .addFavorite, .recordHistory:
            return .post
        case .updateSettings:
            return .put
        case .removeFavorite, .clearHistory:
            return .delete
        }
    }

    /// 是否需要在请求头注入 Bearer token。
    var requiresAuth: Bool {
        switch self {
        case .me, .billingVerify, .billingRestore, .membership,
             .addFavorite, .removeFavorite, .listFavorites,
             .recordHistory, .listHistory, .clearHistory, .getSettings, .updateSettings:
            return true
        default:
            return false
        }
    }

    var path: String {
        let p = APIEnv.prefix
        switch self {
        case .dailyBrief: return "\(p)/daily-brief"
        case .feed: return "\(p)/feed"
        case .ranking: return "\(p)/ranking"
        case .eventDetail(let id): return "\(p)/event/\(id)"
        case .search: return "\(p)/search"
        case .appleLogin: return "\(p)/auth/apple"
        case .devLogin: return "\(p)/auth/dev-login"
        case .me: return "\(p)/auth/me"
        case .billingPlans: return "\(p)/billing/plans"
        case .billingVerify: return "\(p)/billing/verify"
        case .billingRestore: return "\(p)/billing/restore"
        case .membership: return "\(p)/me/membership"
        case .addFavorite(let id), .removeFavorite(let id): return "\(p)/me/favorites/\(id)"
        case .listFavorites: return "\(p)/me/favorites"
        case .recordHistory(let id): return "\(p)/me/history/\(id)"
        case .listHistory, .clearHistory: return "\(p)/me/history"
        case .getSettings, .updateSettings: return "\(p)/me/settings"
        }
    }

    var queryItems: [URLQueryItem] {
        switch self {
        case let .dailyBrief(date, module, limit):
            var items = [URLQueryItem(name: "limit", value: String(limit))]
            if let date { items.append(URLQueryItem(name: "date", value: date)) }
            if let module { items.append(URLQueryItem(name: "module", value: module)) }
            return items
        case let .feed(cursor, module, limit):
            var items = [URLQueryItem(name: "limit", value: String(limit))]
            if let cursor { items.append(URLQueryItem(name: "cursor", value: cursor)) }
            if let module { items.append(URLQueryItem(name: "module", value: module)) }
            return items
        case let .ranking(module, limit):
            var items = [URLQueryItem(name: "limit", value: String(limit))]
            if let module { items.append(URLQueryItem(name: "module", value: module)) }
            return items
        case let .search(q, cursor, module, limit):
            var items = [
                URLQueryItem(name: "q", value: q),
                URLQueryItem(name: "limit", value: String(limit)),
            ]
            if let cursor { items.append(URLQueryItem(name: "cursor", value: cursor)) }
            if let module { items.append(URLQueryItem(name: "module", value: module)) }
            return items
        default:
            return []
        }
    }

    /// 请求体（仅 POST/PUT 带）。
    var body: Data? {
        switch self {
        case let .appleLogin(body), let .devLogin(body),
             let .billingVerify(body), let .billingRestore(body),
             let .updateSettings(body):
            return body
        default:
            return nil
        }
    }

    /// 构造完整的 URLRequest（不含鉴权头，鉴权由 APIClient 统一注入）。
    func makeRequest() -> URLRequest {
        var components = URLComponents(
            url: APIEnv.baseURL.appendingPathComponent(path),
            resolvingAgainstBaseURL: false
        )!
        if !queryItems.isEmpty {
            components.queryItems = queryItems
        }
        var request = URLRequest(url: components.url!)
        request.httpMethod = method.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }
}
