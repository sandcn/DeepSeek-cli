"""API 调用错误分类体系 — 语义化异常、统一重试判定与退避策略

集中管理错误处理的四个决策点（此前分散在 client_async / _retry /
core.exceptions 三处，且依赖字符串关键词匹配推断错误类别）：
1. 分类：classify_http_error() 将 HTTP 状态码映射为语义化异常类型
2. 判定：is_retryable() 统一回答「该错误是否值得重试」
3. 退避：compute_retry_delay() 指数退避 + 抖动，429 时优先尊重 Retry-After
4. 呈现：format_user_error() 按状态码生成可操作的用户提示
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

# 可重试的 HTTP 状态码（瞬时错误）：请求超时/速率限制/服务端过载/网关超时。
# 其余（400/401/403/404/422 等）为永久错误，重试无意义。
RETRYABLE_HTTP_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Retry-After 绝对上限（秒）：防御服务端返回异常大的值导致近乎挂起
RETRY_AFTER_CAP = 120.0

# 指数退避单次等待上限（秒）
MAX_BACKOFF_SEC = 60.0

# 连接类错误（网络层瞬时故障，始终可重试）
CONNECTION_ERRORS = (
    ConnectionError,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.WriteError,
)


# ── 异常体系 ────────────────────────────────────────────────

class APIError(Exception):
    """API 调用错误基类。

    Attributes:
        status_code: HTTP 状态码
        message: 服务端返回的错误正文（截断后）
        retry_after: 服务端建议的等待秒数（来自 Retry-After 头），可能为 None
        retryable: 该状态码是否属于瞬时错误（可重试）
    """

    def __init__(self, status_code: int, message: str, *, retry_after=None):
        self.status_code = status_code
        self.message = message
        if isinstance(retry_after, str):
            retry_after = parse_retry_after(retry_after)
        self.retry_after = retry_after
        super().__init__(f"API error {status_code}: {message}")

    @property
    def retryable(self) -> bool:
        return self.status_code in RETRYABLE_HTTP_STATUS


class RateLimitError(APIError):
    """429 速率限制 / 额度不足（可重试，应尊重 Retry-After）。"""

    def __init__(self, message: str = "rate limited", *, retry_after=None):
        super().__init__(429, message, retry_after=retry_after)


class AuthError(APIError):
    """401/403 认证或授权失败（永久错误，重试无意义）。"""


class NotFoundError(APIError):
    """404 接口地址或模型不存在（永久错误）。"""


class InvalidRequestError(APIError):
    """400/422 请求参数错误（永久错误）。"""


class ServerError(APIError):
    """5xx / 408 / 425 服务端瞬时错误（可重试）。"""


# ── 状态码 → 用户可操作的提示 ────────────────────────────────

_STATUS_HINTS = {
    400: "请求参数不合法",
    401: "API 密钥无效或未设置，请检查环境变量 CHAT_API_KEY",
    403: "API 密钥无权访问（可能欠费或权限不足）",
    404: "接口地址或模型不存在，请检查 BASE_URL 与模型名",
    408: "请求超时",
    422: "请求参数验证失败（如消息历史中 tool_calls 与 tool 响应不配对）",
    425: "请求过早，请稍后重试",
    429: "请求频率超限或额度不足",
    500: "服务端内部错误",
    502: "网关错误（上游服务不可用）",
    503: "服务暂时不可用（过载或维护中）",
    504: "网关超时",
}


def classify_http_error(status_code: int, message: str, *, retry_after=None) -> APIError:
    """将 HTTP 状态码映射为语义化异常类型。

    未知状态码回退为通用 APIError（retryable 按 RETRYABLE_HTTP_STATUS 判定）。
    """
    if status_code == 429:
        return RateLimitError(message, retry_after=retry_after)
    if status_code in (401, 403):
        return AuthError(status_code, message, retry_after=retry_after)
    if status_code == 404:
        return NotFoundError(status_code, message, retry_after=retry_after)
    if status_code in (400, 422):
        return InvalidRequestError(status_code, message, retry_after=retry_after)
    if status_code in RETRYABLE_HTTP_STATUS:
        return ServerError(status_code, message, retry_after=retry_after)
    return APIError(status_code, message, retry_after=retry_after)


# ── 重试判定 ────────────────────────────────────────────────

def is_retryable(exc: BaseException) -> bool:
    """统一判定异常是否值得重试（瞬时错误）。

    规则：
    - APIError 及子类 → 按 status_code 是否属于 RETRYABLE_HTTP_STATUS
    - 连接类错误（CONNECTION_ERRORS）→ True
    - httpx 传输层错误（RequestError/HTTPStatusError）→ True
    - JSON 解析错误 / 超时 → True（响应损坏可能是网关瞬时故障）
    - 其余 → False（由调用方决定抛出）
    """
    if isinstance(exc, APIError):
        return exc.retryable
    if isinstance(exc, CONNECTION_ERRORS):
        return True
    if isinstance(exc, (httpx.RequestError, httpx.HTTPStatusError)):
        return True
    if isinstance(exc, (json.JSONDecodeError, TimeoutError, asyncio.TimeoutError)):
        return True
    return False


# ── Retry-After 解析 ────────────────────────────────────────

def parse_retry_after(value) -> float | None:
    """解析 Retry-After 头值，支持秒数与 HTTP-date 两种格式。

    无效/缺失/负值返回 None（视为未提供）。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        seconds = float(text)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if target is None:
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max((target - datetime.now(timezone.utc)).total_seconds(), 0.0)


# ── 退避策略 ────────────────────────────────────────────────

def compute_retry_delay(
    attempt: int,
    base_delay: float,
    exc: BaseException | None = None,
    *,
    max_delay: float = MAX_BACKOFF_SEC,
) -> float:
    """计算第 attempt 次失败后的重试等待秒数。

    优先级：
    1. 异常携带 retry_after（429 场景服务端建议）→ min(retry_after, RETRY_AFTER_CAP)
    2. 指数退避：min(base_delay * 2^(attempt-1), max_delay) + 10% 抖动

    base_delay=0 时恒返回 0（快速失败场景，如 SubAgent）。
    """
    if exc is not None:
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None and retry_after > 0:
            return min(float(retry_after), RETRY_AFTER_CAP)
    base = max(float(base_delay), 0.0)
    exp = max(int(attempt), 1) - 1
    delay = min(base * (2 ** exp), max_delay)
    return delay + random.uniform(0.0, delay * 0.1)


# ── 用户消息 ────────────────────────────────────────────────

def format_user_error(exc: BaseException) -> str:
    """生成用户可读、可操作的错误消息。

    消息前缀保持与 core.exceptions._NETWORK_ERROR_KEYWORDS 的关键词
    匹配兼容（「连接错误」/「API 调用出错」/「请求超时」）。
    """
    status_code = getattr(exc, "status_code", None)
    hint = _STATUS_HINTS.get(status_code) if isinstance(status_code, int) else None
    if isinstance(exc, APIError):
        msg = f"抱歉，API 调用出错: {exc}"
        if isinstance(exc, RateLimitError) and exc.retry_after:
            msg += f"，服务端建议 {exc.retry_after:.0f} 秒后重试"
        if hint:
            msg += f"（{hint}）"
        return msg
    if isinstance(exc, CONNECTION_ERRORS):
        return f"连接错误: {exc}"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return f"抱歉，API 调用出错: 请求超时（{exc}）"
    return f"抱歉，API 调用出错: {exc}"


__all__ = [
    "APIError", "RateLimitError", "AuthError", "NotFoundError",
    "InvalidRequestError", "ServerError",
    "CONNECTION_ERRORS", "RETRYABLE_HTTP_STATUS",
    "RETRY_AFTER_CAP", "MAX_BACKOFF_SEC",
    "classify_http_error", "is_retryable", "parse_retry_after",
    "compute_retry_delay", "format_user_error",
]
