//
//  APIClientTests.swift
//  网络层：用 URLProtocol 桩拦截请求，验证鉴权头注入 + 状态码映射 + 解码。
//

import XCTest
@testable import ReDu

/// 内存 token store。
final class MemoryTokenStore: TokenStoring {
    private var value: String?
    init(token: String? = nil) { self.value = token }
    var token: String? { value }
    func save(_ token: String) { value = token }
    func clear() { value = nil }
}

/// 请求拦截桩：按需返回状态码 + body，并捕获最近一次请求供断言。
final class StubURLProtocol: URLProtocol {
    nonisolated(unsafe) static var statusCode = 200
    nonisolated(unsafe) static var responseData = Data()
    nonisolated(unsafe) static var lastRequest: URLRequest?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func stopLoading() {}

    override func startLoading() {
        StubURLProtocol.lastRequest = request
        let response = HTTPURLResponse(
            url: request.url!,
            statusCode: StubURLProtocol.statusCode,
            httpVersion: nil,
            headerFields: ["Content-Type": "application/json"]
        )!
        client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: StubURLProtocol.responseData)
        client?.urlProtocolDidFinishLoading(self)
    }
}

final class APIClientTests: XCTestCase {

    private func makeClient(token: String?) -> APIClient {
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [StubURLProtocol.self]
        return APIClient(session: URLSession(configuration: config),
                         tokenStore: MemoryTokenStore(token: token))
    }

    override func tearDown() {
        StubURLProtocol.statusCode = 200
        StubURLProtocol.responseData = Data()
        StubURLProtocol.lastRequest = nil
        super.tearDown()
    }

    func test_send_decodesBody() async throws {
        StubURLProtocol.statusCode = 200
        StubURLProtocol.responseData = "[]".data(using: .utf8)!
        let client = makeClient(token: nil)
        let cards = try await client.send(.ranking(module: nil, limit: 10), as: [EventCard].self)
        XCTAssertTrue(cards.isEmpty)
    }

    func test_authedEndpoint_injectsBearer() async throws {
        StubURLProtocol.statusCode = 200
        StubURLProtocol.responseData = """
        {"daily_push": true, "push_time": "08:00", "breaking_push": false}
        """.data(using: .utf8)!
        let client = makeClient(token: "tok-123")
        _ = try await client.send(.getSettings, as: PushSettings.self)
        XCTAssertEqual(
            StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"),
            "Bearer tok-123"
        )
    }

    func test_publicEndpoint_omitsBearer() async throws {
        StubURLProtocol.statusCode = 200
        StubURLProtocol.responseData = "[]".data(using: .utf8)!
        let client = makeClient(token: "tok-123")
        _ = try await client.send(.ranking(module: nil, limit: 5), as: [EventCard].self)
        XCTAssertNil(StubURLProtocol.lastRequest?.value(forHTTPHeaderField: "Authorization"))
    }

    func test_401_mapsToUnauthorized() async {
        StubURLProtocol.statusCode = 401
        let client = makeClient(token: "expired")
        do {
            _ = try await client.send(.me, as: UserProfile.self)
            XCTFail("应抛出 unauthorized")
        } catch {
            XCTAssertEqual(error as? APIError, .unauthorized)
        }
    }

    func test_500_mapsToHTTP() async {
        StubURLProtocol.statusCode = 500
        StubURLProtocol.responseData = "{}".data(using: .utf8)!
        let client = makeClient(token: nil)
        do {
            _ = try await client.send(.ranking(module: nil, limit: 5), as: [EventCard].self)
            XCTFail("应抛出 http(500)")
        } catch {
            XCTAssertEqual(error as? APIError, .http(status: 500))
        }
    }
}
