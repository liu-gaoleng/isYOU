//
//  HomeViewModel.swift
//  今日首页 VM：TODAY 头部 + 今日热榜 TOP10（四模块综合评估）。
//
//  产品决策（2026-08-05）：首页只保留 TODAY + 今日热榜；原「今日聚合」分区
//  与频道页内容耦合，已下线——深度浏览统一走频道页 feed 分页。
//

import Foundation

@MainActor
final class HomeViewModel: ObservableObject {
    @Published var state: LoadState = .idle
    /// 今日热榜：全站综合 TOP10（module=nil 即四模块混合评估榜）。
    @Published var ranking: [EventCard] = []

    private let repo: ContentRepositoryProtocol

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
            ranking = try await repo.ranking(module: nil, limit: 10)
            state = ranking.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败，请稍后重试")
        }
    }

    /// 下拉刷新：不切 loading 态，避免界面闪烁。
    func refresh() async {
        do {
            ranking = try await repo.ranking(module: nil, limit: 10)
            state = ranking.isEmpty ? .empty : .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "刷新失败")
        }
    }
}
