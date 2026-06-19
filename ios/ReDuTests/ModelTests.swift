//
//  ModelTests.swift
//  DTO 解码 + ContentModule + 工具函数（纯逻辑，无网络）。
//

import XCTest
@testable import ReDu

final class ModelTests: XCTestCase {

    private func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let raw = try c.decode(String.self)
            guard let date = ISO8601DateParser.parse(raw) else {
                throw DecodingError.dataCorruptedError(in: c, debugDescription: "bad date \(raw)")
            }
            return date
        }
        return d
    }

    // MARK: - EventCard 解码（snake_case 映射 + 日期）

    func test_eventCard_decodesSnakeCaseAndDate() throws {
        let json = """
        {
          "id": 7,
          "module": "finance",
          "title": "降准落地",
          "card_summary": "央行全面降准 0.5 个百分点",
          "importance": 88.5,
          "hotness": 0.92,
          "source_count": 5,
          "tags": ["货币政策", "降准"],
          "last_update": "2026-06-14T08:00:00.123456+00:00"
        }
        """.data(using: .utf8)!
        let card = try decoder().decode(EventCard.self, from: json)
        XCTAssertEqual(card.id, 7)
        XCTAssertEqual(card.module, "finance")
        XCTAssertEqual(card.cardSummary, "央行全面降准 0.5 个百分点")
        XCTAssertEqual(card.sourceCount, 5)
        XCTAssertEqual(card.tags, ["货币政策", "降准"])
    }

    // MARK: - FeedPage 解码（next_cursor 可空）

    func test_feedPage_decodesNullCursor() throws {
        let json = """
        {"items": [], "next_cursor": null}
        """.data(using: .utf8)!
        let page = try decoder().decode(FeedPage.self, from: json)
        XCTAssertTrue(page.items.isEmpty)
        XCTAssertNil(page.nextCursor)
    }

    // MARK: - DeepContent 付费墙形态

    func test_deepContent_lockedShape() throws {
        let json = """
        {
          "is_locked": true,
          "content": null,
          "preview": "前 80 字预览…",
          "paywall": {"required_tier": "member", "cta": "开通会员解锁全文"}
        }
        """.data(using: .utf8)!
        let deep = try decoder().decode(DeepContent.self, from: json)
        XCTAssertTrue(deep.isLocked)
        XCTAssertNil(deep.content)
        XCTAssertEqual(deep.paywall?.cta, "开通会员解锁全文")
    }

    // MARK: - ContentModule

    func test_contentModule_parseAndDisplay() {
        XCTAssertEqual(ContentModule.parse("ai"), .ai)
        XCTAssertEqual(ContentModule.parse("unknown"), .tech)  // 容错回退
        XCTAssertEqual(ContentModule.tech.displayName, "科技")
        XCTAssertEqual(ContentModule.allCases.map(\.rawValue), ["tech", "finance", "ai", "macro"])
    }

    // MARK: - ISO8601DateParser 多形态

    func test_iso8601Parser_variants() {
        XCTAssertNotNil(ISO8601DateParser.parse("2026-06-14T08:00:00+00:00"))
        XCTAssertNotNil(ISO8601DateParser.parse("2026-06-14T08:00:00.123456+00:00"))
        XCTAssertNotNil(ISO8601DateParser.parse("2026-06-14T08:00:00"))  // naive 兜底
        XCTAssertNil(ISO8601DateParser.parse("not-a-date"))
    }

    // MARK: - APIError 等价性

    func test_apiError_equatable() {
        XCTAssertEqual(APIError.unauthorized, APIError.unauthorized)
        XCTAssertEqual(APIError.http(status: 500), APIError.http(status: 500))
        XCTAssertNotEqual(APIError.http(status: 500), APIError.http(status: 404))
    }
}
