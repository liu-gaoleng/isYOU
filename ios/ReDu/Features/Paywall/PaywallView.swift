//
//  PaywallView.swift
//  会员开通页（sheet）：展示档位与价格，发起 StoreKit 购买，支持恢复购买。
//  购买成功后回调上层刷新全局会员态并自动关闭。
//

import SwiftUI

struct PaywallView: View {
    /// 购买成功回调（传出最新会员态），由上层刷新 AuthStore。
    var onPurchased: (MembershipStatus) -> Void

    @Environment(\.dismiss) private var dismiss
    @StateObject private var vm = PaywallViewModel()
    @State private var selectedID: String?

    var body: some View {
        NavigationStack {
            ZStack {
                DSColor.bg.ignoresSafeArea()
                content
            }
            .navigationTitle("开通会员")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button("关闭") { dismiss() }
                        .foregroundStyle(DSColor.ink3)
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("恢复购买") { Task { await runRestore() } }
                        .foregroundStyle(DSColor.ink2)
                        .disabled(vm.purchasing)
                }
            }
        }
        .task { await vm.load() }
    }

    @ViewBuilder
    private var content: some View {
        switch vm.state {
        case .idle, .loading:
            LoadingView()
        case .failed(let msg):
            ErrorStateView(message: msg) { Task { await vm.load() } }
        case .empty:
            EmptyStateView(message: "暂无可购买的套餐")
        case .loaded:
            loaded
        }
    }

    private var loaded: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                benefits
                ForEach(vm.rows) { row in
                    planCard(row)
                }
                if let err = vm.purchaseError {
                    Text(err)
                        .font(.system(size: 12))
                        .foregroundStyle(DSColor.up)
                }
                purchaseButton
                disclaimer
            }
            .padding(20)
        }
    }

    private var benefits: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("会员专享")
                .font(.system(size: 18, weight: .heavy))
                .foregroundStyle(DSColor.ink)
            ForEach(["解锁全部深度解读全文", "四大模块完整情报", "重大事件即时推送"], id: \.self) { item in
                HStack(spacing: 8) {
                    Image(systemName: "checkmark.seal.fill")
                        .font(.system(size: 13))
                        .foregroundStyle(DSColor.accent)
                    Text(item)
                        .font(.system(size: 14))
                        .foregroundStyle(DSColor.ink2)
                }
            }
        }
        .padding(.bottom, 4)
    }

    private func planCard(_ row: PaywallViewModel.PlanRow) -> some View {
        let selected = selectedID == row.id
        return Button {
            selectedID = row.id
        } label: {
            HStack {
                VStack(alignment: .leading, spacing: 4) {
                    Text(planName(row.plan.plan))
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(DSColor.ink)
                    Text("\(row.plan.periodDays) 天")
                        .font(.system(size: 12, design: .monospaced))
                        .foregroundStyle(DSColor.ink3)
                }
                Spacer()
                Text(row.displayPrice)
                    .font(.system(size: 16, weight: .heavy))
                    .foregroundStyle(selected ? DSColor.accent : DSColor.ink)
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? DSColor.accentSoft : DSColor.card)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(selected ? DSColor.accent : DSColor.line, lineWidth: selected ? 1.5 : 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .buttonStyle(.plain)
    }

    private var purchaseButton: some View {
        Button {
            Task { await runPurchase() }
        } label: {
            Group {
                if vm.purchasing {
                    ProgressView().tint(DSColor.bg)
                } else {
                    Text("立即开通")
                        .font(.system(size: 16, weight: .bold))
                }
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 14)
            .background(selectedRow == nil ? DSColor.ink3 : DSColor.accent)
            .foregroundStyle(DSColor.bg)
            .clipShape(RoundedRectangle(cornerRadius: 12))
        }
        .disabled(selectedRow == nil || vm.purchasing)
        .padding(.top, 4)
    }

    private var disclaimer: some View {
        Text("订阅将通过 Apple ID 付费，到期自动续费，可在系统设置中随时取消。")
            .font(.system(size: 11))
            .foregroundStyle(DSColor.ink3)
            .lineSpacing(3)
    }

    private var selectedRow: PaywallViewModel.PlanRow? {
        vm.rows.first { $0.id == selectedID }
    }

    private func runPurchase() async {
        guard let row = selectedRow else { return }
        if let status = await vm.purchase(row), status.isMember {
            onPurchased(status)
            dismiss()
        }
    }

    private func runRestore() async {
        if let status = await vm.restore(), status.isMember {
            onPurchased(status)
            dismiss()
        }
    }

    private func planName(_ plan: String) -> String {
        switch plan {
        case "monthly": return "月度会员"
        case "quarterly": return "季度会员"
        case "yearly": return "年度会员"
        default: return plan
        }
    }
}
