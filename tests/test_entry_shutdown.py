"""入口退出路径与 SignalManager 退出清理阶段测试

背景：
- Python 3.9 的 asyncio.run() 在主协程返回后进入清理阶段
  （_cancel_all_tasks → shutdown_asyncgens → shutdown_default_executor）。
- shutdown_default_executor 会启动新线程等待默认线程池全部结束，
  期间若再次收到 SIGINT/SIGTERM/SIGHUP，SignalManager 会取消事件循环
  上的所有任务（含清理任务），导致 await future 抛出 CancelledError，
  从 asyncio.run() 冒出形成裸 traceback（chat.py:21）。

覆盖：
1. chat._run() 兜底：CancelledError → 0、KeyboardInterrupt → 130、
   正常 → 0，均不抛异常。
2. SignalManager.mark_exiting() 后信号处理器不再取消任务；
   未标记时第二次 SIGINT 仍执行强关（取消任务）保持原行为。
"""

import asyncio

import chat
from src.app_init._signal import SignalManager


# ═══════════════════════════════════════════════════════════════
# chat._run() 退出码兜底
# ═══════════════════════════════════════════════════════════════

class TestChatRun:
    def test_cancelled_error_swallowed(self, monkeypatch):
        def _fake_run(coro):
            coro.close()  # 消费 coroutine，避免 "never awaited" 警告
            raise asyncio.CancelledError()

        monkeypatch.setattr(chat.asyncio, "run", _fake_run)
        assert chat._run() == 0

    def test_keyboard_interrupt_maps_to_130(self, monkeypatch):
        def _fake_run(coro):
            coro.close()
            raise KeyboardInterrupt()

        monkeypatch.setattr(chat.asyncio, "run", _fake_run)
        assert chat._run() == 130

    def test_normal_return_returns_0(self, monkeypatch):
        def _fake_run(coro):
            coro.close()
            return None

        monkeypatch.setattr(chat.asyncio, "run", _fake_run)
        assert chat._run() == 0


# ═══════════════════════════════════════════════════════════════
# SignalManager 退出清理阶段信号抑制
# ═══════════════════════════════════════════════════════════════

class TestSignalManagerExiting:
    def test_mark_exiting_flag_and_idempotent(self):
        mgr = SignalManager()
        assert not mgr.is_exiting
        mgr.mark_exiting()
        assert mgr.is_exiting
        mgr.mark_exiting()  # 幂等
        assert mgr.is_exiting

    def test_sigint_ignored_after_mark_exiting(self):
        async def scenario():
            mgr = SignalManager()
            mgr._shutdown_requested.set()  # 模拟第一次 Ctrl+C 已请求中断
            other = asyncio.create_task(asyncio.sleep(10))
            mgr.mark_exiting()
            await mgr.handle_sigint()
            assert not other.cancelled()
            other.cancel()

        asyncio.run(scenario())

    def test_shutdown_ignored_after_mark_exiting(self):
        async def scenario():
            mgr = SignalManager()
            other = asyncio.create_task(asyncio.sleep(10))
            mgr.mark_exiting()
            await mgr.shutdown()
            assert not other.cancelled()
            other.cancel()

        asyncio.run(scenario())

    def test_sighup_ignored_after_mark_exiting(self):
        async def scenario():
            mgr = SignalManager()
            other = asyncio.create_task(asyncio.sleep(10))
            mgr.mark_exiting()
            await mgr.handle_sighup()
            assert not other.cancelled()
            other.cancel()

        asyncio.run(scenario())

    def test_second_sigint_still_cancels_before_exiting(self):
        async def scenario():
            mgr = SignalManager()
            mgr._shutdown_requested.set()  # 第二次 Ctrl+C → 强关
            other = asyncio.create_task(asyncio.sleep(10))
            await mgr.handle_sigint()
            await asyncio.sleep(0)  # 让取消请求传播到任务
            assert other.cancelled()

        asyncio.run(scenario())
