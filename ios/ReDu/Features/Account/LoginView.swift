//
//  LoginView.swift
//  登录页（清单 2.6）：Sign in with Apple 为主入口；dev-login 仅本地联调备用。
//

import AuthenticationServices
import SwiftUI

struct LoginView: View {
    @EnvironmentObject private var auth: AuthStore
    @Environment(\.dismiss) private var dismiss

    /// 是否暴露 dev-login 入口（DEBUG 构建下显示，便于无真机联调）。
    #if DEBUG
    private let showDevLogin = true
    #else
    private let showDevLogin = false
    #endif

    var body: some View {
        ZStack {
            DSColor.bg.ignoresSafeArea()
            VStack(spacing: 0) {
                Spacer()
                brand
                Spacer()
                signInArea
            }
            .padding(.horizontal, 28)
            .padding(.bottom, 40)
        }
        // 登录成功后自动收起。
        .onChange(of: auth.isAuthenticated) { authed in
            if authed { dismiss() }
        }
    }

    private var brand: some View {
        VStack(spacing: 16) {
            Text("热读")
                .font(.system(size: 40, weight: .heavy))
                .foregroundStyle(DSColor.accent)
                .tracking(4)
            Text("10 分钟读懂科技 · 金融 · AI · 宏观")
                .font(.system(size: 14, weight: .semibold))
                .foregroundStyle(DSColor.ink2)
            Text("登录后可同步收藏、阅读历史与会员权益")
                .font(.system(size: 12))
                .foregroundStyle(DSColor.ink3)
                .multilineTextAlignment(.center)
        }
    }

    private var signInArea: some View {
        VStack(spacing: 14) {
            SignInWithAppleButton(.signIn) { request in
                request.requestedScopes = [.fullName, .email]
            } onCompletion: { result in
                handleApple(result)
            }
            .signInWithAppleButtonStyle(.white)
            .frame(height: 50)
            .clipShape(RoundedRectangle(cornerRadius: 12))
            .disabled(auth.isLoggingIn)

            if showDevLogin {
                Button {
                    Task { await auth.devLogin(appleUserID: "dev-tester", asMember: false) }
                } label: {
                    Text("开发者登录（联调）")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(DSColor.ink3)
                }
                .disabled(auth.isLoggingIn)
            }

            if auth.isLoggingIn {
                ProgressView().tint(DSColor.accent)
            }

            if let err = auth.loginError {
                Text(err)
                    .font(.system(size: 12))
                    .foregroundStyle(DSColor.up)
                    .multilineTextAlignment(.center)
            }

            Text("继续即表示同意《用户协议》与《隐私政策》")
                .font(.system(size: 10.5))
                .foregroundStyle(DSColor.ink3)
                .padding(.top, 4)
        }
    }

    private func handleApple(_ result: Result<ASAuthorization, Error>) {
        switch result {
        case let .success(authorization):
            guard let cred = authorization.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = cred.identityToken,
                  let identityToken = String(data: tokenData, encoding: .utf8) else {
                auth.loginError = "未能获取 Apple 凭据，请重试"
                return
            }
            // Apple 仅首次登录返回 fullName，拼成展示名后随登录上送。
            let displayName = [cred.fullName?.givenName, cred.fullName?.familyName]
                .compactMap { $0 }
                .joined(separator: " ")
            Task {
                await auth.loginWithApple(
                    identityToken: identityToken,
                    displayName: displayName.isEmpty ? nil : displayName
                )
            }
        case let .failure(error):
            // 用户主动取消不算错误，不打扰。
            if (error as? ASAuthorizationError)?.code == .canceled { return }
            auth.loginError = "Apple 登录失败，请重试"
        }
    }
}
