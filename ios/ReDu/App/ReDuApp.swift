//
//  ReDuApp.swift
//  「热读」iOS 客户端入口。
//
//  MVVM + SwiftUI + NavigationStack；最低 iOS 16。
//

import SwiftUI

@main
struct ReDuApp: App {
    var body: some Scene {
        WindowGroup {
            RootTabView()
                .preferredColorScheme(.dark) // 对齐原型：深石墨底
        }
    }
}
