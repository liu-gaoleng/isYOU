//
//  HomeViewModel.swift
//  今日简报首页 VM：拉取当日简报卡片流 + 全站热榜 TOP10。
//

import Foundation

@MainActor
final class HomeViewModel: ObservableObject {
    @Published var state: LoadState = .idle
    @Published var brief: [EventCard] = []
    @Published var ranking: [EventCard] = []

    private let repo: ContentRepositoryProtocol
    private let pageSize = 20

    init(repo: ContentRepositoryProtocol = ContentRepository.shared) {
        self.repo = repo
    }

    /// slogan 文案（首期静态；后续可由后端下发）。
    let slogan = "10 分钟，读懂今天的科技、金融、AI 与宏观。"

    var dateTitle: String { DateText.headerTitle() }

    func load() async {
        if case .loading = state { return }
        state = .loading
        do {
            // 并发拉简报 + 热榜
            async let briefTask = repo.dailyBrief(date: nil, module: nil, limit: pageSize)
            async let rankTask = repo.ranking(module: nil, limit: 10)
            let (briefResult, rankResult) = try await (briefTask, rankTask)
            brief = briefResult
            ranking = rankResult
            state = briefResult.isEmpty && rankResult.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败，请稍后重试")
        }
    }

    /// 下拉刷新：不切 loading 态，避免界面闪烁。
    func refresh() async {
        do {
            async let briefTask = repo.dailyBrief(date: nil, module: nil, limit: pageSize)
            async let rankTask = repo.ranking(module: nil, limit: 10)
            let (briefResult, rankResult) = try await (briefTask, rankTask)
            brief = briefResult
            ranking = rankResult
            state = briefResult.isEmpty && rankResult.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "刷新失败")
        }
    }
}
