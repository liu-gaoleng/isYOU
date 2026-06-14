//
//  LoadState.swift
//  通用加载态：空/加载/成功/失败，供各页面统一渲染。
//

import Foundation

enum LoadState: Equatable {
    case idle
    case loading
    case loaded
    case empty
    case failed(String)
}
