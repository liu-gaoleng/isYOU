//
//  MembershipView.swift
//  §4.4 会员权益页（"我的"二级页）：
//  - 展示当前会员态（plan / 到期日 / 自动续费）
//  - 即将到期（≤7 天）显眼提示
//  - 主行动按钮一律跳 `PaywallView`（统一走 IAP 路径，复用 `.paywallView` 埋点）
//  - 二级"管理订阅"按钮跳 Apple 官方深链 `apps.apple.com/account/subscriptions`
//
//  克制：本页不重复列举权益条款（PaywallView 已含权益清单），只做状态展示 + 引导。
//

import SwiftUI

struct MembershipView: View {
    @EnvironmentObject private var auth: AuthStore
    @Environment(\.openURL) private var openURL
    @StateObject private var vm = MembershipViewModel()

    @State private var showPaywall = false

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            ScrollView {
                VStack(spacing: 16) {
                    statusCard
                    if vm.showsManageSubscription {
                        manageSubscriptionRow
                    }
                    actionButton
                    Spacer(minLength: 24)
                    footnote
                }
                .padding(16)
            }
        }
        .navigationTitle("我的会员")
        .navigationBarTitleDisplayMode(.inline)
        .task { vm.status = currentStatus() }
        .onChange(of: auth.user?.isMember ?? false) { _ in
            vm.status = currentStatus()
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView { newStatus in
                Task { await auth.applyMembership(newStatus) }
                vm.status = newStatus
            }
            .environmentObject(auth)
        }
    }

    // MARK: - 派生

    /// 从 AuthStore 现有用户态拼装一个轻量 MembershipStatus。
    /// 不主动重拉 /me/membership：登录后已有的字段足以驱动 UI；
    /// PaywallView 购买回调会刷新本页 status。
    private func currentStatus() -> MembershipStatus {
        let user = auth.user
        return MembershipStatus(
            isMember: user?.isMember ?? false,
            memberTier: user?.memberTier ?? "free",
            memberExpireAt: user?.memberExpireAt,
            plan: nil,
            autoRenew: true,
            subscriptionStatus: nil
        )
    }

    // MARK: - 子视图

    @ViewBuilder
    private var statusCard: some View {
        switch vm.display {
        case .nonMember:
            card {
                cardHeader(icon: "person.crop.circle", title: "普通用户", tint: DSColor.ink3)
                Text("开通会员后可解锁全部深度解读、独家专题与早报。")
                    .font(.system(size: 14))
                    .foregroundStyle(DSColor.ink2)
            }

        case let .active(daysRemaining, expiringSoon):
            card {
                cardHeader(
                    icon: "checkmark.seal.fill",
                    title: vm.planTitle,
                    tint: DSColor.accent
                )
                infoRow(title: "到期日期", value: vm.expireDateText)
                if !vm.autoRenewSubtitle.isEmpty {
                    infoRow(title: "续费状态", value: vm.autoRenewSubtitle)
                }
                if expiringSoon {
                    banner(
                        icon: "exclamationmark.triangle.fill",
                        text: daysRemaining == 0
                            ? "会员将在今天到期"
                            : "会员将在 \(daysRemaining) 天后到期",
                        tint: DSColor.up
                    )
                }
            }

        case let .expired(expiredAt):
            card {
                cardHeader(icon: "clock.badge.xmark", title: "会员已到期", tint: DSColor.ink3)
                infoRow(
                    title: "到期日期",
                    value: MembershipView.expiredFormatter.string(from: expiredAt)
                )
                banner(
                    icon: "exclamationmark.triangle.fill",
                    text: "重新开通后将立即解锁所有深度内容",
                    tint: DSColor.up
                )
            }
        }
    }

    private var actionButton: some View {
        Button { showPaywall = true } label: {
            Text(vm.primaryCTA)
                .font(.system(size: 16, weight: .bold))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
                .background(DSColor.accent)
                .foregroundStyle(DSColor.bg)
                .clipShape(RoundedRectangle(cornerRadius: 14))
        }
    }

    private var manageSubscriptionRow: some View {
        Button {
            openURL(vm.manageSubscriptionURL)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: "creditcard")
                    .font(.system(size: 16))
                    .foregroundStyle(DSColor.accent)
                    .frame(width: 22)
                Text("管理订阅")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(DSColor.ink)
                Spacer()
                Image(systemName: "arrow.up.right.square")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(DSColor.ink3)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(DSColor.card)
            .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
            .clipShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
    }

    private var footnote: some View {
        Text("订阅将通过 Apple ID 付费，到期自动续费，可在系统设置中随时取消。")
            .font(.system(size: 12))
            .foregroundStyle(DSColor.ink3)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 8)
    }

    // MARK: - 组件

    private func card<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            content()
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(DSColor.card)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(DSColor.line, lineWidth: 1))
        .clipShape(RoundedRectangle(cornerRadius: 14))
    }

    private func cardHeader(icon: String, title: String, tint: Color) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 20, weight: .bold))
                .foregroundStyle(tint)
            Text(title)
                .font(.system(size: 17, weight: .heavy))
                .foregroundStyle(DSColor.ink)
            Spacer()
        }
    }

    private func infoRow(title: String, value: String) -> some View {
        HStack {
            Text(title)
                .font(.system(size: 14))
                .foregroundStyle(DSColor.ink2)
            Spacer()
            Text(value)
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(DSColor.ink)
        }
    }

    private func banner(icon: String, text: String, tint: Color) -> some View {
        HStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(tint)
            Text(text)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(tint)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(tint.opacity(0.10))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private static let expiredFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()
}
