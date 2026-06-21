//
//  AnalyticsTracker.swift
//  阶段 4.3：自建埋点上报。
//
//  设计取舍：
//  - **批量缓冲**：内存 buffer 攒到 ``flushThreshold`` 或 ``flushInterval`` 触发一次 POST，
//    控制网络请求频率与电量。
//  - **失败保留**：网络/HTTP 失败时把整批 buffer 放回队首（最多保留 ``maxBufferSize`` 条），
//    下次 flush 继续重试；超过则丢最早的，避免 OOM。
//  - **静默失败**：埋点不能影响主业务，所有错误吞掉只 print，不抛给上层。
//  - **不强制鉴权**：``app_open`` 等漏斗起点事件必须无登录也能埋；登录时 APIClient 会自动
//    带上 Bearer，服务端写入 ``user_id``。
//
//  注：iOS 16+ 用 ``Task.sleep(for:)``；测试时通过 ``flushNow()`` 手动触发，
//  避免依赖真实时钟。
//

import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// 上送策略协议，便于测试时注入 FakeUploader 拦截请求。
protocol AnalyticsUploading {
    /// 上送一批事件；抛错视为失败，由 tracker 重试。
    func upload(_ events: [AnalyticsEventPayload]) async throws
}

/// 默认实现：走 APIClient → POST /api/v1/analytics/events。
final class APIAnalyticsUploader: AnalyticsUploading {
    private let client: APIClientProtocol
    private let encoder: JSONEncoder

    init(client: APIClientProtocol = APIClient.shared) {
        self.client = client
        self.encoder = JSONEncoder()
    }

    func upload(_ events: [AnalyticsEventPayload]) async throws {
        struct Body: Encodable { let events: [AnalyticsEventPayload] }
        let data = try encoder.encode(Body(events: events))
        try await client.send(.analyticsEvents(body: data))
    }
}

@MainActor
final class AnalyticsTracker {
    static let shared = AnalyticsTracker()

    /// 触发 flush 的事件条数阈值。
    let flushThreshold: Int
    /// 失败保留时的最大 buffer 容量（超过丢最早）。
    let maxBufferSize: Int

    private var buffer: [AnalyticsEventPayload] = []
    private var inFlight = false
    private let uploader: AnalyticsUploading
    private let deviceIDStore: DeviceIDStoring
    private let appVersion: String
    private let osVersion: String

    init(
        uploader: AnalyticsUploading = APIAnalyticsUploader(),
        deviceIDStore: DeviceIDStoring = DeviceIDStore.shared,
        flushThreshold: Int = 10,
        maxBufferSize: Int = 200,
        appVersion: String = AnalyticsTracker.bundleVersion,
        osVersion: String = AnalyticsTracker.systemVersion
    ) {
        self.uploader = uploader
        self.deviceIDStore = deviceIDStore
        self.flushThreshold = flushThreshold
        self.maxBufferSize = maxBufferSize
        self.appVersion = appVersion
        self.osVersion = osVersion
    }

    // MARK: - 公共 API

    /// 入队一条事件。达到 ``flushThreshold`` 自动 flush。
    func track(_ name: AnalyticsEventName, props: [String: AnyCodable]? = nil) {
        let payload = AnalyticsEventPayload(
            name: name.rawValue,
            deviceID: deviceIDStore.deviceID(),
            appVersion: appVersion,
            osVersion: osVersion,
            platform: "ios",
            tsClient: Self.nowMillis(),
            props: props
        )
        buffer.append(payload)
        trimIfNeeded()
        if buffer.count >= flushThreshold {
            Task { await flushNow() }
        }
    }

    /// 立即 flush 当前缓冲；典型场景：App 进入后台、Tab 切换、登出。
    func flushNow() async {
        guard !inFlight, !buffer.isEmpty else { return }
        let batch = buffer
        buffer.removeAll(keepingCapacity: true)
        inFlight = true
        defer { inFlight = false }
        do {
            try await uploader.upload(batch)
        } catch {
            // 失败保留：把 batch 放回队首，下次再试。
            buffer.insert(contentsOf: batch, at: 0)
            trimIfNeeded()
            print("[analytics] flush 失败，已保留 \(batch.count) 条待重试: \(error)")
        }
    }

    /// 内部测试用：当前 buffer 大小。
    var bufferedCount: Int { buffer.count }

    // MARK: - 内部辅助

    private func trimIfNeeded() {
        if buffer.count > maxBufferSize {
            let overflow = buffer.count - maxBufferSize
            buffer.removeFirst(overflow)
        }
    }

    private static func nowMillis() -> Int64 {
        Int64(Date().timeIntervalSince1970 * 1000)
    }

    static var bundleVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? ""
    }

    static var systemVersion: String {
        #if canImport(UIKit)
        return UIDevice.current.systemVersion
        #else
        return ""
        #endif
    }
}
