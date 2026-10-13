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
from typing import AsyncIterator, Any, Optional

import httpx

from ..config import (
    HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT, HTTP_WRITE_TIMEOUT,
    HTTP_MAX_CONNECTIONS, HTTP_MAX_CONNECTIONS_PER_HOST, HTTP_KEEP_ALIVE_TIMEOUT,
    HTTP_ENABLE_POOL, HTTP_ENABLE_HTTP2, API_KEY,
)
from .errors import (  # noqa: F401  （re-export 保持既有导入路径兼容）
    APIError, RateLimitError, ServerError, classify_http_error, parse_retry_after,
)
from .errors import CONNECTION_ERRORS as _CONNECTION_ERRORS

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
# ★ 2026-08-20（稳定性修复）：模块级 asyncio.Lock() 在 import 时依赖事件循环
#   （Python 3.9 get_event_loop）——asyncio.run 之后的同进程内 import 本模块会抛
#   RuntimeError（xdist 全量测试偶发）。改为 None + 首次 async 使用惰性创建。
_clients_lock: Optional[asyncio.Lock] = None


def _get_clients_lock() -> asyncio.Lock:
    """惰性创建客户端池锁（仅 async 上下文调用，保证存在运行事件循环）。"""
    global _clients_lock
    if _clients_lock is None:
        _clients_lock = asyncio.Lock()
    return _clients_lock


async def get_async_client() -> httpx.AsyncClient:
    """按当前事件循环获取或创建 AsyncClient。

    每一事件循环持有独立的 httpx.AsyncClient，避免跨循环共享
    导致 asyncio 原语（Event/Lock）"bound to a different event loop" 错误。
    """
    loop = asyncio.get_running_loop()
    async with _get_clients_lock():
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
    async with _get_clients_lock():
        client = _clients.pop(loop, None)
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                _logger.debug("关闭 AsyncClient 异常（事件循环可能已关闭）", exc_info=True)





# ── 请求头 ──────────────────────────────────────────────────

_api_key_warned = False
_api_key_warned_lock = threading.Lock()


def _headers() -> dict[str, str]:
    global _api_key_warned
    if not API_KEY:
        with _api_key_warned_lock:
            if not _api_key_warned:
                _api_key_warned = True
                _logger.warning("未设置 API 密钥。请在 /models 模型档案中填写 API 密钥。")
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


def _headers_anthropic() -> dict[str, str]:
    """构建 Anthropic API 请求头（x-api-key 认证 + anthropic-version）。"""
    global _api_key_warned
    if not API_KEY:
        with _api_key_warned_lock:
            if not _api_key_warned:
                _api_key_warned = True
                _logger.warning("未设置 API 密钥。请在 /models 模型档案中填写 API 密钥。")
    return {
        "x-api-key": API_KEY,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }


# ── 响应检查 ────────────────────────────────────────────────

def _check_response(resp: httpx.Response) -> None:
    """非 200 响应统一转换为语义化异常（含 Retry-After 解析）。

    - 429 → RateLimitError（携带服务端建议的等待秒数）
    - 401/403 → AuthError；404 → NotFoundError；400/422 → InvalidRequestError
    - 5xx/408/425 → ServerError；其余未知状态码 → APIError
    """
    if resp.status_code == 200:
        return
    retry_after = parse_retry_after(resp.headers.get("retry-after"))
    raise classify_http_error(resp.status_code, resp.text[:500], retry_after=retry_after)


#: SSE 数据行前缀（字段名 ``data``，其后空格可选——见 _process_sse_line）
_SSE_DATA_PREFIX = b"data:"

# ── 响应内容编码（Content-Encoding）支持表 ──────────────────
# SSE 响应可能被服务端/网关压缩（DeepSeek 官方自 2026-10 起对
# /chat/completions 的流式响应返回 ``Content-Encoding: gzip``）。httpx 的
# ``aiter_bytes()`` 会按该头增量解码，但**对未知编码静默回退
# IdentityDecoder**（原样透传压缩字节）——此时 SSE 行匹配全部落空，退化成
# 「模型返回空响应」且无任何报错。这里取 httpx 解码器注册表用于提前识别，
# 私有 API 导入失败时保守回退到标准库可解码的集合。
try:  # pragma: no cover - 依赖 httpx 内部结构，仅作能力探测
    from httpx._decoders import SUPPORTED_DECODERS as _SUPPORTED_DECODERS
except Exception:  # pragma: no cover
    _SUPPORTED_DECODERS = {"identity": object, "gzip": object, "deflate": object}


def _unsupported_content_encodings(resp: httpx.Response) -> list[str]:
    """返回响应 ``Content-Encoding`` 中本地无法解码的编码（去重保序）。

    ``identity`` / 空值为无编码，不算不支持；编码列表按 HTTP 规范可逗号分隔
    （多重编码，如 ``gzip, br``）。
    """
    raw = resp.headers.get("content-encoding", "") or ""
    seen: list[str] = []
    for part in raw.split(","):
        enc = part.strip().lower()
        if not enc or enc == "identity" or enc in _SUPPORTED_DECODERS:
            continue
        if enc not in seen:
            seen.append(enc)
    return seen


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

    max_attempts = 10
    # ★ 修复重复渲染：标记是否已向调用方产出过 chunk。
    # 一旦已产出任何 chunk，断线时绝不能重启流（重启会从开头重新 yield
    # 已渲染过的内容 → 下游重复显示）；此时直接抛出连接错误，由
    # stream_call_async 保留已累积的部分内容。仅当未产出任何 chunk 时
    # （连接尚未建立 / 首个 SSE 块前断开）重启才是安全无损的。
    yielded_any = False
    for attempt in range(max_attempts):
        try:
            client = await get_async_client()
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                _check_response(resp)

                # ★ 本地无法解码的压缩编码（如未安装 brotli 时的 br）：httpx 会
                #   静默按 IdentityDecoder 透传压缩字节 → SSE 一行都匹配不到。
                #   提前显式报错（501 非可重试：重试不会改变结果），给出可操作
                #   提示，而不是让上层误判成「模型返回空响应」重试 10 次。
                unsupported = _unsupported_content_encodings(resp)
                if unsupported:
                    raise APIError(
                        501,
                        "服务端响应使用了本地不支持的压缩编码 "
                        f"{', '.join(unsupported)}（流式响应无法解析）；"
                        "请安装对应解码库（br → brotli，zstd → zstandard）",
                    )

                buffer = b""
                consecutive_failures = 0
                # 已收到的（解码后）响应体字节数：用于「收到数据却解析不出任何
                # SSE data 帧」的诊断告警（区分「服务端真的什么都没返回」与
                # 「返回了但本地没解析出来」）。
                received_bytes = 0

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
                    if not line_bytes.startswith(_SSE_DATA_PREFIX):
                        return None, False, False
                    data_bytes = line_bytes[len(_SSE_DATA_PREFIX):]
                    # SSE 规范：字段名 ``data`` 后是**可选的单个前导空格**。
                    # 此前只认 ``data: ``（带空格）——省略空格的 ``data:{...}``
                    # （部分 OpenAI 兼容网关这样发送）会被整条静默丢弃，再次
                    # 退化成「模型返回空响应」。两种写法都接受。
                    if data_bytes.startswith(b" "):
                        data_bytes = data_bytes[1:]
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
                    # ★ 错误帧检测（2026-10-09）：部分 OpenAI 兼容服务端在 HTTP
                    #   200 的 SSE 流里以 ``{"error": {...}}`` 帧报告失败（不在
                    #   HTTP 层抛错）。本函数此前把它当普通帧 yield——该帧没有
                    #   choices，管线解析后「无产出」，于是本轮被当作空回答
                    #   （占位「(无内容)」）静默结束。现改为抛语义化可重试异常：
                    #   服务端错误信息不再丢失，交由重试层决定重发。
                    error_payload = parsed.get("error") if isinstance(parsed, dict) else None
                    if error_payload:
                        if isinstance(error_payload, dict):
                            message = (error_payload.get("message")
                                       or error_payload.get("error")
                                       or str(error_payload))
                        else:
                            message = str(error_payload)
                        raise ServerError(503, f"流式响应错误帧: {message}")
                    # finish_reason 结束检查：非空时置 stop（先 yield 当前 chunk
                    # 再结束，确保最后一个 delta 被下游处理）。覆盖完整行与残余
                    # 行两条解析路径；对不发送 [DONE] 且连接不关闭的服务端，
                    # 避免流挂起至空闲超时。
                    return parsed, True, _has_finish_reason(parsed)

                # ★ 2026-10-13 修复（「模型返回空响应（无正文、无推理、无工具
                #   调用）」根因）：官方 API 的 SSE 响应带 ``Content-Encoding:
                #   gzip``（非流式路径走 resp.json() 有解码，故只有流式对话受害）。
                #   此前用 ``aiter_raw()`` 读到的是**压缩后的原始字节**——压缩流里
                #   没有以 ``data: `` 开头的行，全部被静默跳过 → 零 chunk → 管线
                #   判定「完全无产出」→ EmptyResponseError 重试 10 次仍空 → 用户
                #   看到「API 调用失败 … 模型返回空响应」，回答永久丢失。
                #   ``aiter_bytes()`` 按 Content-Encoding 增量解码（gzip/deflate/
                #   br/zstd；无编码时与原 aiter_raw() 行为等价），且解码是流式的
                #   （服务端 Z_SYNC_FLUSH 逐帧压缩，首 token 延迟不受影响）。
                async for chunk in resp.aiter_bytes():
                    if done:
                        break
                    received_bytes += len(chunk)
                    buffer += chunk
                    while b"\n" in buffer:
                        line_bytes, buffer = buffer.split(b"\n", 1)
                        parsed, should_yield, stop = _process_sse_line(line_bytes)
                        if should_yield:
                            yielded_any = True
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
                        yielded_any = True
                        yield parsed
                    if stop:
                        done = True
                # ★ 诊断（2026-10-13）：收到响应体却解析不出任何 SSE 帧——典型成因
                #   是 Content-Encoding 未被解码、服务端返回非 SSE 响应体、或 SSE
                #   行前缀不是 ``data: ``。上层会把本轮判为「空响应」并重试，这里
                #   留下可定位的 WARNING（否则故障完全静默，只能看到重试文案）。
                if not yielded_any and received_bytes > 0:
                    _logger.warning(
                        "流式响应收到 %d 字节但未解析出任何 SSE data 帧"
                        "（HTTP %s，Content-Encoding=%r）——按空响应处理，请检查"
                        "响应体是否为 SSE 且编码可解码",
                        received_bytes, resp.status_code,
                        resp.headers.get("content-encoding"),
                    )
                break  # 正常完成 (含 [DONE] 正常结束)

        except _CONNECTION_ERRORS as e:
            # ★ 修复重复渲染：已产出过 chunk 时不再重启（重启会从开头重新
            # yield 已渲染内容 → 下游重复显示），直接抛出连接错误，由
            # stream_call_async 保留已累积的部分内容。
            if not yielded_any and attempt < max_attempts - 1:
                _logger.warning(
                    "流式连接错误 (尝试 %d/%d): %s — 重置客户端后重试",
                    attempt + 1, max_attempts, e,
                )
                await reset_async_client()
                continue
            if yielded_any:
                _logger.warning(
                    "流式中途断开（已产出部分内容），放弃重启以避免重复渲染，"
                    "保留已接收内容: %s", e,
                )
            else:
                _logger.error("流式连接错误，已重试 %d 次仍失败: %s", max_attempts, e)
            raise
        except httpx.DecodingError as e:
            # 压缩流解码失败（响应体损坏 / 中途截断）：未产出任何内容时可安全
            # 重发 → 转成可重试语义交由重试层；已产出部分内容时不重启（避免
            # 重复渲染），保留已接收内容。
            if not yielded_any:
                _logger.warning("流式响应解码失败（未产出内容），交由重试层重试: %s", e)
                raise ServerError(503, f"流式响应解码失败（Content-Encoding）: {e}") from e
            _logger.warning("流式中途解码失败（已产出部分内容），保留已接收内容: %s", e)
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
