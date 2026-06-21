//
//  AnalyticsEvent.swift
//  阶段 4.3：埋点事件常量 + 上送 payload。
//
//  与后端 schemas.ANALYTICS_EVENT_NAMES 严格对齐：任一字符串不一致都会被服务端 422 整批拒收。
//

import Foundation

/// 埋点事件名常量。集中一处，避免分散字符串拼写漂移。
enum AnalyticsEventName: String {
    /// 启动/前台恢复（漏斗起点）。
    case appOpen = "app_open"
    /// 进入事件详情（漏斗：阅读）。
    case eventView = "event_view"
    /// 付费墙曝光（漏斗：付费触达）。
    case paywallView = "paywall_view"
    /// 购买成功（漏斗：付费）。
    case purchaseSuccess = "purchase_success"
    /// APNs 推送被点击（漏斗：推送打开）。
    case pushOpen = "push_open"
    /// 搜索发起。
    case search
    /// 收藏（add/remove 都埋）。
    case favorite
    /// 分享。
    case share
}

/// 上送时的单条 JSON payload，蛇形字段与后端 ``AnalyticsEventIn`` 对齐。
struct AnalyticsEventPayload: Codable, Equatable {
    let name: String
    let deviceID: String
    let appVersion: String
    let osVersion: String
    let platform: String
    let tsClient: Int64
    let props: [String: AnyCodable]?

    enum CodingKeys: String, CodingKey {
        case name
        case deviceID = "device_id"
        case appVersion = "app_version"
        case osVersion = "os_version"
        case platform
        case tsClient = "ts_client"
        case props
    }
}

/// 仅 props 用：String/Int/Double/Bool 四种标量；不允许嵌套结构，
/// 强制约束 payload 体积可控、服务端 JSON 列查询稳定。
struct AnyCodable: Codable, Equatable {
    enum Value: Equatable {
        case string(String)
        case int(Int)
        case double(Double)
        case bool(Bool)
    }
    let value: Value

    init(_ s: String) { value = .string(s) }
    init(_ i: Int) { value = .int(i) }
    init(_ d: Double) { value = .double(d) }
    init(_ b: Bool) { value = .bool(b) }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch value {
        case .string(let s): try c.encode(s)
        case .int(let i): try c.encode(i)
        case .double(let d): try c.encode(d)
        case .bool(let b): try c.encode(b)
        }
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let i = try? c.decode(Int.self) { value = .int(i); return }
        if let d = try? c.decode(Double.self) { value = .double(d); return }
        if let b = try? c.decode(Bool.self) { value = .bool(b); return }
        if let s = try? c.decode(String.self) { value = .string(s); return }
        throw DecodingError.dataCorruptedError(
            in: c, debugDescription: "unsupported analytics prop value"
        )
    }
}
