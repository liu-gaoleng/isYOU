//
//  DeviceIDStore.swift
//  阶段 4.3：匿名设备标识。
//
//  与 access token 同样存 Keychain（与 App 卸载/重装解耦由 Keychain 行为决定），
//  首次读取时若不存在则生成 UUID v4 并写入。不可作为 IDFA / IDFV 替代品广告归因，
//  仅用于"同一设备同一标识"的留存与漏斗归因。
//

import Foundation
import Security

protocol DeviceIDStoring {
    /// 读取或首次创建匿名设备 id。
    func deviceID() -> String
}

final class DeviceIDStore: DeviceIDStoring {
    static let shared = DeviceIDStore()

    private let service = "com.redu.app.analytics"
    private let account = "device_id"
    private var cached: String?

    private func baseQuery() -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
    }

    func deviceID() -> String {
        if let cached { return cached }
        if let existing = load() {
            cached = existing
            return existing
        }
        let fresh = UUID().uuidString
        store(fresh)
        cached = fresh
        return fresh
    }

    private func load() -> String? {
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

    private func store(_ value: String) {
        SecItemDelete(baseQuery() as CFDictionary)
        var attributes = baseQuery()
        attributes[kSecValueData as String] = Data(value.utf8)
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        SecItemAdd(attributes as CFDictionary, nil)
    }
}

/// 测试用的内存实现。
final class InMemoryDeviceIDStore: DeviceIDStoring {
    private var value: String?

    init(initial: String? = nil) { self.value = initial }

    func deviceID() -> String {
        if let value { return value }
        let fresh = UUID().uuidString
        value = fresh
        return fresh
    }
}
