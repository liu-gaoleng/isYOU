//
//  HomeViewModel.swift
//  今日简报首页 VM：当日简报按四模块聚合分区 + 全站热榜 TOP10 + 加载更多。
//

import Foundation

/// 首页「四模块聚合」分区：一个模块 + 该模块当日卡片（按 importance 倒序）。
struct BriefSection: Identifiable {
    let module: ContentModule
    let cards: [EventCard]
    var id: String { module.rawValue }
}

@MainActor
final class HomeViewModel: ObservableObject {
    @Published var state: LoadState = .idle
    @Published var sections: [BriefSection] = []
    @Published var ranking: [EventCard] = []
    @Published var isLoadingMore = false
    @Published var hasMore = true

    private let repo: ContentRepositoryProtocol
    private let pageSize = 40
    /// daily-brief 不支持 cursor，仅按 importance + limit；加载更多通过逐步抬高 limit 揭示当日更多事件。
    /// 后端 limit 上限 100，到顶即停（深度浏览走频道页 feed 分页）。
    private let maxBrief = 100
    private var briefLimit = 40
    private var allCards: [EventCard] = []

    init(repo: ContentRepositoryProtocol = ContentRepository.shared) {
        self.repo = repo
    }

    /// slogan 文案（首期静态；后续可由后端下发）。
    let slogan = "10 分钟，读懂今天的科技、金融、AI 与宏观。"

    var dateTitle: String { DateText.headerTitle() }

    /// 当日聚合总条数（供"今日聚合 · N 条"展示）。
    var totalCount: Int { allCards.count }

    func load() async {
        if case .loading = state { return }
        state = .loading
        briefLimit = pageSize
        do {
            async let briefTask = repo.dailyBrief(date: nil, module: nil, limit: briefLimit)
            async let rankTask = repo.ranking(module: nil, limit: 10)
            let (briefResult, rankResult) = try await (briefTask, rankTask)
            apply(brief: briefResult, requested: briefLimit)
            ranking = rankResult
            state = allCards.isEmpty && rankResult.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败，请稍后重试")
        }
    }

    /// 下拉刷新：不切 loading 态，避免界面闪烁。
    func refresh() async {
        briefLimit = pageSize
        do {
            async let briefTask = repo.dailyBrief(date: nil, module: nil, limit: briefLimit)
            async let rankTask = repo.ranking(module: nil, limit: 10)
            let (briefResult, rankResult) = try await (briefTask, rankTask)
            apply(brief: briefResult, requested: briefLimit)
            ranking = rankResult
            state = allCards.isEmpty && rankResult.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "刷新失败")
        }
    }

    /// 加载更多：抬高 brief limit 重取并重新分区；取满一页且未达上限才允许继续。
    func loadMore() async {
        guard hasMore, !isLoadingMore, briefLimit < maxBrief else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }
        let next = min(briefLimit + pageSize, maxBrief)
        do {
            let briefResult = try await repo.dailyBrief(date: nil, module: nil, limit: next)
            briefLimit = next
            apply(brief: briefResult, requested: next)
        } catch {
            // 加载更多失败不打断已有列表，仅停止继续翻页
            hasMore = false
        }
    }

    /// 落地一批 brief：缓存全量、按四模块分区、更新 hasMore。
    private func apply(brief: [EventCard], requested: Int) {
        allCards = brief
        sections = Self.group(brief)
        // 取满一页说明可能还有；到 limit 上限则停止。
        hasMore = brief.count >= requested && requested < maxBrief
    }

    /// 按四模块固定顺序分区（tech/finance/ai/macro），各区内沿用后端 importance 倒序；空区不显示。
    static func group(_ cards: [EventCard]) -> [BriefSection] {
        ContentModule.allCases.compactMap { module in
            let items = cards.filter { ContentModule.parse($0.module) == module }
            return items.isEmpty ? nil : BriefSection(module: module, cards: items)
        }
    }
}
