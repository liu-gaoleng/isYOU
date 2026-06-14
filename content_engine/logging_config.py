"""统一日志配置（阶段 D 可观测性补强）。

为什么单独一个模块而不放进 config/settings：
日志需要在进程最早期初始化（早于大多数业务 import），且初始化逻辑要保持
对 settings/DB 零依赖，避免循环导入与「日志还没配好就先打日志」的尴尬。
因此这里只读环境变量，提供两个入口：

- ``configure_logging()``：进程级一次性初始化（幂等），FastAPI / Celery / CLI
  入口各调一次即可；重复调用不会叠加 handler。
- ``get_logger(name)``：业务模块取 logger 的便捷封装（等价 logging.getLogger）。

环境变量：
- ``RD_LOG_LEVEL``  日志级别（默认 INFO）；
- ``RD_LOG_FORMAT`` ``plain``（默认，人读）或 ``json``（结构化，便于采集）。
"""

from __future__ import annotations

import json
import logging
import os
import sys

_CONFIGURED = False

# 业务统一 logger 根命名空间（各模块用 get_logger(__name__) 自动挂在其下）
ROOT_NAME = "content_engine"


class _JsonFormatter(logging.Formatter):
    """极简 JSON 行格式，便于日志采集系统结构化解析。"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """进程级日志初始化（幂等）。

    在 content_engine 根 logger 上挂一个 StreamHandler(stderr)，业务模块通过
    ``get_logger`` 取到的子 logger 会向上传播到这里，统一格式与级别。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level_name = (level or os.getenv("RD_LOG_LEVEL", "INFO")).upper()
    fmt_name = (fmt or os.getenv("RD_LOG_FORMAT", "plain")).lower()

    handler = logging.StreamHandler(stream=sys.stderr)
    if fmt_name == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger(ROOT_NAME)
    root.setLevel(getattr(logging, level_name, logging.INFO))
    # 幂等：先清掉自己挂过的 handler，避免重复初始化叠加输出
    root.handlers.clear()
    root.addHandler(handler)
    # 不向 Python 根 logger 传播，避免与第三方 basicConfig 重复打印
    root.propagate = False

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """取业务 logger；name 一般传 __name__。"""
    return logging.getLogger(name)


__all__ = ["configure_logging", "get_logger", "ROOT_NAME"]
