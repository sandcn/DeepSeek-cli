"""
Core exception classification for CLI message consumer.

Provides a hierarchy of exceptions to distinguish fatal vs non-fatal errors,
enabling the CLI interaction loop to continue running after recoverable failures.
"""

from __future__ import annotations

import asyncio
import logging
import re

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class NonFatalError(Exception):
    """Non-fatal exception: the application can continue running.

    Examples: network blips, single API failures, message format errors.
    """


class TransientError(NonFatalError):
    """Transient error that may succeed on retry.

    Examples: API rate-limits, temporary network timeouts.
    """


class FatalError(Exception):
    """Fatal exception: the application MUST exit immediately.

    Examples: disk full (ENOSPC), out-of-memory (ENOMEM), data corruption.
    """


# ---------------------------------------------------------------------------
# Fatal-pattern detection
# ---------------------------------------------------------------------------

# OSError errno substrings that indicate unrecoverable resource exhaustion.
_FATAL_ERRNO_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\[Errno 28\]"),  # ENOSPC – No space left on device
    re.compile(r"\[Errno 12\]"),  # ENOMEM – Cannot allocate memory
]

# Exception types that are always considered fatal.
_FATAL_TYPES: tuple[type[BaseException], ...] = (
    MemoryError,
    SystemExit,
)

# Exception types that are always considered non-fatal.
_NONFATAL_TYPES: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    ValueError,
    KeyError,
)


def is_fatal_exception(exc: BaseException) -> bool:
    """Determine whether *exc* is fatal and should trigger a forced exit.

    Decision logic (first match wins):
    1. ``asyncio.CancelledError`` is **never** fatal – it is a cooperative
       cancellation signal and must always be re-raised.
    2. Instances of ``_FATAL_TYPES`` (MemoryError, SystemExit) → fatal.
    3. ``OSError`` whose message matches a fatal errno pattern → fatal.
    4. Instances of ``FatalError`` → fatal.
    5. Instances of ``NonFatalError`` (including ``TransientError``) → non-fatal.
    6. Instances of ``_NONFATAL_TYPES`` → non-fatal.
    7. Everything else → **non-fatal** by default (conservative: keep running).
    """
    # CancelledError is cooperative cancellation, not an error.
    if isinstance(exc, asyncio.CancelledError):
        return False

    # Explicit fatal markers.
    if isinstance(exc, _FATAL_TYPES):
        return True

    # OSError with fatal errno.
    if isinstance(exc, OSError):
        msg = str(exc)
        if any(pat.search(msg) for pat in _FATAL_ERRNO_PATTERNS):
            return True

    # Custom hierarchy.
    if isinstance(exc, FatalError):
        return True

    if isinstance(exc, NonFatalError):
        return False

    # Well-known non-fatal built-in types.
    if isinstance(exc, _NONFATAL_TYPES):
        return False

    # Conservative default: assume non-fatal to avoid unnecessary exits.
    return False


# ---------------------------------------------------------------------------
# Network error detection
# ---------------------------------------------------------------------------

# 网络错误内容关键词 — 从 retry_api_call_async 的错误消息模式提取
# API 层重试用尽后返回包含这些关键词的错误字符串
# 所有关键词均为小写，在与 content.lower() 匹配时使用
_NETWORK_ERROR_KEYWORDS: tuple[str, ...] = (
    # 来自 _CONNECTION_ERRORS 重试用尽: f"连接错误: {str(e)}"
    "连接错误",
    # 来自 httpx.HTTPStatusError/RequestError/JSONDecodeError/TimeoutError/RateLimitError 重试用尽: f"抱歉，API 调用出错: {str(e)}"
    "api 调用出错",
    # 来自 StreamIdleTimeoutError: "流空闲超时: ..."
    "流空闲超时",
    "stream idle timeout",
    # 英文网络错误消息
    "connection error",
    "connection refused",
    "connection reset",
    # 超时类错误（含 "请求超时"、"timed out" 等变体）
    "请求超时",
    "timed out",
)


def is_network_error(content: str | None, exc: BaseException | None = None) -> bool:
    """检测是否为网络错误。

    支持两种检测策略（OR 组合）：
    1. 内容关键词匹配：在 content 字符串中搜索网络错误关键词
    2. 异常类型匹配：检查 exc 是否为网络相关异常类型

    此为纯函数：无副作用、无 IO、无可变全局状态修改。

    Args:
        content: 模型返回的文本内容（可能包含 API 层重试用尽后的错误消息）
        exc: 捕获的异常对象（可选）

    Returns:
        True 如果检测到网络错误，否则 False
    """
    # ── 策略 1：内容关键词匹配 ──
    if content:
        content_lower = content.lower()
        for keyword in _NETWORK_ERROR_KEYWORDS:
            if keyword in content_lower:
                return True

    # ── 策略 2：异常类型匹配 ──
    if exc is not None:
        if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError)):
            return True
        # httpx 连接/传输错误（延迟导入避免模块级依赖）
        try:
            import httpx
        except ImportError:
            _logger.debug("httpx not available, cannot check httpx-specific network errors")
        else:
            if isinstance(exc, (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RequestError,
                httpx.HTTPStatusError,
            )):
                return True

    return False
