//
//  TokenStore.swift
//  access token 的安全持久化：Keychain（kSecClassGenericPassword）。
//  token 属于敏感凭据，不入 UserDefaults。
//

import Foundation
import Security

/// access token 读写协议，便于测试时注入内存实现。
protocol TokenStoring {
    var token: String? { get }
    func save(_ token: String)
    func clear()
}

/// 基于 Keychain 的默认实现：进程内单例。
final class TokenStore: TokenStoring {
    static let shared = TokenStore()

    private let service = "com.redu.app.auth"
    private let account = "access_token"

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    var token: String? {
        var query = baseQuery()
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess,
              let data = item as? Data,
              let value = String(data: data, encoding: .utf8) else {
            return nil
        }
        return value
    }

    func save(_ token: String) {
        let data = Data(token.utf8)
        // 先删旧值再写，避免 duplicate item。
        SecItemDelete(baseQuery() as CFDictionary)
        var attributes = baseQuery()
        attributes[kSecValueData as String] = data
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(attributes as CFDictionary, nil)
    }

    func clear() {
        SecItemDelete(baseQuery() as CFDictionary)
    }
}
