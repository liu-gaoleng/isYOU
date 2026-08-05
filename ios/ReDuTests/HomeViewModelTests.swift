//
//  HomeViewModelTests.swift
//  今日首页（2026-08-05 改版后）：只保留 TODAY 头部 + 今日热榜 TOP10。
//

import XCTest
@testable import ReDu

@MainActor
final class HomeViewModelTests: XCTestCase {

    func test_load_populatesRanking() async {
        let repo = FakeContentRepository()
        repo.rankingResult = (1...10).map { makeCard(id: $0, module: "tech") }
        let vm = HomeViewModel(repo: repo)

        await vm.load()

        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.ranking.count, 10)
        XCTAssertEqual(vm.ranking.map(\.id), Array(1...10))
    }

    func test_load_emptyRanking_setsEmpty() async {
        let repo = FakeContentRepository()
        let vm = HomeViewModel(repo: repo)

        await vm.load()

        XCTAssertEqual(vm.state, .empty)
    }

    func test_load_failure_setsFailed() async {
        let repo = FakeContentRepository()
        repo.rankingError = APIError.http(status: 500)
        let vm = HomeViewModel(repo: repo)

        await vm.load()

        if case .failed = vm.state { } else { XCTFail("应为 failed 态") }
    }

    func test_refresh_replacesRanking() async {
        let repo = FakeContentRepository()
        repo.rankingResult = [makeCard(id: 1, module: "tech")]
        let vm = HomeViewModel(repo: repo)
        await vm.load()
        XCTAssertEqual(vm.ranking.map(\.id), [1])

        repo.rankingResult = [makeCard(id: 2, module: "ai"), makeCard(id: 3, module: "ai")]
        await vm.refresh()

        XCTAssertEqual(vm.state, .loaded)
        XCTAssertEqual(vm.ranking.map(\.id), [2, 3])
    }
}
