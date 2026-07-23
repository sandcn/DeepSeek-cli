"""aiohttp Web UI 服务器 — 静态文件服务 + WebSocket 路由

提供 run_web_server() 入口，由 app.py --webui 启动。
WebSocket 处理逻辑委托给 ws_handler.handle_websocket。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path

from aiohttp import web

from ._termux import auto_open_browser
from .session import WEBChatSession
from .routing import handle_websocket
from ..tui.events.consumers import publish_output

_logger = logging.getLogger(__name__)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


# ═══════════════════════════════════════════════════════════════
# HTTP 路由
# ═══════════════════════════════════════════════════════════════

MIME_MAP = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".json": "application/json",
    ".map": "application/json",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".eot": "application/vnd.ms-fontobject",
    ".webp": "image/webp",
    ".wasm": "application/wasm",
    ".txt": "text/plain",
}


async def handle_static(request: web.Request) -> web.StreamResponse:
    """提供 static 目录下的静态文件（style.css, app.js 等）。

    安全限制：只允许访问 STATIC_DIR 内的文件，拒绝路径穿越。
    使用 pathlib.resolve() 校验路径合法性，杜绝字符串拼接绕过的可能。
    """
    filename = request.match_info.get("filename", "index.html")
    # 安全校验（优先于其他所有检查）：必须位于 STATIC_DIR 内
    try:
        static_dir = Path(STATIC_DIR).resolve()
        filepath = (static_dir / filename).resolve()
        filepath.relative_to(static_dir)
    except (ValueError, OSError):
        raise web.HTTPForbidden(reason="路径越界")
    # 文件存在性检查
    if not filepath.is_file():
        raise web.HTTPNotFound(reason="文件不存在")
    # 扩展名白名单检查（仅用于 MIME 映射，不在白名单的兜底为 application/octet-stream）
    ext = os.path.splitext(filename)[1].lower()
    if not ext and filename != "index.html":
        raise web.HTTPNotFound(reason="不支持的文件类型")
    content_type = MIME_MAP.get(ext, "application/octet-stream")
    loop = asyncio.get_running_loop()
    body = await loop.run_in_executor(None, filepath.read_bytes)
    # 缓存策略：HTML/CSS/JS 不缓存（开发迭代频繁），其他资源缓存 1 小时
    cache_seconds = 0 if ext in (".html", ".css", ".js", ".mjs") else 3600
    headers = {"Cache-Control": f"public, max-age={cache_seconds}"}
    return web.Response(body=body, content_type=content_type,
                        headers=headers,
                        charset="utf-8" if ext in (".html", ".css", ".js", ".mjs", ".json", ".map", ".svg", ".txt") else None)


# ═══════════════════════════════════════════════════════════════
# 服务器启动入口——async 版（兼容 app.py 的 asyncio.run 事件循环）
# ═══════════════════════════════════════════════════════════════

async def run_web_server(host: str = "0.0.0.0", port: int = 8080,
                          loaded_data: dict | None = None) -> None:
    """启动 Web UI 服务器（async 版，在现有事件循环中运行）。

    Args:
        host: 监听地址。默认 0.0.0.0（所有网络接口），
              生产环境建议设为 "127.0.0.1" 或使用 nginx 反向代理。
        port: 监听端口
        loaded_data: 从 --load 恢复的会话数据，可选
    """
    # ── 创建会话（使用 WEBChatSession，复用 ChatSession 全部功能） ──
    session = WEBChatSession()
    session.web_initialize()

    if loaded_data:
        sid = loaded_data.get("id")
        if sid:
            data = session.load(sid)
            if data:
                _logger.info("已恢复会话: %s", sid)

    # ── 创建关闭事件（必须抢在 app 创建之前，供 ws_handler exit 使用） ──
    shutdown_event = asyncio.Event()

    # ── 创建 aiohttp 应用 ──────────────────────────────────
    app = web.Application()
    app["session"] = session
    app["shutdown_event"] = shutdown_event

    @web.middleware
    async def _cors_middleware(request, handler):
        if request.method == "OPTIONS":
            return web.Response(headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, Authorization",
            })
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            # ★ 修复：HTTPException（如 403/404）以异常形式传播，
            #   不会经过 handler 后的 response.headers 赋值。
            #   在异常上设置 CORS 头后再重新抛出。
            exc.headers.setdefault("Access-Control-Allow-Origin", "*")
            raise
        response.headers["Access-Control-Allow-Origin"] = "*"
        return response
    app.middlewares.append(_cors_middleware)

    app.router.add_get("/", handle_static)
    app.router.add_get("/{filename:.+}", handle_static)
    app.router.add_get("/ws", handle_websocket)

    # ── 使用 AppRunner + TCPSite（不阻塞事件循环） ─────────
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    try:
        await site.start()
    except Exception:
        await runner.cleanup()
        raise

    host_display = host if host != "0.0.0.0" else "localhost"
    url = f"http://{host_display}:{port}"
    publish_output(f"\033[1;36m  Web UI: {url}\033[0m", level="raw")
    publish_output(f"\033[2m  按 Ctrl+C 停止服务器\033[0m", level="raw")

    # ── Termux：自动用浏览器打开 URL（委托给 _termux 模块） ──
    await auto_open_browser(url)

    publish_output("", level="raw")  # 空行分隔

    # ── 注册信号处理（优雅关闭） ─────────────────────
    _signal_registered = False
    _on_termux = bool(os.environ.get('TERMUX_VERSION'))

    def _on_shutdown():
        """信号回调：设置 shutdown_event 唤醒主协程，让 finally 执行 cleanup"""
        shutdown_event.set()

    # ★ Termux 修复：Android 系统会向后台进程发送 SIGTERM（进程生命周期管理），
    #   这不是用户意图，不应导致服务器退出。只在 Termux 下注册 SIGINT（Ctrl+C），
    #   跳过 SIGTERM。非 Termux 环境保持双信号注册不变。
    _signals_to_handle = [signal.SIGINT]
    if not _on_termux:
        _signals_to_handle.append(signal.SIGTERM)

    # 方式 1：asyncio 原生 add_signal_handler（首选）
    try:
        loop = asyncio.get_running_loop()
        for sig in _signals_to_handle:
            try:
                loop.add_signal_handler(sig, _on_shutdown)
            except (RuntimeError, ValueError):
                # 已注册过 → 先移除再重新注册
                try:
                    loop.remove_signal_handler(sig)
                    loop.add_signal_handler(sig, _on_shutdown)
                except (RuntimeError, ValueError, NotImplementedError):
                    pass
        _signal_registered = True
    except (NotImplementedError, RuntimeError, ValueError):
        _signal_registered = False

    # 方式 2：signal.signal 兜底（主线程中生效）
    if not _signal_registered:
        try:
            loop = asyncio.get_running_loop()
            signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(_on_shutdown))
        except (ValueError, RuntimeError):
            pass  # 不在主线程中时忽略
        # ★ Termux 兜底：将 SIGTERM 设为忽略，防止 Android 系统信号杀死进程
        if _on_termux:
            try:
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
            except (ValueError, RuntimeError):
                pass

    # 等待 shutdown 信号（Event.wait() 零延迟响应）
    try:
        await shutdown_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        shutdown_event.set()
    finally:
        try:
            await asyncio.wait_for(runner.cleanup(), timeout=10)
        except asyncio.TimeoutError:
            _logger.warning("服务器清理超时（10s），强制退出")
            os._exit(1)
        except Exception:
            _logger.exception("服务器清理异常")


__all__ = ["run_web_server"]
