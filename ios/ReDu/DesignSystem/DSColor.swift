//
//  DSColor.swift
//  设计系统配色，对齐 prototype/index.html 的「终端·情报流」基调：
//  深石墨底 + 琥珀金强调 + 涨红跌绿。
//

import SwiftUI

enum DSColor {
    /// 主文字（浅）
    static let ink = Color(hex: 0xECEEF0)
    /// 次级文字
    static let ink2 = Color(hex: 0x9AA0A6)
    /// 弱化文字
    static let ink3 = Color(hex: 0x6B7177)
    /// 深色分割线
    static let line = Color(hex: 0x23262B)
    /// 屏幕底（石墨黑）
    static let bg = Color(hex: 0x0D0F12)
    /// 面板
    static let card = Color(hex: 0x16191E)
    /// 略亮面板
    static let card2 = Color(hex: 0x1C2026)
    /// 品牌琥珀金（强调 / 链接 / 解读）
    static let accent = Color(hex: 0xF3B13B)
    /// 亮琥珀
    static let accent2 = Color(hex: 0xFFCE6B)
    /// 琥珀软背景
    static let accentSoft = Color(hex: 0xF3B13B, alpha: 0.13)
    /// 涨 / 热（红）
    static let up = Color(hex: 0xFF5B4D)
    /// 跌（绿）
    static let down = Color(hex: 0x27C281)
}

extension Color {
    /// 用 0xRRGGBB 十六进制整数构造颜色。
    init(hex: UInt32, alpha: Double = 1.0) {
        let r = Double((hex >> 16) & 0xFF) / 255.0
        let g = Double((hex >> 8) & 0xFF) / 255.0
        let b = Double(hex & 0xFF) / 255.0
        self.init(.sRGB, red: r, green: g, blue: b, opacity: alpha)
    }
}
