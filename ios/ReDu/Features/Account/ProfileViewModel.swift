//
//  ProfileViewModel.swift
//  「我的」页 VM：登录态下加载收藏 / 历史 / 推送设置。
//

import Foundation

@MainActor
final class ProfileViewModel: ObservableObject {
    @Published var favorites: [FavoriteCard] = []
    @Published var history: [HistoryCard] = []
    @Published var settings: PushSettings = .default

    @Published var favoritesState: LoadState = .idle
    @Published var historyState: LoadState = .idle
    @Published var settingsLoaded = false
    /// 设置写入中，避免并发抖动。
    @Published var savingSettings = false

    private let repo: AuthRepositoryProtocol

    init(repo: AuthRepositoryProtocol = AuthRepository.shared) {
        self.repo = repo
    }

    func loadFavorites() async {
        favoritesState = .loading
        do {
            favorites = try await repo.listFavorites()
            favoritesState = favorites.isEmpty ? .empty : .loaded
        } catch {
            favoritesState = .failed((error as? APIError)?.errorDescription ?? "加载失败")
        }
    }

    func loadHistory() async {
        historyState = .loading
        do {
            history = try await repo.listHistory()
            historyState = history.isEmpty ? .empty : .loaded
        } catch {
            historyState = .failed((error as? APIError)?.errorDescription ?? "加载失败")
        }
    }

    func clearHistory() async {
        do {
            try await repo.clearHistory()
            history = []
            historyState = .empty
        } catch {
            // 清空失败保持原列表，下次重试。
        }
    }

    func loadSettings() async {
        do {
            settings = try await repo.getSettings()
            settingsLoaded = true
        } catch {
            // 读取失败用默认值兜底，仍可展示。
            settingsLoaded = true
        }
    }

    /// 更新推送设置：乐观更新 + 失败回滚。
    func updateSettings(dailyPush: Bool? = nil, pushTime: String? = nil, breakingPush: Bool? = nil) async {
        let previous = settings
        if let dailyPush { settings.dailyPush = dailyPush }
        if let pushTime { settings.pushTime = pushTime }
        if let breakingPush { settings.breakingPush = breakingPush }

        savingSettings = true
        defer { savingSettings = false }
        do {
            settings = try await repo.updateSettings(
                dailyPush: dailyPush, pushTime: pushTime, breakingPush: breakingPush
            )
        } catch {
            settings = previous
        }
    }
}
