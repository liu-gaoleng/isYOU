//
//  EventDetailViewModel.swift
//  事件详情 VM：拉取结构化正文 + 信源列表。
//

import Foundation

@MainActor
final class EventDetailViewModel: ObservableObject {
    @Published var state: LoadState = .idle
    @Published var detail: EventDetail?

    private let repo: ContentRepositoryProtocol

    init(repo: ContentRepositoryProtocol = ContentRepository.shared) {
        self.repo = repo
    }

    func load(id: Int) async {
        state = .loading
        do {
            detail = try await repo.eventDetail(id: id)
            state = .loaded
        } catch {
            state = .failed((error as? APIError)?.errorDescription ?? "加载失败")
        }
    }
}
