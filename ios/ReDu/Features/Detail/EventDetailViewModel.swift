//
//  EventDetailViewModel.swift
//  事件详情 VM：拉取结构化正文 + 信源列表 + 付费深度解读；
//  登录态下记录阅读历史、维护收藏状态。
//

import Foundation

@MainActor
final class EventDetailViewModel: ObservableObject {
    @Published var state: LoadState = .idle
    @Published var detail: EventDetail?
    @Published var isFavorited = false
    /// 收藏切换进行中，防抖。
    @Published var favoriteToggling = false

    private let repo: ContentRepositoryProtocol
    private let authRepo: AuthRepositoryProtocol

    init(repo: ContentRepositoryProtocol = ContentRepository.shared,
         authRepo: AuthRepositoryProtocol = AuthRepository.shared) {
        self.repo = repo
        self.authRepo = authRepo
    }

    /// 加载详情；登录态下同时记录阅读历史并同步收藏态。
    func load(id: Int, isAuthenticated: Bool) async {
        state = .loading
        do {
            detail = try await repo.eventDetail(id: id)
            state = .loaded
            if isAuthenticated {
                await recordHistory(id: id)
                await syncFavorite(id: id)
            }
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败")
        }
    }

    /// 切换收藏（仅登录态可用）。
    func toggleFavorite(id: Int) async {
        guard !favoriteToggling else { return }
        favoriteToggling = true
        defer { favoriteToggling = false }
        do {
            let result: FavoriteState
            if isFavorited {
                result = try await authRepo.removeFavorite(eventID: id)
            } else {
                result = try await authRepo.addFavorite(eventID: id)
            }
            isFavorited = result.isFavorited
        } catch {
            // 失败保持原状态，下次重试。
        }
    }

    private func recordHistory(id: Int) async {
        try? await authRepo.recordHistory(eventID: id)
    }

    private func syncFavorite(id: Int) async {
        guard let favorites = try? await authRepo.listFavorites() else { return }
        isFavorited = favorites.contains { $0.id == id }
    }
}
