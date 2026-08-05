//
//  InsightViewModelTests.swift
//  热点透视：状态机 + 轮询推进 + 门禁映射 + 取消。
//
//  轮询间隔与超时上限均为 init 注入（.zero / 短超时），不依赖真实时钟；
//  埋点断言用独立 tracker 实例 + 本地 uploader（不打 .shared 单例，避免跨测试污染）。
//

import XCTest
@testable import ReDu

@MainActor
final class InsightViewModelTests: XCTestCase {

    /// 本地埋点拦截器（避免与其它测试文件的 fake 重名/共享单例）。
    final class InsightTestUploader: AnalyticsUploading {
        var events: [AnalyticsEventPayload] = []
        func upload(_ batch: [AnalyticsEventPayload]) async throws {
            events.append(contentsOf: batch)
        }
    }

    private func makeVM(
        repo: FakeContentRepository,
        maxPollDuration: Duration = .seconds(5)
    ) -> (vm: InsightViewModel, uploader: InsightTestUploader, tracker: AnalyticsTracker) {
        let uploader = InsightTestUploader()
        let tracker = AnalyticsTracker(uploader: uploader, deviceIDStore: DeviceIDStore.shared)
        let vm = InsightViewModel(
            repo: repo,
            tracker: tracker,
            pollInterval: .zero,
            maxPollDuration: maxPollDuration
        )
        return (vm, uploader, tracker)
    }

    // MARK: - load（进页恢复）

    func test_load_none_mapsToIdle() async {
        let repo = FakeContentRepository()
        repo.insightResults = [.success(makeInsight(status: "none"))]
        let (vm, _, _) = makeVM(repo: repo)
        await vm.load(eventID: 7)
        XCTAssertEqual(vm.state, .idle)
    }

    func test_load_ready_decodesSections() async {
        let repo = FakeContentRepository()
        repo.insightResults = [.success(makeInsight(status: "ready"))]
        let (vm, _, _) = makeVM(repo: repo)
        await vm.load(eventID: 7)
        guard case .ready(let insight) = vm.state else {
            return XCTFail("期望 ready，实际 \(vm.state)")
        }
        XCTAssertEqual(insight.sections?.history, "来龙去脉正文")
        XCTAssertEqual(insight.sections?.forecast, "趋势推演正文")
        XCTAssertEqual(insight.disclaimer, "免责声明")
        XCTAssertNotNil(insight.generatedAt)  // snake_case + 日期解码
    }

    func test_load_generating_pollsUntilReady() async {
        let repo = FakeContentRepository()
        // 第一次（load）generating → 轮询第二次 ready
        repo.insightResults = [
            .success(makeInsight(status: "generating")),
            .success(makeInsight(status: "ready")),
        ]
        let (vm, _, _) = makeVM(repo: repo)
        await vm.load(eventID: 7)
        // 轮询是后台 Task：等它推进（pollInterval=.zero，最多 5s 超时上限）
        try? await Task.sleep(for: .milliseconds(50))
        guard case .ready = vm.state else {
            return XCTFail("期望轮询推进到 ready，实际 \(vm.state)")
        }
        XCTAssertEqual(repo.insightCallCount, 2)
    }

    // MARK: - generate（触发）

    func test_generate_postsOnceThenPollsToReady() async {
        let repo = FakeContentRepository()
        repo.triggerResult = .success(makeInsight(status: "pending"))
        repo.insightResults = [.success(makeInsight(status: "ready"))]
        let (vm, _, _) = makeVM(repo: repo)
        await vm.generate(eventID: 7)
        try? await Task.sleep(for: .milliseconds(50))
        XCTAssertEqual(repo.triggerCallCount, 1)
        guard case .ready = vm.state else {
            return XCTFail("期望 ready，实际 \(vm.state)")
        }
    }

    func test_generate_unauthorized_mapsLoginRequired() async {
        let repo = FakeContentRepository()
        repo.triggerResult = .failure(APIError.unauthorized)
        let (vm, _, _) = makeVM(repo: repo)
        await vm.generate(eventID: 7)
        XCTAssertEqual(vm.state, .loginRequired)
    }

    func test_generate_forbidden_mapsMemberRequired() async {
        let repo = FakeContentRepository()
        repo.triggerResult = .failure(APIError.http(status: 403))
        let (vm, _, _) = makeVM(repo: repo)
        await vm.generate(eventID: 7)
        XCTAssertEqual(vm.state, .memberRequired)
    }

    // MARK: - 超时 / 取消 / 重试

    func test_pollTimeout_mapsFailed() async {
        let repo = FakeContentRepository()
        // 恒 generating：耗尽后抛错，VM continue，直到超时上限
        repo.insightResults = [.success(makeInsight(status: "generating"))]
        let (vm, _, _) = makeVM(repo: repo, maxPollDuration: .milliseconds(30))
        await vm.load(eventID: 7)
        try? await Task.sleep(for: .milliseconds(120))
        guard case .failed(let msg) = vm.state else {
            return XCTFail("期望超时 failed，实际 \(vm.state)")
        }
        XCTAssertTrue(msg.contains("超时"))
    }

    func test_cancelPolling_stopsFurtherCalls() async {
        let repo = FakeContentRepository()
        repo.insightResults = [.success(makeInsight(status: "generating"))]
        let (vm, _, _) = makeVM(repo: repo, maxPollDuration: .seconds(10))
        await vm.load(eventID: 7)
        try? await Task.sleep(for: .milliseconds(30))
        vm.cancelPolling()
        let calls = repo.insightCallCount
        try? await Task.sleep(for: .milliseconds(60))
        // 取消瞬间至多有一个已在途的调用完成（异步取消的固有语义：in-flight 不可撤回）
        XCTAssertLessThanOrEqual(
            repo.insightCallCount - calls, 1, "取消后至多完成一个在途调用，不应再发起新调用"
        )
    }

    func test_retryAfterFailed_reGenerates() async {
        let repo = FakeContentRepository()
        repo.insightResults = [.success(makeInsight(status: "failed", error: "生成失败"))]
        repo.triggerResult = .success(makeInsight(status: "pending"))
        let (vm, _, _) = makeVM(repo: repo)
        await vm.load(eventID: 7)
        guard case .failed = vm.state else { return XCTFail("期望 failed") }
        await vm.generate(eventID: 7)
        XCTAssertEqual(repo.triggerCallCount, 1)
        guard case .generating = vm.state else {
            return XCTFail("重试后应进入 generating，实际 \(vm.state)")
        }
    }

    // MARK: - 埋点

    func test_ready_tracksInsightView() async {
        let repo = FakeContentRepository()
        repo.insightResults = [.success(makeInsight(status: "ready"))]
        let (vm, uploader, tracker) = makeVM(repo: repo)
        await vm.load(eventID: 7)
        // track 只入 buffer；flush 后 uploader 应收到 insight_view（带 event_id）
        await tracker.flushNow()
        let names = uploader.events.map(\.name)
        XCTAssertTrue(names.contains("insight_view"), "实际事件：\(names)")
        let event = uploader.events.first { $0.name == "insight_view" }
        XCTAssertEqual(event?.props?["event_id"], AnyCodable(7))
    }
}
