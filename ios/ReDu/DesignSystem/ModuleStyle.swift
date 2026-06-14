//
//  ModuleStyle.swift
//  四大模块（科技/金融/AI/宏观）的展示元数据：中文名、强调色。
//

import SwiftUI

/// 与后端 Module 枚举对齐：tech / finance / ai / macro。
enum ContentModule: String, CaseIterable, Identifiable {
    case tech
    case finance
    case ai
    case macro

    var id: String { rawValue }

    /// 频道中文名。
    var displayName: String {
        switch self {
        case .tech: return "科技"
        case .finance: return "金融"
        case .ai: return "AI"
        case .macro: return "宏观"
        }
    }

    /// 模块强调色（卡片 chip / 频道高亮）。
    var tint: Color {
        switch self {
        case .tech: return Color(hex: 0x4FA3FF)
        case .finance: return DSColor.up
        case .ai: return DSColor.accent
        case .macro: return DSColor.down
        }
    }

    /// 容错解析：未知值回退 tech。
    static func parse(_ raw: String) -> ContentModule {
        ContentModule(rawValue: raw) ?? .tech
    }
}
