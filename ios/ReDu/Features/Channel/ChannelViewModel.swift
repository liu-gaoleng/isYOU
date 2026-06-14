//
//  ChannelViewModel.swift
//  频道页 VM：按模块拉 TOP10 热榜 + 信息流（cursor 分页加载更多）。
//  记住上次选中的频道（UserDefaults）。
//

import Foundation

@MainActor
final class ChannelViewModel: ObservableObject {
    @Published var selected: ContentModule {
        didSet { persistSelection() }
    }
    @Published var state: LoadState = .idle
    @Published var ranking: [EventCard] = []
    @Published var cards: [EventCard] = []
    @Published var isLoadingMore = false

    private let repo: ContentRepositoryProtocol
    private let pageSize = 20
    private var cursor: String?
    private var hasMore = true
    private static let selectionKey = "channel.last_selected_module"

    init(repo: ContentRepositoryProtocol = ContentRepository.shared) {
        self.repo = repo
        let saved = UserDefaults.standard.string(forKey: Self.selectionKey)
        self.selected = saved.flatMap(ContentModule.init(rawValue:)) ?? .tech
    }

    private func persistSelection() {
        UserDefaults.standard.set(selected.rawValue, forKey: Self.selectionKey)
    }

    /// 切换频道：清空旧数据并重载。
    func switchTo(_ module: ContentModule) async {
        guard module != selected else { return }
        selected = module
        await load()
    }

    func load() async {
        state = .loading
        cursor = nil
        hasMore = true
        do {
            async let rankTask = repo.ranking(module: selected, limit: 10)
            async let feedTask = repo.feed(cursor: nil, module: selected, limit: pageSize)
            let (rankResult, feedResult) = try await (rankTask, feedTask)
            ranking = rankResult
            cards = feedResult.items
            cursor = feedResult.nextCursor
            hasMore = feedResult.nextCursor != nil
            state = (rankResult.isEmpty && feedResult.items.isEmpty) ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败，请稍后重试")
        }
    }

    func refresh() async {
        cursor = nil
        hasMore = true
        do {
            async let rankTask = repo.ranking(module: selected, limit: 10)
            async let feedTask = repo.feed(cursor: nil, module: selected, limit: pageSize)
            let (rankResult, feedResult) = try await (rankTask, feedTask)
            ranking = rankResult
            cards = feedResult.items
            cursor = feedResult.nextCursor
            hasMore = feedResult.nextCursor != nil
            state = (rankResult.isEmpty && feedResult.items.isEmpty) ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "刷新失败")
        }
    }

    /// 加载更多：滚到底部触发，cursor 取尽即停。
    func loadMoreIfNeeded(current card: EventCard) async {
        guard hasMore, !isLoadingMore else { return }
        // 接近末尾（倒数第 3 个）时预取
        guard let idx = cards.firstIndex(of: card), idx >= cards.count - 3 else { return }
        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let page = try await repo.feed(cursor: cursor, module: selected, limit: pageSize)
            cards.append(contentsOf: page.items)
            cursor = page.nextCursor
            hasMore = page.nextCursor != nil
        } catch {
            // 加载更多失败不打断已有列表，仅停止继续翻页
            hasMore = false
        }
    }
}
