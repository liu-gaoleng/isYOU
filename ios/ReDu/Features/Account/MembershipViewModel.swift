//
//  MembershipViewModel.swift
//  §4.4 会员权益页：把后端 `MembershipStatus` 映射到 UI 4 态。
//
//  设计取舍：
//  - 不新增网络/埋点白名单：续费动作复用现有 PaywallView，复用 `.paywallView` 埋点；
//    管理订阅跳 Apple 官方深链 `https://apps.apple.com/account/subscriptions`，不走 App 内自实现。
//  - 与 PaywallViewModel 解耦：本 VM 只做状态/文案派生，不持有 store/billing。
//

import Foundation

@MainActor
final class MembershipViewModel: ObservableObject {

    /// UI 4 态：未会员 / 会员有效 / 即将到期（≤7 天）/ 已到期。
    enum DisplayState: Equatable {
        case nonMember
        case active(daysRemaining: Int, expiringSoon: Bool)
        case expired(expiredAt: Date)
    }

    @Published var status: MembershipStatus?

    /// 与"会员将在 N 天内到期"提示挂钩的阈值，单位天。
    static let expiringSoonThresholdDays = 7

    /// 当前 UI 应展示的态。
    var display: DisplayState {
        guard let s = status else { return .nonMember }
        guard s.isMember else {
            if let expired = s.memberExpireAt, expired < Date() {
                return .expired(expiredAt: expired)
            }
            return .nonMember
        }
        let days = Self.daysRemaining(until: s.memberExpireAt)
        let soon = days >= 0 && days <= Self.expiringSoonThresholdDays
        return .active(daysRemaining: max(days, 0), expiringSoon: soon)
    }

    /// 主行动按钮文案。
    var primaryCTA: String {
        switch display {
        case .nonMember: return "开通会员"
        case .active(_, true): return "立即续费"
        case .active: return "查看续费方案"
        case .expired: return "重新开通"
        }
    }

    /// 是否显示"管理订阅"二级按钮（仅会员/已到期态显示，未购买过则不显示）。
    var showsManageSubscription: Bool {
        switch display {
        case .nonMember: return false
        case .active, .expired: return true
        }
    }

    /// 当前档位对外展示文案（plan 字段可能为 monthly/quarterly/yearly）。
    var planTitle: String {
        switch status?.plan {
        case "monthly": return "月度会员"
        case "quarterly": return "季度会员"
        case "yearly": return "年度会员"
        default: return "—"
        }
    }

    /// 自动续费副标题（仅会员态有意义）。
    var autoRenewSubtitle: String {
        guard let s = status, s.isMember else { return "" }
        return s.autoRenew ? "已开启自动续费" : "未开启自动续费"
    }

    /// 到期日格式化（"2026-07-21"）。
    var expireDateText: String {
        guard let date = status?.memberExpireAt else { return "—" }
        return Self.dateFormatter.string(from: date)
    }

    /// Apple 官方"管理订阅"深链。
    let manageSubscriptionURL = URL(string: "https://apps.apple.com/account/subscriptions")!

    // MARK: - 工具

    static func daysRemaining(until date: Date?, now: Date = Date()) -> Int {
        guard let date else { return 0 }
        let interval = date.timeIntervalSince(now)
        return Int(ceil(interval / 86_400))
    }

    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
