//
//  AppRoute.swift
//  NavigationStack 路由值：当前仅事件详情。
//

import Foundation

enum AppRoute: Hashable {
    case eventDetail(id: Int, title: String?)
    case favorites
    case history
}
