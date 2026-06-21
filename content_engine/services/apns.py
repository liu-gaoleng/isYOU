"""阶段 4.2：APNs 推送服务（HTTP/2 + token-based ES256 JWT）。

职责边界（不碰 DB，纯密码学/网络）：
- :class:`ApnsClient`：APNs HTTP/2 长连接客户端，自动管理 ES256 JWT 轮换；
- :func:`send_notification`：单次推送（同步发送，失败抛 :class:`ApnsError`）；
- :class:`ApnsResult`：发送结果（含 apns-id / 状态码 / Apple reason）。

为什么自研而非引入 ``aioapns``：
- 仅依赖 ``httpx[http2]`` + ``pyjwt[crypto]``，二者本就在运行时依赖中（前者新增、
  后者 3.1 已装），新增依赖为零；
- 测试 mock 简单：注入自定义 ``httpx.Client`` 即可，无需 mock 第三方内部对象；
- APNs HTTP/2 协议非常窄（POST /3/device/{token} + 三个固定头），自研更可控。

铁律：
- 凭证缺失（``apns_settings.configured == False``）→ 构造 client 时抛
  :class:`ApnsConfigError`，由调用方决定干运行还是 503；
- token 失效（HTTP 410 / reason=Unregistered）→ 抛 :class:`ApnsBadTokenError`，
  上层应回写 ``device_tokens.invalid_at`` 软删；
- 其它失败 → :class:`ApnsError`（含 status_code / reason），调用方决定是否重试。

参考：https://developer.apple.com/documentation/usernotifications/sending_notification_requests_to_apns
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from content_engine.config import ApnsSettings, settings
from content_engine.logging_config import get_logger

_logger = get_logger(__name__)


class ApnsError(Exception):
    """APNs 下发失败（非 410/Unregistered 的其它错误）。"""

    def __init__(self, status_code: int, reason: str, message: str = "") -> None:
        super().__init__(message or f"APNs error {status_code}: {reason}")
        self.status_code = status_code
        self.reason = reason


class ApnsBadTokenError(ApnsError):
    """token 失效（HTTP 410 或 reason=Unregistered / BadDeviceToken）。

    调用方应把对应 ``device_tokens.invalid_at`` 置当前时间，避免重复尝试。
    """


class ApnsConfigError(Exception):
    """APNs 凭据未配置或 .p8 文件不可读。映射运营层级错误（503）。"""


@dataclass
class ApnsResult:
    """单次推送的发送结果。"""

    apns_id: str
    status_code: int = 200
    reason: str = ""


def build_payload(
    *,
    title: str,
    body: str,
    badge: int | None = None,
    sound: str = "default",
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 APNs JSON payload（aps 标准结构 + 业务自定义键）。

    自定义键直接平铺在顶层，与 ``aps`` 同级，便于 iOS userInfo 直接取值
    （如 ``event_id`` 用于点击跳转详情页）。
    """
    payload: dict[str, Any] = {
        "aps": {
            "alert": {"title": title, "body": body},
            "sound": sound,
        }
    }
    if badge is not None:
        payload["aps"]["badge"] = badge
    if custom:
        for k, v in custom.items():
            if k == "aps":
                continue  # 不允许覆盖标准段
            payload[k] = v
    return payload


class ApnsClient:
    """APNs HTTP/2 客户端：自动维护 ES256 JWT 轮换 + 单次发送。

    用法（任务层）：
        client = ApnsClient.from_settings(settings.apns)
        client.send(token="abcd…", payload={"aps": {"alert": "..."}})

    线程/进程安全：内部 ``httpx.Client`` 是同步实现，Celery worker 单进程内串行
    复用即可；多进程间不共享连接池。
    """

    def __init__(
        self,
        *,
        team_id: str,
        key_id: str,
        bundle_id: str,
        private_key: str,
        host: str,
        jwt_ttl_seconds: int = 3000,
        request_timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._team_id = team_id
        self._key_id = key_id
        self._bundle_id = bundle_id
        self._private_key = private_key
        self._host = host
        self._jwt_ttl = jwt_ttl_seconds
        # 注入点：单测传入自定义 transport 的 client；生产用默认 HTTP/2 客户端
        self._client = client or httpx.Client(http2=True, timeout=request_timeout)
        self._jwt_token: str | None = None
        self._jwt_iat: int = 0

    @classmethod
    def from_settings(
        cls,
        apns: ApnsSettings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> "ApnsClient":
        """从全局 :class:`ApnsSettings` 装配（凭证缺失 → ApnsConfigError）。"""
        apns = apns or settings.apns
        if not apns.configured:
            raise ApnsConfigError(
                "APNs 凭据未配置：需同时设置 team_id / key_id / bundle_id / private_key_path"
            )
        try:
            private_key = _read_private_key(apns.private_key_path)
        except OSError as e:
            raise ApnsConfigError(f"无法读取 APNs .p8 私钥：{e}") from e
        return cls(
            team_id=apns.team_id,
            key_id=apns.key_id,
            bundle_id=apns.bundle_id,
            private_key=private_key,
            host=apns.host,
            jwt_ttl_seconds=apns.jwt_ttl_seconds,
            request_timeout=apns.request_timeout,
            client=client,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApnsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _provider_token(self) -> str:
        """生成或复用 provider JWT（< jwt_ttl_seconds 时复用，否则轮换）。"""
        now = int(time.time())
        if self._jwt_token and (now - self._jwt_iat) < self._jwt_ttl:
            return self._jwt_token
        token = jwt.encode(
            {"iss": self._team_id, "iat": now},
            self._private_key,
            algorithm="ES256",
            headers={"kid": self._key_id, "typ": "JWT"},
        )
        self._jwt_token = token
        self._jwt_iat = now
        return token

    def send(
        self,
        *,
        token: str,
        payload: dict[str, Any],
        push_type: str = "alert",
        priority: int = 10,
        collapse_id: str | None = None,
        topic_suffix: str = "",
    ) -> ApnsResult:
        """向单个设备 token 推送 ``payload``，失败抛 :class:`ApnsError`。

        - ``push_type``：alert / background / voip / location 等；早报用 alert。
        - ``priority``：10 立即下发，5 节能（system-driven，alert 后台时降级用）。
        - ``collapse_id``：相同 id 的推送在锁屏上合并为一条（早报建议传日期）。
        - ``topic_suffix``：apns-topic 默认是 bundle_id；voip/complication 等需要后缀。
        """
        apns_id = uuid.uuid4().hex
        topic = self._bundle_id + (topic_suffix or "")
        headers = {
            "authorization": f"bearer {self._provider_token()}",
            "apns-id": apns_id,
            "apns-push-type": push_type,
            "apns-priority": str(priority),
            "apns-topic": topic,
        }
        if collapse_id:
            headers["apns-collapse-id"] = collapse_id
        url = f"https://{self._host}/3/device/{token}"
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        resp = self._client.post(url, content=body, headers=headers)
        return _interpret_response(resp, fallback_apns_id=apns_id)


def _read_private_key(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _interpret_response(resp: httpx.Response, *, fallback_apns_id: str) -> ApnsResult:
    """把 APNs HTTP/2 响应翻译成 :class:`ApnsResult` 或抛 ApnsError 子类。"""
    apns_id = resp.headers.get("apns-id", fallback_apns_id)
    if resp.status_code == 200:
        return ApnsResult(apns_id=apns_id, status_code=200, reason="")

    reason = ""
    try:
        data = resp.json()
        reason = (data or {}).get("reason", "")
    except (ValueError, json.JSONDecodeError):
        reason = resp.text or ""

    # 410 必失效；reason 也可能是 BadDeviceToken / Unregistered（5xx 之外都按硬错）
    if resp.status_code == 410 or reason in ("Unregistered", "BadDeviceToken"):
        _logger.warning("[apns] bad token: status=%s reason=%s", resp.status_code, reason)
        raise ApnsBadTokenError(resp.status_code, reason)
    _logger.warning("[apns] send failed: status=%s reason=%s", resp.status_code, reason)
    raise ApnsError(resp.status_code, reason)


__all__ = [
    "ApnsClient",
    "ApnsConfigError",
    "ApnsBadTokenError",
    "ApnsError",
    "ApnsResult",
    "build_payload",
]
