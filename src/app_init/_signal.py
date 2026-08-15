"""信号处理管理器 — 封装 SIGINT/SIGTERM/SIGHUP 处理和降级路径

从 app_init.py 拆分而来，与 _args.py / _session_cmd.py / main.py 协同
构成应用初始化包。
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading

from ..tui.events.consumers import publish_output
from ..api.escape_monitor import stop_active_monitor

_logger = logging.getLogger(__name__)

# ── 常量 ──
_SHUTDOWN_GRACE_PERIOD = 3.0


class SignalManager:
    """信号处理管理器 — 封装 SIGINT/SIGTERM/SIGHUP 处理和降级路径"""

    def __init__(self):
        self._registered: bool = False
        self._shutdown_requested = asyncio.Event()
        self._sigint_lock = threading.Lock()
        # ★ 程序已进入退出清理阶段（asyncio.run 的 shutdown_default_executor 等）。
        #   此后到达的信号不再取消任何任务，避免清理阶段任务被取消
        #   而抛出 CancelledError 裸 traceback（Python 3.9 已知行为）。
        self._exiting = threading.Event()

    @property
    def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    @property
    def is_exiting(self) -> bool:
        return self._exiting.is_set()

    def mark_exiting(self) -> None:
        """标记程序已进入退出清理阶段。

        应在主协程返回前（如 app_init/main.py 的 finally 中）调用，
        覆盖 asyncio.run() 清理阶段的信号窗口：此后的信号处理器
        直接忽略，不再取消任务，保证 shutdown_default_executor 等
        清理任务能完整执行。
        """
        self._exiting.set()

    async def handle_sigint(self) -> None:
        """处理 SIGINT — 首按优雅中断，再按强制关闭"""
        if self._exiting.is_set():
            _logger.info("SIGINT 忽略：程序已进入退出清理阶段")
            return

        from ..api.interrupt_async import request_interrupt_async

        with self._sigint_lock:
            if self._shutdown_requested.is_set():
                # 第二次按 Ctrl+C 直接强关，不再去抖
                publish_output("\n  ⚠ 强制关闭所有任务…", level="raw")
                stop_active_monitor()
                current = asyncio.current_task()
                if current is None:
                    # current_task() 返回 None：取消所有任务触发优雅关闭
                    # 替代 sys.exit(1)，避免 SystemExit 在 asyncio 中导致资源泄漏
                    for t in asyncio.all_tasks():
                        t.cancel()
                    return
                tasks_to_cancel = [
                    t for t in asyncio.all_tasks() if t is not current
                ]
                for t in tasks_to_cancel:
                    t.cancel()
                return

            self._shutdown_requested.set()

        # 锁外执行非关键路径
        publish_output("\n  ⚠ 正在中断…（再按一次 Ctrl+C 强制退出）", level="raw")
        request_interrupt_async()

        await asyncio.sleep(_SHUTDOWN_GRACE_PERIOD)

    async def shutdown(self) -> None:
        """SIGTERM 的优雅关闭 — 直接强制退出"""
        if self._exiting.is_set():
            _logger.info("SIGTERM 忽略：程序已进入退出清理阶段")
            return

        publish_output("\n  ⚠ 正在关闭…", level="raw")
        stop_active_monitor()
        current = asyncio.current_task()
        if current is None:
            import sys
            sys.exit(1)
        tasks = [t for t in asyncio.all_tasks() if t is not current]
        for t in tasks:
            t.cancel()
        # 不 await gather，不 stop loop

    async def handle_sighup(self) -> None:
        """处理 SIGHUP — 终端断开时优雅关闭
        
        pty 断开/Cygwin 会话变更/休眠恢复时触发，行为与 shutdown() 一致，
        但使用独立的提示信息以区分用户主动 SIGTERM 与 pty 断开 SIGHUP。
        """
        if self._exiting.is_set():
            _logger.info("SIGHUP 忽略：程序已进入退出清理阶段")
            return

        publish_output("\n  ⚠ 检测到终端断开(SIGHUP)，正在关闭…", level="raw")
        stop_active_monitor()
        current = asyncio.current_task()
        if current is None:
            # current_task() 返回 None：取消所有任务触发优雅关闭
            for t in asyncio.all_tasks():
                t.cancel()
            return
        tasks = [t for t in asyncio.all_tasks() if t is not current]
        for t in tasks:
            t.cancel()

    def register_handlers(self, loop=None) -> None:
        """注册 SIGINT/SIGTERM/SIGHUP 回调

        优先使用 asyncio 原生 add_signal_handler（与事件循环集成最佳），
        降级到 signal.signal + loop.call_soon_threadsafe。
        在 Termux 下 SIGTERM 设为忽略（Android 进程管理发来的非用户信号）。
        SIGHUP 在所有平台（含 Termux/Cygwin）均注册处理器，实现终端断开时优雅关闭。
        """
        if self._registered:
            return
        if loop is None:
            loop = asyncio.get_event_loop()
        _sigint_ok = False
        _sigterm_ok = False

        try:
            loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(self.handle_sigint()))
            _sigint_ok = True
        except NotImplementedError:
            pass

        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.shutdown()))
            _sigterm_ok = True
        except NotImplementedError:
            pass

        # ★ Bug1 修复：降级到 signal.signal（Windows / Android Termux 不支持 add_signal_handler）
        if not _sigint_ok:
            try:
                signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.handle_sigint())
                ))
            except (ValueError, RuntimeError):
                pass

        if not _sigterm_ok:
            try:
                if os.environ.get('TERMUX_VERSION'):
                    # ★ Termux 修复：Android 系统会向后台进程发送 SIGTERM，
                    # 这是进程生命周期管理信号，不是用户意图，不应退出服务器。
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                else:
                    signal.signal(signal.SIGTERM, lambda s, f: loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.shutdown())
                    ))
            except (ValueError, RuntimeError):
                pass

        # ── SIGHUP 处理器：终端断开/Cygwin pty 断开/休眠恢复时优雅关闭 ──
        if hasattr(signal, 'SIGHUP'):
            _sighup_ok = False
            try:
                loop.add_signal_handler(
                    signal.SIGHUP,
                    lambda: asyncio.create_task(self.handle_sighup()),
                )
                _sighup_ok = True
            except NotImplementedError:
                pass

            if not _sighup_ok:
                try:
                    # ★ SIGHUP 在任何平台都表示终端断开，Termux 也不忽略
                    signal.signal(
                        signal.SIGHUP,
                        lambda s, f: loop.call_soon_threadsafe(
                            lambda: asyncio.create_task(self.handle_sighup())
                        ),
                    )
                except (ValueError, RuntimeError):
                    pass

        self._registered = True
