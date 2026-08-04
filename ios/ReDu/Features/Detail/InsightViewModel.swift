//
//  InsightViewModel.swift
//  热点透视：状态机 + 服务端任务轮询。
//
//  设计要点：
//  - 生成是服务端异步任务（10–60s）：POST 触发后按 ``pollInterval`` 轮询 GET，
//    直到 ready / failed / 超时上限 ``maxPollDuration``；
//  - 单次网络抖动不终止轮询（超时上限兜底）；页面消失必须 ``cancelPolling()``；
//  - 门禁不在 VM 里弹 UI：401/403 映射为 loginRequired / memberRequired，
//    由 View 复用详情页已有的登录 / 付费墙 sheet；
//  - 轮询间隔与超时上限均为 init 注入参数：测试传 .zero / 短超时，不依赖真实时钟。
//

import Foundation

@MainActor
final class InsightViewModel: ObservableObject {

    enum State: Equatable {
        /// 入口卡（含服务端 none：从未生成过）。
        case idle
        /// pending / generating 合并渲染。
        case generating
        case ready(EventInsight)
        /// 可重试的失败（含超时）。
        case failed(String)
        /// 401：未登录。
        case loginRequired
        /// 403：已登录但非会员。
        case memberRequired
    }

    @Published private(set) var state: State = .idle

    private let repo: ContentRepositoryProtocol
    private let tracker: AnalyticsTracker
    private let pollInterval: Duration
    private let maxPollDuration: Duration
    private var pollTask: Task<Void, Never>?

    init(
        repo: ContentRepositoryProtocol = ContentRepository.shared,
        tracker: AnalyticsTracker = .shared,
        pollInterval: Duration = .seconds(3),
        maxPollDuration: Duration = .seconds(90)
    ) {
        self.repo = repo
        self.tracker = tracker
        self.pollInterval = pollInterval
        self.maxPollDuration = maxPollDuration
    }

    // MARK: - 查询（进详情页恢复状态）

    /// 会员进入详情页时调用：恢复服务端已有状态（none→idle / 进行中→续轮询 /
    /// ready→直接展示 / failed→可重试）。非会员由 View 层拦在入口，不调本方法。
    func load(eventID: Int) async {
        do {
            let r = try await repo.insight(eventID: eventID)
            apply(r, eventID: eventID)
        } catch {
            mapError(error)
        }
    }

    // MARK: - 触发生成

    /// 点击「立即生成」。防双击：进行中不重复触发；服务端对重复 POST 也只返回现状。
    func generate(eventID: Int) async {
        if case .generating = state { return }
        state = .generating
        do {
            let r = try await repo.triggerInsight(eventID: eventID)
            apply(r, eventID: eventID)
        } catch {
            mapError(error)
        }
    }

    // MARK: - 生命周期

    func cancelPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    // MARK: - 内部

    private func apply(_ r: EventInsight, eventID: Int) {
        switch r.status {
        case "none":
            state = .idle
        case "pending", "generating":
            state = .generating
            startPolling(eventID: eventID)
        case "ready":
            state = .ready(r)
            tracker.track(.insightView, props: ["event_id": AnyCodable(eventID)])
        case "failed":
            state = .failed(r.error ?? "生成失败，请稍后重试")
        default:
            state = .failed("未知状态，请稍后重试")
        }
    }

    private func mapError(_ error: Error) {
        if let apiError = error as? APIError {
            switch apiError {
            case .unauthorized:
                state = .loginRequired
                return
            case .http(let status) where status == 403:
                state = .memberRequired
                return
            default:
                break
            }
        }
        state = .failed("网络异常，请稍后重试")
    }

    private func startPolling(eventID: Int) {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            let deadline = ContinuousClock.now + self.maxPollDuration
            while !Task.isCancelled, ContinuousClock.now < deadline {
                try? await Task.sleep(for: self.pollInterval)
                if Task.isCancelled { return }
                do {
                    let r = try await self.repo.insight(eventID: eventID)
                    switch r.status {
                    case "ready":
                        self.state = .ready(r)
                        self.tracker.track(
                            .insightView, props: ["event_id": AnyCodable(eventID)]
                        )
                        return
                    case "failed":
                        self.state = .failed(r.error ?? "生成失败，请稍后重试")
                        return
                    default:
                        continue  // pending/generating：继续等
                    }
                } catch {
                    continue  // 单次网络抖动不终止，超时上限兜底
                }
            }
            if !Task.isCancelled {
                self.state = .failed("生成超时，请稍后重试")
            }
        }
    }
}
