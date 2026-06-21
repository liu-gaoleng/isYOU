//
//  AnalyticsTrackerTests.swift
//  阶段 4.3：自建埋点 buffer / flushThreshold / flushNow / 失败重试 / maxBufferSize 行为覆盖。
//
//  这里只覆盖 tracker 自身行为；服务端入库由后端 test_analytics_api.py 覆盖，
//  HTTP 链路（Endpoint/APIClient）由 APIClientTests 覆盖，三层隔离测试避免耦合。
//

import XCTest
@testable import ReDu

@MainActor
final class AnalyticsTrackerTests: XCTestCase {

    // MARK: - FakeUploader：拦截上送并可注入错误

    final class FakeUploader: AnalyticsUploading {
        var batches: [[AnalyticsEventPayload]] = []
        /// 调用次数，与 batches.count 相等（含失败的）。
        var calls: Int { batches.count }
        var errorToThrow: Error?

        func upload(_ events: [AnalyticsEventPayload]) async throws {
            batches.append(events)
            if let err = errorToThrow {
                throw err
            }
        }
    }

    private enum TestError: Error { case network }

    // MARK: - 工厂：装配一个隔离的 tracker（不动 shared）

    private func makeTracker(
        uploader: AnalyticsUploading,
        flushThreshold: Int = 10,
        maxBufferSize: Int = 200
    ) -> AnalyticsTracker {
        AnalyticsTracker(
            uploader: uploader,
            deviceIDStore: InMemoryDeviceIDStore(initial: "test-device-id"),
            flushThreshold: flushThreshold,
            maxBufferSize: maxBufferSize,
            appVersion: "1.0.0",
            osVersion: "17.0"
        )
    }

    // MARK: - buffer 行为

    func test_track_belowThreshold_doesNotFlush() async {
        let fake = FakeUploader()
        let tracker = makeTracker(uploader: fake, flushThreshold: 5)

        tracker.track(.appOpen)
        tracker.track(.eventView, props: ["event_id": AnyCodable(1)])

        // 给可能的异步 flush 一个机会执行
        await Task.yield()

        XCTAssertEqual(fake.calls, 0)
        XCTAssertEqual(tracker.bufferedCount, 2)
    }

    func test_track_reachingThreshold_autoFlushes() async {
        let fake = FakeUploader()
        let tracker = makeTracker(uploader: fake, flushThreshold: 3)

        tracker.track(.appOpen)
        tracker.track(.eventView, props: ["event_id": AnyCodable(1)])
        tracker.track(.share, props: ["event_id": AnyCodable(1)])

        // 等到 inFlight 内的 Task 跑完
        for _ in 0..<10 {
            await Task.yield()
            if fake.calls > 0 { break }
        }

        XCTAssertEqual(fake.calls, 1)
        XCTAssertEqual(fake.batches.first?.count, 3)
        XCTAssertEqual(tracker.bufferedCount, 0)
    }

    // MARK: - flushNow

    func test_flushNow_manuallySendsAllBuffered() async {
        let fake = FakeUploader()
        let tracker = makeTracker(uploader: fake, flushThreshold: 100)

        tracker.track(.appOpen)
        tracker.track(.paywallView)
        await tracker.flushNow()

        XCTAssertEqual(fake.calls, 1)
        XCTAssertEqual(fake.batches.first?.count, 2)
        XCTAssertEqual(tracker.bufferedCount, 0)
    }

    func test_flushNow_emptyBuffer_isNoop() async {
        let fake = FakeUploader()
        let tracker = makeTracker(uploader: fake, flushThreshold: 100)

        await tracker.flushNow()

        XCTAssertEqual(fake.calls, 0)
    }

    // MARK: - 失败重试

    func test_flushNow_failure_preservesBatchForRetry() async {
        let fake = FakeUploader()
        fake.errorToThrow = TestError.network
        let tracker = makeTracker(uploader: fake, flushThreshold: 100)

        tracker.track(.appOpen)
        tracker.track(.eventView, props: ["event_id": AnyCodable(42)])
        await tracker.flushNow()

        // 失败保留：批次回到队首，buffer 仍有 2 条
        XCTAssertEqual(fake.calls, 1)
        XCTAssertEqual(tracker.bufferedCount, 2)

        // 下次成功后清空
        fake.errorToThrow = nil
        await tracker.flushNow()
        XCTAssertEqual(fake.calls, 2)
        XCTAssertEqual(tracker.bufferedCount, 0)
    }

    // MARK: - maxBufferSize 溢出丢最早

    func test_maxBufferSize_dropsOldestOnOverflow() async {
        let fake = FakeUploader()
        // flushThreshold 故意调到比 cap 大，避免自动 flush 影响
        let tracker = makeTracker(uploader: fake, flushThreshold: 999, maxBufferSize: 3)

        tracker.track(.appOpen)                                          // 0
        tracker.track(.eventView, props: ["event_id": AnyCodable(1)])    // 1
        tracker.track(.eventView, props: ["event_id": AnyCodable(2)])    // 2
        tracker.track(.eventView, props: ["event_id": AnyCodable(3)])    // 3 → 超额，丢 appOpen
        tracker.track(.eventView, props: ["event_id": AnyCodable(4)])    // 4 → 再丢 event_id=1

        XCTAssertEqual(tracker.bufferedCount, 3)
        await tracker.flushNow()
        let names = fake.batches.first?.map(\.name) ?? []
        // 最早两条已被丢掉，剩 event_view * 3
        XCTAssertEqual(names, ["event_view", "event_view", "event_view"])
    }

    // MARK: - payload 字段完整性

    func test_track_buildsCompletePayload() async {
        let fake = FakeUploader()
        let tracker = makeTracker(uploader: fake, flushThreshold: 100)

        tracker.track(.favorite, props: [
            "event_id": AnyCodable(99),
            "action": AnyCodable("add"),
        ])
        await tracker.flushNow()

        guard let payload = fake.batches.first?.first else {
            return XCTFail("无上送 payload")
        }
        XCTAssertEqual(payload.name, "favorite")
        XCTAssertEqual(payload.deviceID, "test-device-id")
        XCTAssertEqual(payload.appVersion, "1.0.0")
        XCTAssertEqual(payload.osVersion, "17.0")
        XCTAssertEqual(payload.platform, "ios")
        XCTAssertGreaterThan(payload.tsClient, 0)
        XCTAssertEqual(payload.props?["event_id"], AnyCodable(99))
        XCTAssertEqual(payload.props?["action"], AnyCodable("add"))
    }
}
