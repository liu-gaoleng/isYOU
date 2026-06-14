//
//  StateViews.swift
//  统一的加载/空/失败态视图，保证各页面体验一致（清单 2.2/2.3 验收要求）。
//

import SwiftUI

struct LoadingView: View {
    var body: some View {
        VStack(spacing: 12) {
            ProgressView()
                .tint(DSColor.accent)
            Text("加载中…")
                .font(.caption)
                .foregroundStyle(DSColor.ink3)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct EmptyStateView: View {
    var message: String = "今天还没有内容"

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "tray")
                .font(.system(size: 40))
                .foregroundStyle(DSColor.ink3)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(DSColor.ink2)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct ErrorStateView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "wifi.exclamationmark")
                .font(.system(size: 40))
                .foregroundStyle(DSColor.ink3)
            Text(message)
                .font(.subheadline)
                .foregroundStyle(DSColor.ink2)
                .multilineTextAlignment(.center)
            Button(action: retry) {
                Text("重试")
                    .font(.system(size: 14, weight: .bold))
                    .padding(.horizontal, 24)
                    .padding(.vertical, 10)
                    .background(DSColor.accentSoft)
                    .foregroundStyle(DSColor.accent)
                    .clipShape(Capsule())
            }
        }
        .padding()
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
