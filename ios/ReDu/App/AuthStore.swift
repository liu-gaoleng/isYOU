//
//  AuthStore.swift
//  全局登录态：持有当前用户 + token，驱动登录/登出与会员态刷新。
//  作为 @StateObject 注入应用入口，下游通过 @EnvironmentObject 读取。
//

import Foundation

@MainActor
final class AuthStore: ObservableObject {
    /// 登录态：未登录 / 已登录（含用户信息）。
    enum Phase: Equatable {
        case anonymous
        case authenticated(UserProfile)
    }

    @Published private(set) var phase: Phase = .anonymous
    /// 登录请求进行中（驱动按钮 loading）。
    @Published var isLoggingIn = false
    /// 登录失败提示。
    @Published var loginError: String?

    private let repo: AuthRepositoryProtocol
    private let tokenStore: TokenStoring

    init(repo: AuthRepositoryProtocol = AuthRepository.shared,
         tokenStore: TokenStoring = TokenStore.shared) {
        self.repo = repo
        self.tokenStore = tokenStore
    }

    /// 是否已登录。
    var isAuthenticated: Bool {
        if case .authenticated = phase { return true }
        return false
    }

    /// 当前用户（未登录为 nil）。
    var user: UserProfile? {
        if case let .authenticated(u) = phase { return u }
        return nil
    }

    /// 是否会员（驱动付费墙解锁态）。
    var isMember: Bool { user?.isMember ?? false }

    /// 应用启动时调用：若 Keychain 有 token，拉取用户信息恢复登录态。
    func restore() async {
        guard tokenStore.token != nil else { return }
        do {
            let profile = try await repo.currentUser()
            phase = .authenticated(profile)
        } catch APIError.unauthorized {
            // token 失效，清理本地凭据回到未登录。
            logout()
        } catch {
            // 网络等临时错误：保留 token，下次再试，不强制登出。
        }
    }

    /// Sign in with Apple：上送 identityToken 换取本地 token。
    func loginWithApple(identityToken: String, displayName: String?) async {
        await performLogin {
            try await self.repo.loginWithApple(identityToken: identityToken, displayName: displayName)
        }
    }

    /// dev 测试登录（仅本地联调）。
    func devLogin(appleUserID: String, asMember: Bool) async {
        await performLogin {
            try await self.repo.devLogin(
                appleUserID: appleUserID, email: nil, displayName: nil, asMember: asMember
            )
        }
    }

    /// 登出：清空 token 与本地态。
    func logout() {
        tokenStore.clear()
        phase = .anonymous
    }

    /// 刷新当前用户（如付费后会员态变化）。
    func refreshProfile() async {
        guard isAuthenticated else { return }
        if let profile = try? await repo.currentUser() {
            phase = .authenticated(profile)
        }
    }

    private func performLogin(_ task: @escaping () async throws -> LoginResponse) async {
        isLoggingIn = true
        loginError = nil
        defer { isLoggingIn = false }
        do {
            let resp = try await task()
            tokenStore.save(resp.accessToken)
            phase = .authenticated(resp.user)
        } catch {
            loginError = (error as? APIError)?.errorDescription ?? "登录失败，请重试"
        }
    }
}
