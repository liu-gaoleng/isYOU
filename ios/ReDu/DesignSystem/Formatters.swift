//
//  Formatters.swift
//  展示用格式化工具：相对时间、热度数值、日期标题。
//

import Foundation

enum DateText {
    /// 顶部日期标题，如 "2026.06.14 周六"。
    static func headerTitle(_ date: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_CN")
        f.dateFormat = "yyyy.MM.dd EEEE"
        return f.string(from: date)
    }

    /// 卡片相对时间，如 "12 分钟前"、"3 小时前"。
    static func relative(_ date: Date, now: Date = Date()) -> String {
        let seconds = Int(now.timeIntervalSince(date))
        if seconds < 60 { return "刚刚" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes) 分钟前" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours) 小时前" }
        let days = hours / 24
        if days < 30 { return "\(days) 天前" }
        let f = DateFormatter()
        f.dateFormat = "MM-dd"
        return f.string(from: date)
    }

    /// 当日字符串 YYYY-MM-DD，给 daily-brief 接口用。
    static func ymd(_ date: Date = Date()) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }
}

enum HotText {
    /// 热度 0~1 → 百分号展示。
    static func percent(_ hotness: Double) -> String {
        "\(Int((hotness * 100).rounded()))"
    }
}
