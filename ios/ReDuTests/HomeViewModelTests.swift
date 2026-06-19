//
//  HomeViewModelTests.swift
//  2.2 首页：四模块聚合分区 + 加载更多（抬高 brief limit）。
//

import XCTest
@testable import ReDu

@MainActor
final class HomeViewModelTests: XCTestCase {

    // MARK: - 分区分组（纯函数）

    func test_group_keepsFourModuleOrder_andDropsEmpty() {
        let cards = [
            makeCard(id: 1, module: "macro"),
            makeCard(id: 2, module: "tech"),
            makeCard(id: 3, module: "ai"),
            makeCard(id: 4, module: "tech"),
        ]
        let sections = HomeViewModel.group(cards)
        // 固定顺序 tech/finance/ai/macro，finance 无卡被剔除
        XCTAssertEqual(sections.map(\.module), [.tech, .ai, .macro])
        XCTAssertEqual(sections.first?.cards.map(\.id), [2, 4])
    }

    func test_group_unknownModule_fallsBackToTech() {
        let sections = HomeViewModel.group([makeCard(id: 1, module: "weird")])
        XCTAssertEqual(sections.map(\.module), [.tech])
    }

    // MARK: - 加载

    func test_load_populatesSectionsAndRanking() async {
        let repo = FakeContentRepository()
        repo.briefByLimit = { _ in [makeCard(id: 1, module: "tech"), makeCard(id: 2, module: "ai")] }
        repo.rankingResult = [makeCard(id: 9, module: "finance")]
        let vm = HomeViewModel(repo: repo)

        await vm.load()

        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.totalCount, 2)
        XCTAssertEqual(vm.sections.map(\.module), [.tech, .ai])
        XCTAssertEqual(vm.ranking.map(\.id), [9])
    }

    func test_load_emptyBriefAndRanking_setsEmpty() async {
        let repo = FakeContentRepository()
        let vm = HomeViewModel(repo: repo)
        await vm.load()
        XCTAssertEqual(vm.state, .empty)
    }

    func test_load_failure_setsFailed() async {
        let repo = FakeContentRepository()
        repo.briefError = APIError.http(status: 500)
        let vm = HomeViewModel(repo: repo)
        await vm.load()
        if case .failed = vm.state { } else { XCTFail("应为 failed 态") }
    }

    // MARK: - 加载更多

    func test_loadMore_raisesLimitAndRegroups() async {
        let repo = FakeContentRepository()
        // 第一页 40 条全满 → hasMore；第二页给 41 条
        repo.briefByLimit = { limit in
            (0..<limit).map { makeCard(id: $0, module: $0 % 2 == 0 ? "tech" : "finance") }
        }
        let vm = HomeViewModel(repo: repo)
        await vm.load()
        XCTAssertTrue(vm.hasMore)
        XCTAssertEqual(repo.requestedBriefLimits, [40])

        await vm.loadMore()
        // limit 抬到 80，重新请求
        XCTAssertEqual(repo.requestedBriefLimits, [40, 80])
        XCTAssertEqual(vm.totalCount, 80)
    }

    func test_loadMore_stopsWhenPageNotFull() async {
        let repo = FakeContentRepository()
        // 仅返回 10 条（< 请求的 40）→ 没有更多
        repo.briefByLimit = { _ in (0..<10).map { makeCard(id: $0, module: "tech") } }
        let vm = HomeViewModel(repo: repo)
        await vm.load()
        XCTAssertFalse(vm.hasMore)

        await vm.loadMore()
        // hasMore=false 时不应再发请求
        XCTAssertEqual(repo.requestedBriefLimits, [40])
    }

    func test_loadMore_capsAtMaxBrief() async {
        let repo = FakeContentRepository()
        repo.briefByLimit = { limit in (0..<limit).map { makeCard(id: $0, module: "tech") } }
        let vm = HomeViewModel(repo: repo)
        await vm.load()                 // limit 40
        await vm.loadMore()             // 80
        await vm.loadMore()             // 100（上限）
        XCTAssertEqual(repo.requestedBriefLimits, [40, 80, 100])
        XCTAssertFalse(vm.hasMore)      // 到上限即停

        await vm.loadMore()             // 不再请求
        XCTAssertEqual(repo.requestedBriefLimits, [40, 80, 100])
    }
}
