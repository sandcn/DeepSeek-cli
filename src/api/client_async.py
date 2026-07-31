"""Async HTTP 客户端 — 基于 httpx.AsyncClient

与同步版 client.py 保持接口兼容，但返回 async generator / awaitable。
特性：
- 连接池自动管理（Python 3.13+ asyncio 原生支持）
- SSE 流式解析（async generator）
- 连接错误自动恢复（重置客户端后重试）
- 线程安全（asyncio lock）
"""

from __future__ import annotations

import json
import copy
import asyncio
import logging
import threading
import weakref
from typing import AsyncIterator, Any

import httpx

from ..config import (
    HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT, HTTP_WRITE_TIMEOUT,
    HTTP_MAX_CONNECTIONS, HTTP_MAX_CONNECTIONS_PER_HOST, HTTP_KEEP_ALIVE_TIMEOUT,
    HTTP_ENABLE_POOL, HTTP_ENABLE_HTTP2, API_KEY,
)

_logger = logging.getLogger(__name__)

# 快速 JSON 解析器
try:
    import orjson
    _json_loads = orjson.loads
    _JSON_DECODE_ERRORS = (json.JSONDecodeError, orjson.JSONDecodeError, ValueError)
except ImportError:
    _json_loads = json.loads
    _JSON_DECODE_ERRORS = (json.JSONDecodeError, ValueError)


# HTTP/2 可用性缓存（模块级单次检查）
_HTTP2_AVAILABLE: bool | None = None


def _is_http2_available() -> bool:
    """检查 HTTP/2 是否可用（h2 包已安装）。结果模块级缓存，仅首次检查。"""
    global _HTTP2_AVAILABLE
    if _HTTP2_AVAILABLE is not None:
        return _HTTP2_AVAILABLE
    try:
        import h2  # noqa: F401
        _HTTP2_AVAILABLE = True
    except ImportError:
        _logger.warning("h2 包未安装，HTTP/2 不可用，回退 HTTP/1.1")
        _HTTP2_AVAILABLE = False
    return _HTTP2_AVAILABLE


def _create_async_client() -> httpx.AsyncClient:
    """创建并返回配置好的 httpx.AsyncClient 实例。"""
    timeout = httpx.Timeout(
        connect=HTTP_CONNECT_TIMEOUT,
        read=HTTP_READ_TIMEOUT,
        write=HTTP_WRITE_TIMEOUT,
        pool=HTTP_KEEP_ALIVE_TIMEOUT,
    )
    http2_enabled = HTTP_ENABLE_HTTP2 and _is_http2_available()

    limits = httpx.Limits(
        max_connections=HTTP_MAX_CONNECTIONS,
        max_keepalive_connections=HTTP_MAX_CONNECTIONS_PER_HOST,
    ) if HTTP_ENABLE_POOL else None

    return httpx.AsyncClient(timeout=timeout, limits=limits, http2=http2_enabled)


# ── 每事件循环独立 AsyncClient 池 ──────────────────────────
# 不同事件循环（Agent 持久化循环 / asyncio.run() 临时循环 / 线程子循环）
# 各自持有独立 httpx.AsyncClient，避免 "bound to a different event loop"。
# ★ 使用 asyncio.Lock 而非 threading.RLock：避免阻塞事件循环。
#   所有访问 _clients 的操作均为 async def，可用 async with 安全等待。
_clients: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_clients_lock = asyncio.Lock()


async def get_async_client() -> httpx.AsyncClient:
    """按当前事件循环获取或创建 AsyncClient。

    每一事件循环持有独立的 httpx.AsyncClient，避免跨循环共享
    导致 asyncio 原语（Event/Lock）"bound to a different event loop" 错误。
    """
    loop = asyncio.get_running_loop()
    async with _clients_lock:
        client = _clients.get(loop)
        if client is None:
            client = _create_async_client()
            _clients[loop] = client
        return client


async def reset_async_client() -> None:
    """关闭并重建当前事件循环的 AsyncClient。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    async with _clients_lock:
        client = _clients.pop(loop, None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                _logger.debug("关闭 AsyncClient 异常（事件循环可能已关闭）", exc_info=True)





# ── 连接错误类型 ────────────────────────────────────────────

_CONNECTION_ERRORS = (
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


# ── 自定义异常 ──────────────────────────────────────────────

class RateLimitError(Exception):
    pass


class APIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"API error {status_code}: {message}")


# ── 请求头 ──────────────────────────────────────────────────

_api_key_warned = False
_api_key_warned_lock = threading.Lock()


def _headers() -> dict[str, str]:
    if not API_KEY:
        with _api_key_warned_lock:
            if not _api_key_warned:
                _api_key_warned = True
                _logger.warning("未设置 API 密钥。请设置环境变量 CHAT_API_KEY（参考 .env.example）。")
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _headers_anthropic() -> dict[str, str]:
    """构建 Anthropic API 请求头（x-api-key 认证 + anthropic-version）。"""
    if not API_KEY:
        with _api_key_warned_lock:
            if not _api_key_warned:
                _api_key_warned = True
                _logger.warning("未设置 API 密钥。请设置环境变量 CHAT_API_KEY（参考 .env.example）。")
    return {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


# ── 响应检查 ────────────────────────────────────────────────

def _check_response(resp: httpx.Response) -> None:
    if resp.status_code == 429:
        raise RateLimitError(f"Rate limited: {resp.text[:500]}")
    if resp.status_code != 200:
        raise APIError(resp.status_code, resp.text[:500])


def _has_finish_reason(chunk: dict) -> bool:
    """检查 chunk 是否携带非空 finish_reason（用于提前结束 SSE 流迭代）。

    对不发送 ``[DONE]`` 且连接不关闭的服务端，finish_reason 是可靠的流结束
    信号；返回 True 时调用方应在 yield 当前 chunk 后停止迭代（先消费最后一个
    delta 再结束）。不检查 null/空字符串 finish_reason（流式过程中 finish_reason
    为 null/缺失为常态，仅最终块非空）。
    """
    try:
        choices = chunk.get("choices")
        if not choices:
            return False
        first = choices[0]
        if not isinstance(first, dict):
            return False
        fr = first.get("finish_reason")
        return bool(fr and str(fr).strip())
    except (IndexError, TypeError, AttributeError):
        # chunk 结构异常（如 data: null 解析为 None）时保守返回 False，不中断
        return False


# ── 公开接口 ────────────────────────────────────────────────

async def chat_completions_async(
    *,
    model: str,
    messages: list,
    tools: list | None = None,
    stream: bool = False,
    stream_options: dict | None = None,
    **extra: Any,
) -> dict | AsyncIterator[dict]:
    """异步调用 /v1/chat/completions 接口。

    非流式返回解析后的 JSON dict；
    流式返回 async generator，逐个 yield 解析后的 chunk dict。
    """
    payload: dict = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
    if stream:
        payload["stream"] = True
    if stream_options:
        payload["stream_options"] = stream_options
    # 防止 extra 覆盖内部关键字段
    payload.update({k: v for k, v in extra.items()
                    if k not in ("model", "messages", "stream", "stream_options", "tools")})

    from .. import config as _cfg
    url = _cfg.BASE_URL
    headers = _headers()

    if not stream:
        resp = await _call_with_recovery("post", url, headers=headers, json=payload)
        _check_response(resp)
        return resp.json()

    return _stream_iter_async(url, headers, payload, model)


async def chat_completions_async_anthropic(
    *,
    model: str,
    messages: list,
    tools: list | None = None,
    stream: bool = False,
    stream_options: dict | None = None,
    base_url: str | None = None,
    **extra: Any,
) -> dict | AsyncIterator[dict]:
    """异步调用 Anthropic /v1/messages 接口。

    使用 x-api-key 头和 /v1/messages 端点，区别于 OpenAI 的
    Bearer token + /v1/chat/completions 格式。

    非流式返回解析后的 JSON dict；
    流式返回 async generator，逐个 yield 解析后的 chunk dict。
    """
    payload: dict = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
    if stream:
        payload["stream"] = True
    # Anthropic 的 extra 参数（max_tokens, system 等）直接作为请求体字段
    payload.update({k: v for k, v in extra.items()
                    if k not in ("model", "messages", "stream", "tools")})

    from .. import config as _cfg
    api_base = (base_url or _cfg.BASE_URL).rstrip("/")
    url = f"{api_base}/messages"
    headers = _headers_anthropic()

    if not stream:
        resp = await _call_with_recovery("post", url, headers=headers, json=payload)
        _check_response(resp)
        return resp.json()

    return _stream_iter_async(url, headers, payload, model)


async def _call_with_recovery(method: str, *args, **kwargs) -> httpx.Response:
    """发起 HTTP 调用，遇到连接错误时自动重置客户端并重试一次。"""
    client = await get_async_client()
    try:
        return await getattr(client, method)(*args, **kwargs)
    except _CONNECTION_ERRORS as e:
        _logger.warning("连接错误，重置 HTTP 客户端后重试: %s", e)
        await reset_async_client()
        client = await get_async_client()
        return await getattr(client, method)(*args, **kwargs)


async def _stream_iter_async(
    url: str,
    headers: dict,
    payload: dict,
    model: str | None = None,
) -> AsyncIterator[dict]:
    """SSE 流式异步迭代器，yield 解析后的 JSON dict。"""
    # 深拷贝 messages 层，防止并发修改竞态
    msgs = payload.get("messages") or []
    if msgs:
        payload["messages"] = copy.deepcopy(msgs)

    max_attempts = 2
    for attempt in range(max_attempts):
        try:
            client = await get_async_client()
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                _check_response(resp)

                buffer = b""
                consecutive_failures = 0

                done = False

                def _process_sse_line(line_bytes: bytes):
                    """处理单行 SSE 数据（完整行与尾部残余无换行行共用）。

                    返回 (parsed, should_yield, stop)：
                      - parsed: JSON 解析结果（JSON 为 null 时可能为 None，
                        仍会 yield——与原完整行路径行为一致）
                      - should_yield: 是否应 yield parsed
                      - stop: 是否应停止迭代（[DONE] 或 finish_reason 非空）
                    JSON 解析失败按既有 consecutive_failures 计数/跳过策略处理。
                    """
                    nonlocal consecutive_failures
                    if not line_bytes:
                        return None, False, False
                    if not line_bytes.startswith(b"data: "):
                        return None, False, False
                    data_bytes = line_bytes[6:]
                    if data_bytes == b"[DONE]":
                        return None, False, True
                    try:
                        parsed = _json_loads(data_bytes.decode("utf-8"))
                        consecutive_failures = 0
                    except _JSON_DECODE_ERRORS:
                        consecutive_failures += 1
                        if consecutive_failures >= 3:
                            _logger.warning(
                                "流式数据 JSON 连续解析失败 %d 次: %s",
                                consecutive_failures, line_bytes[:100],
                            )
                        else:
                            _logger.debug(
                                "流式数据 JSON 解析失败，已跳过: %s",
                                line_bytes[:100], exc_info=True,
                            )
                        return None, False, False
                    # finish_reason 结束检查：非空时置 stop（先 yield 当前 chunk
                    # 再结束，确保最后一个 delta 被下游处理）。覆盖完整行与残余
                    # 行两条解析路径；对不发送 [DONE] 且连接不关闭的服务端，
                    # 避免流挂起至空闲超时。
                    return parsed, True, _has_finish_reason(parsed)

                async for chunk in resp.aiter_raw():
                    if done:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                        parsed, should_yield, stop = _process_sse_line(line_bytes)
                        if should_yield:
                            yield parsed
                        if stop:
                            done = True
                            break
                # 循环结束后残余 buffer（尾部 data 行无 \n，原实现从不解析）——
                # 修复尾 token 丢失：残余行按与完整行相同逻辑处理（含 [DONE]/
                # finish_reason 结束检查；解析失败按既有策略跳过，不抛错）。
                if buffer:
                    line_bytes = buffer.rstrip(b"\r\n")
                    buffer = b""
                    parsed, should_yield, stop = _process_sse_line(line_bytes)
                    if should_yield:
                        yield parsed
                    if stop:
                        done = True
                break  # 正常完成 (含 [DONE] 正常结束)

        except _CONNECTION_ERRORS as e:
            if attempt < max_attempts - 1:
                _logger.warning(
                    "流式连接错误 (尝试 %d/%d): %s — 重置客户端后重试",
                    attempt + 1, max_attempts, e,
                )
                await reset_async_client()
                continue
            _logger.error("流式连接错误，已重试 %d 次仍失败: %s", max_attempts, e)
            raise
        except (RateLimitError, APIError):
            raise
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except RuntimeError as e:
            # 事件循环关闭时的连接清理错误（非致命，可忽略）
            if "Event loop is closed" in str(e):
                _logger.debug("流式连接清理时事件循环已关闭: %s", e)
                return
            raise
        except Exception:
            _logger.exception("SSE 流式迭代器异常")
            raise
