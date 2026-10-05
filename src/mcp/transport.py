"""MCP 传输层 — stdio / Streamable HTTP / 旧式 HTTP+SSE。

三种传输共用同一接口（request / notify / close），上层 McpClient 只依赖该接口：

- StdioTransport  — 启动 MCP server 子进程，经 stdin/stdout 交换换行分隔的
  JSON-RPC 报文（stderr 仅作日志转发）。
- HttpTransport   — Streamable HTTP（2025-06-18）：每个请求一次 HTTP POST，
  响应可为 application/json 或 text/event-stream；会话 ID 经 Mcp-Session-Id 头。
- SseTransport    — 旧式 HTTP+SSE（2024-11-05，已废弃但仍有服务端部署）：
  GET 建立 SSE 长连接，首个 endpoint 事件给出 POST 端点，响应经 SSE 回流。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx

from .config import McpServerConfig
from .errors import McpProtocolError, McpTransportError
from .protocol import (
    HEADER_PROTOCOL_VERSION,
    HEADER_SESSION_ID,
    METHOD_INITIALIZE,
    METHOD_NOT_FOUND,
    NOTIFICATION_INITIALIZED,
    NOTIFICATION_TOOLS_LIST_CHANGED,
    PROTOCOL_VERSION,
    build_initialize_params,
    is_notification,
    is_request,
    is_response,
    make_error_response,
    make_notification,
    make_request,
    negotiate_protocol_version,
    parse_message,
    serialize_message,
)

_logger = logging.getLogger(__name__)

_ACCEPT_STREAM = "application/json, text/event-stream"

# ── stdio 子进程环境变量白名单 ────────────────────────────
# 第三方 MCP server 子进程默认不继承完整 os.environ（避免 CHAT_API_KEY 等
# 凭据外泄给外部包）；仅透传进程运行必需的基础变量，其余需在 server 配置的
# ``env`` 显式声明，或设 ``inherit_env: true`` 恢复继承全部环境变量。
_ENV_WHITELIST: tuple[str, ...] = (
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC",
    "TEMP", "TMP", "TMPDIR",
    "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "USERSID",
    "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "ALLUSERSPROFILE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
    "USER", "USERNAME", "LOGNAME",
    "SHELL", "TERM", "TERM_PROGRAM", "LANG", "LC_ALL", "LC_CTYPE",
    "TZ", "PWD",
    "NODE_OPTIONS", "NVM_DIR", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME",
    "UV_CACHE_DIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
)
_ENV_WHITELIST_UPPER: frozenset = frozenset(name.upper() for name in _ENV_WHITELIST)


def _child_env(cfg: McpServerConfig) -> dict:
    """构造 stdio 子进程环境变量（白名单继承 + 配置显式覆盖）。"""
    if cfg.inherit_env:
        env = dict(os.environ)
    else:
        env = {k: v for k, v in os.environ.items() if k.upper() in _ENV_WHITELIST_UPPER}
    env.update(cfg.env)
    return env


def _log_notification(server_name: str, msg: dict) -> None:
    """记录服务端通知：工具列表变更需显式提示（当前不热刷新注册表）。"""
    method = msg.get("method")
    if method == NOTIFICATION_TOOLS_LIST_CHANGED:
        _logger.info(
            "MCP server '%s' 报告工具列表已变更（当前会话不热刷新，"
            "重启 chat 进程后生效）", server_name,
        )
    else:
        _logger.debug("MCP server '%s' 通知: %s", server_name, method)


def create_transport(cfg: McpServerConfig):
    """按配置创建传输实例。

    「一切皆插件」：传输实现经 ``src.mcp.transport_registry`` 的注册表路由
    （每个内置传输是清单中的独立插件条目，可被 Patch/Overlay 禁用或替换）；
    注册表不可用时回退内置 ``if/elif`` 分派。
    """
    try:
        from .transport_registry import resolve_transport

        transport = resolve_transport(cfg.transport, cfg)
        if transport is not None:
            return transport
    except Exception:
        _logger.debug("传输注册表解析失败，回退内置分派", exc_info=True)
    if cfg.transport == "stdio":
        return StdioTransport(cfg)
    if cfg.transport == "sse":
        return SseTransport(cfg)
    return HttpTransport(cfg)


class _BaseTransport:
    """传输基类 — 定义统一接口与协议版本协商状态。"""

    def __init__(self, cfg: McpServerConfig):
        self._cfg = cfg
        self._timeout = cfg.timeout
        self.protocol_version = PROTOCOL_VERSION
        self.server_info: dict = {}
        self.instructions: str = ""
        self._closed = False

    async def start(self) -> None:
        raise NotImplementedError

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        raise NotImplementedError

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        raise NotImplementedError

    async def initialize(self) -> dict:
        """执行 MCP 初始化握手，返回服务端 initialize 结果。"""
        result = await self.request(METHOD_INITIALIZE, build_initialize_params())
        if not isinstance(result, dict):
            raise McpProtocolError("initialize 响应不是对象")
        server_version = result.get("protocolVersion")
        self.protocol_version = negotiate_protocol_version(server_version)
        if isinstance(server_version, str) and server_version != self.protocol_version:
            _logger.warning(
                "MCP server '%s' 返回不受支持的协议版本 '%s'，"
                "按客户端首选版本 %s 继续通信",
                self._cfg.name, server_version, self.protocol_version,
            )
        info = result.get("serverInfo")
        self.server_info = info if isinstance(info, dict) else {}
        instructions = result.get("instructions")
        self.instructions = instructions if isinstance(instructions, str) else ""
        await self.notify(NOTIFICATION_INITIALIZED, {})
        return result


# ═══════════════════════════════════════════════════════════════
# stdio 传输
# ═══════════════════════════════════════════════════════════════

class StdioTransport(_BaseTransport):
    """stdio 传输：MCP server 作为子进程，报文按换行分隔。"""

    def __init__(self, cfg: McpServerConfig):
        super().__init__(cfg)
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._next_id = 0
        self._write_lock: Optional[asyncio.Lock] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._proc is not None:
            return  # 幂等：重复 start 不重复拉起子进程
        env = _child_env(self._cfg)
        cwd = self._cfg.cwd or None
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._cfg.command, *self._cfg.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
        except (OSError, ValueError) as e:
            raise McpTransportError(
                f"启动 MCP server 子进程失败: {self._cfg.command} ({e})"
            ) from e
        except NotImplementedError as e:  # pragma: no cover - 平台不支持子进程
            raise McpTransportError(f"当前平台不支持 asyncio 子进程: {e}") from e

        self._write_lock = asyncio.Lock()
        self._reader_task = asyncio.ensure_future(self._read_loop())
        self._stderr_task = asyncio.ensure_future(self._stderr_loop())

    async def _read_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        try:
            while True:
                line = await stdout.readline()
                if not line:
                    break
                msg = parse_message(line)
                if msg is None:
                    _logger.debug("MCP(stdio:%s) 收到非法报文: %r", self._cfg.name, line[:200])
                    continue
                if is_response(msg):
                    self._resolve(msg)
                elif is_request(msg):
                    await self._reply_method_not_found(msg)
                elif is_notification(msg):
                    _log_notification(self._cfg.name, msg)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("MCP(stdio:%s) 读循环异常", self._cfg.name, exc_info=True)
        finally:
            self._fail_pending(f"MCP server '{self._cfg.name}' 连接已关闭")

    async def _stderr_loop(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                _logger.debug(
                    "MCP(stdio:%s) stderr: %s",
                    self._cfg.name, line.decode("utf-8", errors="replace").rstrip(),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.debug("MCP(stdio:%s) stderr 读循环异常", self._cfg.name, exc_info=True)

    def _resolve(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            _logger.debug("MCP(stdio:%s) 收到无主响应 id=%r", self._cfg.name, msg.get("id"))
            return
        error = msg.get("error")
        if isinstance(error, dict):
            fut.set_exception(McpProtocolError(
                str(error.get("message") or "JSON-RPC error"),
                code=error.get("code"),
                data=error.get("data"),
            ))
        else:
            fut.set_result(msg.get("result"))

    def _fail_pending(self, message: str) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(McpTransportError(message))
        self._pending.clear()

    async def _reply_method_not_found(self, msg: dict) -> None:
        try:
            await self._write(make_error_response(
                msg.get("id"), METHOD_NOT_FOUND,
                f"客户端不支持服务端请求: {msg.get('method')}",
            ))
        except Exception:
            _logger.debug("MCP(stdio:%s) 回复 method-not-found 失败", self._cfg.name, exc_info=True)

    async def _write(self, msg: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpTransportError(f"MCP server '{self._cfg.name}' 未启动")
        payload = (serialize_message(msg) + "\n").encode("utf-8")
        lock = self._write_lock
        try:
            if lock is None:
                self._proc.stdin.write(payload)
                await self._proc.stdin.drain()
                return
            async with lock:
                self._proc.stdin.write(payload)
                await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            raise McpTransportError(
                f"MCP server '{self._cfg.name}' 写入失败（进程可能已退出）: {e}"
            ) from e

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        if self._closed or self._proc is None:
            raise McpTransportError(f"MCP server '{self._cfg.name}' 未连接")
        self._next_id += 1
        request_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self._write(make_request(request_id, method, params))
        except Exception:
            self._pending.pop(request_id, None)
            raise
        wait = self._timeout if timeout is None else timeout
        try:
            return await asyncio.wait_for(fut, wait)
        except asyncio.TimeoutError as e:
            self._pending.pop(request_id, None)
            raise McpTransportError(
                f"MCP 请求超时（{wait}s）: {self._cfg.name}.{method}"
            ) from e
        except asyncio.CancelledError:
            self._pending.pop(request_id, None)
            raise

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        if self._closed or self._proc is None:
            raise McpTransportError(f"MCP server '{self._cfg.name}' 未连接")
        await self._write(make_notification(method, params))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fail_pending(f"MCP server '{self._cfg.name}' 已关闭")
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: B014 - 清理路径吞掉所有异常
                    pass
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            _logger.debug("MCP(stdio:%s) 关闭 stdin 异常", self._cfg.name, exc_info=True)
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            except Exception:
                _logger.debug("MCP(stdio:%s) terminate 异常", self._cfg.name, exc_info=True)
            try:
                await asyncio.wait_for(proc.wait(), 5.0)
            except (asyncio.TimeoutError, Exception):  # noqa: B014 - 超时后强杀
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except Exception:
                        _logger.debug("MCP(stdio:%s) kill 异常", self._cfg.name, exc_info=True)
                    try:
                        await asyncio.wait_for(proc.wait(), 5.0)
                    except Exception:
                        _logger.debug("MCP(stdio:%s) 等待进程退出超时", self._cfg.name)


# ═══════════════════════════════════════════════════════════════
# Streamable HTTP 传输
# ═══════════════════════════════════════════════════════════════

async def _iter_sse_messages(resp: httpx.Response):
    """从 SSE 响应流中逐条产出 JSON-RPC 报文。

    按 SSE 规范处理：`data:` 行累积到空行后组成一条事件（支持多行 data），
    `:` 注释行与 `event:`/`id:`/`retry:` 字段忽略。
    """
    data_lines: list = []

    def _flush():
        if not data_lines:
            return None
        payload = "\n".join(data_lines)
        data_lines.clear()
        if not payload or payload == "[DONE]":
            return None
        return parse_message(payload)

    async for raw_line in resp.aiter_lines():
        line = raw_line.rstrip("\r")
        if line == "":
            msg = _flush()
            if msg is not None:
                yield msg
            continue
        if line.startswith(":"):
            continue  # SSE 注释
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    msg = _flush()
    if msg is not None:
        yield msg


class HttpTransport(_BaseTransport):
    """Streamable HTTP 传输（MCP 2025-06-18）。"""

    def __init__(self, cfg: McpServerConfig):
        super().__init__(cfg)
        self._client: Optional[httpx.AsyncClient] = None
        self._session_id: Optional[str] = None
        self._next_id = 0

    def _headers(self, extra: Optional[dict] = None) -> dict:
        # 先放用户自定义头，再由协议必需头覆盖（防 Accept/Content-Type 被配置弱化）
        headers: dict = {}
        headers.update(self._cfg.headers)
        headers["Accept"] = _ACCEPT_STREAM
        headers["Content-Type"] = "application/json"
        if self._session_id:
            headers[HEADER_SESSION_ID] = self._session_id
        headers[HEADER_PROTOCOL_VERSION] = self.protocol_version
        if extra:
            headers.update(extra)
        return headers

    async def start(self) -> None:
        if self._client is not None:
            return  # 幂等：重复 start 不重建客户端（测试注入 mock 客户端场景）
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))

    async def _post(self, msg: dict, timeout: Optional[float]) -> tuple[Any, bool]:
        """发送一条报文。

        Returns:
            (结果, 是否为请求响应)。通知（无 id）返回 (None, False)。
        """
        if self._client is None:
            raise McpTransportError(f"MCP server '{self._cfg.name}' 未启动")
        wait = self._timeout if timeout is None else timeout
        expect_response = "id" in msg
        try:
            async with self._client.stream(
                "POST", self._cfg.url, json=msg, headers=self._headers(),
                timeout=httpx.Timeout(wait),
            ) as resp:
                session_id = resp.headers.get(HEADER_SESSION_ID)
                if session_id:
                    self._session_id = session_id

                if resp.status_code == 404 and self._session_id:
                    self._session_id = None
                    raise McpTransportError(
                        f"MCP 会话已失效（HTTP 404）: {self._cfg.name}"
                    )
                if resp.status_code == 202:
                    if expect_response:
                        raise McpTransportError(
                            f"MCP 服务端对请求返回 202（无响应体）: {self._cfg.name}"
                        )
                    return None, False
                if resp.status_code >= 400:
                    body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                    raise McpTransportError(
                        f"MCP HTTP {resp.status_code}: {body or '无响应体'}"
                    )

                content_type = resp.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    async for event in _iter_sse_messages(resp):
                        if is_notification(event):
                            _log_notification(self._cfg.name, event)
                            continue
                        if is_request(event):
                            _logger.info(
                                "MCP(http:%s) 收到服务端请求 %s（Streamable HTTP 客户端"
                                "不支持服务端请求，已忽略）",
                                self._cfg.name, event.get("method"),
                            )
                            continue
                        if not expect_response:
                            continue
                        if is_response(event):
                            return self._unwrap(event), True
                    if expect_response:
                        raise McpTransportError(
                            f"MCP SSE 流结束但未收到响应: {self._cfg.name}"
                        )
                    return None, False

                raw = await resp.aread()
                parsed = parse_message(raw)
                if parsed is None:
                    if not expect_response:
                        return None, False
                    raise McpTransportError(f"MCP 响应非法 JSON: {raw[:200]!r}")
                if not expect_response:
                    return None, False
                return self._unwrap(parsed), True
        except (httpx.HTTPError, OSError) as e:
            raise McpTransportError(f"MCP HTTP 请求失败: {self._cfg.name} ({e})") from e

    @staticmethod
    def _unwrap(msg: dict) -> Any:
        error = msg.get("error")
        if isinstance(error, dict):
            raise McpProtocolError(
                str(error.get("message") or "JSON-RPC error"),
                code=error.get("code"),
                data=error.get("data"),
            )
        return msg.get("result")

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        self._next_id += 1
        result, _ = await self._post(make_request(self._next_id, method, params), timeout)
        return result

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        await self._post(make_notification(method, params), None)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        client = self._client
        self._client = None
        if client is None:
            return
        session_id = self._session_id
        if session_id:
            try:
                await client.delete(
                    self._cfg.url,
                    headers={HEADER_SESSION_ID: session_id, HEADER_PROTOCOL_VERSION: self.protocol_version},
                    timeout=httpx.Timeout(5.0),
                )
            except Exception:
                _logger.debug("MCP(http:%s) 关闭会话异常", self._cfg.name, exc_info=True)
        try:
            await client.aclose()
        except Exception:
            _logger.debug("MCP(http:%s) 关闭客户端异常", self._cfg.name, exc_info=True)


# ═══════════════════════════════════════════════════════════════
# 旧式 HTTP+SSE 传输
# ═══════════════════════════════════════════════════════════════

class SseTransport(_BaseTransport):
    """旧式 HTTP+SSE 传输（2024-11-05）：GET 长连接 + endpoint 事件 + POST 回执。"""

    def __init__(self, cfg: McpServerConfig):
        super().__init__(cfg)
        self._client: Optional[httpx.AsyncClient] = None
        self._endpoint: Optional[str] = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._next_id = 0
        self._reader_task: Optional[asyncio.Task] = None
        # ★ 惰性创建：asyncio.Event() 构造依赖当前事件循环（Python 3.9），
        #   非 async 上下文构造会绑定到错误循环（跨循环 await 报错）。
        self._ready: Optional[asyncio.Event] = None
        self._endpoint_error: str = ""

    async def start(self) -> None:
        if self._client is not None:
            return  # 幂等：重复 start 不重建长连接
        self._ready = asyncio.Event()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        self._reader_task = asyncio.ensure_future(self._read_loop())
        try:
            await asyncio.wait_for(self._ready.wait(), self._timeout)
        except asyncio.TimeoutError as e:
            await self.close()
            raise McpTransportError(
                f"MCP SSE 端点协商超时（{self._timeout}s）: {self._cfg.name}"
            ) from e
        if self._endpoint is None:
            detail = f"（{self._endpoint_error}）" if self._endpoint_error else ""
            await self.close()
            raise McpTransportError(
                f"MCP SSE 未返回 endpoint 事件{detail}: {self._cfg.name}"
            )

    async def _read_loop(self) -> None:
        client = self._client
        if client is None:
            return
        ready = self._ready
        headers = {"Accept": "text/event-stream"}
        headers.update(self._cfg.headers)
        try:
            async with client.stream("GET", self._cfg.url, headers=headers) as resp:
                if resp.status_code >= 400:
                    raise McpTransportError(f"MCP SSE GET HTTP {resp.status_code}")
                event_name = "message"
                async for raw_line in resp.aiter_lines():
                    line = raw_line.rstrip("\r")
                    if not line:
                        event_name = "message"
                        continue
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if event_name == "endpoint":
                        resolved = self._resolve_endpoint(data)
                        if resolved is None:
                            self._endpoint_error = (
                                f"endpoint 与配置 url 不同源，已拒绝: {data}"
                            )
                        else:
                            self._endpoint = resolved
                        if ready is not None:
                            ready.set()
                        continue
                    msg = parse_message(data)
                    if msg is None:
                        continue
                    if is_response(msg):
                        self._resolve(msg)
                    elif is_notification(msg):
                        _log_notification(self._cfg.name, msg)
                    elif is_request(msg):
                        await self._reply_method_not_found(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not self._endpoint_error:
                self._endpoint_error = str(e)
            _logger.warning("MCP(sse:%s) 读循环异常", self._cfg.name, exc_info=True)
        finally:
            if ready is not None:
                ready.set()
            self._fail_pending(f"MCP server '{self._cfg.name}' SSE 连接已关闭")

    async def _reply_method_not_found(self, msg: dict) -> None:
        try:
            await self._post(make_error_response(
                msg.get("id"), METHOD_NOT_FOUND,
                f"客户端不支持服务端请求: {msg.get('method')}",
            ))
        except Exception:
            _logger.debug("MCP(sse:%s) 回复 method-not-found 失败", self._cfg.name, exc_info=True)

    def _resolve_endpoint(self, data: str) -> Optional[str]:
        """解析 endpoint 事件；与配置 url 不同源时返回 None（防凭据外发）。"""
        from urllib.parse import urljoin, urlsplit
        try:
            resolved = urljoin(self._cfg.url, data)
            base = urlsplit(self._cfg.url)
            target = urlsplit(resolved)
        except ValueError:
            return None
        if (base.scheme, base.hostname, base.port) != (target.scheme, target.hostname, target.port):
            _logger.warning(
                "MCP(sse:%s) endpoint 与配置 url 不同源，已拒绝: %s",
                self._cfg.name, resolved,
            )
            return None
        return resolved

    def _resolve(self, msg: dict) -> None:
        fut = self._pending.pop(msg.get("id"), None)
        if fut is None or fut.done():
            return
        error = msg.get("error")
        if isinstance(error, dict):
            fut.set_exception(McpProtocolError(
                str(error.get("message") or "JSON-RPC error"),
                code=error.get("code"),
                data=error.get("data"),
            ))
        else:
            fut.set_result(msg.get("result"))

    def _fail_pending(self, message: str) -> None:
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(McpTransportError(message))
        self._pending.clear()

    async def _post(self, msg: dict) -> None:
        if self._client is None or self._endpoint is None:
            raise McpTransportError(f"MCP server '{self._cfg.name}' 未就绪")
        headers = dict(self._cfg.headers)
        headers["Content-Type"] = "application/json"
        try:
            resp = await self._client.post(self._endpoint, json=msg, headers=headers)
        except (httpx.HTTPError, OSError) as e:
            raise McpTransportError(f"MCP SSE POST 失败: {self._cfg.name} ({e})") from e
        if resp.status_code >= 400:
            raise McpTransportError(
                f"MCP SSE POST HTTP {resp.status_code}: {resp.text[:200]}"
            )

    async def request(self, method: str, params: Optional[dict] = None,
                      timeout: Optional[float] = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            await self._post(make_request(request_id, method, params))
        except Exception:
            self._pending.pop(request_id, None)
            raise
        wait = self._timeout if timeout is None else timeout
        try:
            return await asyncio.wait_for(fut, wait)
        except asyncio.TimeoutError as e:
            self._pending.pop(request_id, None)
            raise McpTransportError(
                f"MCP SSE 请求超时（{wait}s）: {self._cfg.name}.{method}"
            ) from e
        except asyncio.CancelledError:
            # 与 StdioTransport.request 对齐：取消时清掉 pending，避免残留 Future
            self._pending.pop(request_id, None)
            raise

    async def notify(self, method: str, params: Optional[dict] = None) -> None:
        await self._post(make_notification(method, params))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fail_pending(f"MCP server '{self._cfg.name}' 已关闭")
        if self._reader_task is not None and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - 清理路径
                pass
        self._reader_task = None
        client = self._client
        self._client = None
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                _logger.debug("MCP(sse:%s) 关闭客户端异常", self._cfg.name, exc_info=True)


__all__ = [
    "create_transport",
    "StdioTransport",
    "HttpTransport",
    "SseTransport",
]
