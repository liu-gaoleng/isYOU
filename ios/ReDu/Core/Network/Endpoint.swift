//
//  Endpoint.swift
//  接口定义：与后端 content_engine/api/routers/brief.py 对齐。
//  前缀 /api/v1，只读接口。
//

import Foundation

/// 后端环境配置。模拟器默认连本机 8000 端口的 FastAPI。
enum APIEnv {
    /// 后端基址。真机联调时改成局域网 IP 或线上域名。
    static let baseURL = URL(string: "http://127.0.0.1:8000")!
    /// API 版本前缀。
    static let prefix = "/api/v1"
}

/// HTTP 方法（当前只读，仅 GET）。
enum HTTPMethod: String {
    case get = "GET"
}

/// 端点定义：把 path + query 收敛成可构造 URLRequest 的工厂。
enum Endpoint {
    /// 当日简报：按 importance 倒序。
    case dailyBrief(date: String?, module: String?, limit: Int)
    /// 信息流分页：cursor 游标。
    case feed(cursor: String?, module: String?, limit: Int)
    /// 热度榜 TOP-N。
    case ranking(module: String?, limit: Int)
    /// 事件详情。
    case eventDetail(id: Int)

    var method: HTTPMethod { .get }

    var path: String {
        switch self {
        case .dailyBrief: return "\(APIEnv.prefix)/daily-brief"
        case .feed: return "\(APIEnv.prefix)/feed"
        case .ranking: return "\(APIEnv.prefix)/ranking"
        case .eventDetail(let id): return "\(APIEnv.prefix)/event/\(id)"
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
        case .eventDetail:
            return []
        }
    }

    /// 构造完整的 URLRequest。
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
        return request
    }
}
