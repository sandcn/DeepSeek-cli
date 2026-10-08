"""termios 读-改-写互斥测试（``TERMIOS_LOCK`` / ``termios_noncanonical``）。

背景（回归）：终端属性是「整台终端一份」的共享状态，多个持有者各自执行
「保存快照 → 临时修改 → 恢复快照」（首帧 CPR 光标行查询临时非规范模式、
EscapeMonitor 进入 cbreak、RawModeController、kitty 能力查询）。序列交错时
后完成者的「恢复」会用**陈旧快照**覆盖先完成者的设置——实测启动首帧的
光标行查询把 EscapeMonitor 刚设置的 cbreak 恢复回规范模式，导致字符级输入
失效（输入不逐键回显、Tab 补全不弹出）且按键被内核回显污染界面。

本测试锁定：① ``termios_lock`` 可重入；② ``termios_noncanonical`` 只在
必要时修改且退出恢复；③ **并发 cbreak 不被查询恢复覆盖**（核心回归）。
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from src._compat_termios import (
    HAS_TERMIOS,
    TERMIOS_LOCK,
    termios_lock,
    termios_noncanonical,
)

pytestmark = pytest.mark.skipif(not HAS_TERMIOS, reason="termios 不可用（Windows 平台）")


class _FakeOut:
    """最小 stdout 替身（isatty=True + write/flush 记录）。"""

    def __init__(self, fileno: int):
        self._fileno = fileno
        self.data = ""

    def fileno(self) -> int:
        return self._fileno

    def write(self, text: str) -> int:
        self.data += text
        return len(text)

    def flush(self) -> None:
        pass


def _open_pty():
    master, slave = os.openpty()
    return master, slave


class TestTermiosLock:
    def test_reentrant(self):
        with termios_lock():
            with termios_lock():
                pass

    def test_lock_is_rlock(self):
        assert TERMIOS_LOCK.acquire(blocking=False)
        try:
            # 同线程可再次获取（RLock 语义）
            assert TERMIOS_LOCK.acquire(blocking=False)
            TERMIOS_LOCK.release()
        finally:
            TERMIOS_LOCK.release()


class TestTermiosNoncanonical:
    def test_noop_when_already_noncanonical(self, monkeypatch):
        """已处于 cbreak（ICANON/ECHO 已关）时不改动 termios。"""
        import termios
        import tty

        master, slave = _open_pty()
        try:
            tty.setcbreak(slave)
            snapshot = termios.tcgetattr(slave)
            calls = []
            monkeypatch.setattr(
                termios, "tcsetattr",
                lambda fd, when, attrs: calls.append(fd),
            )
            with termios_noncanonical(slave) as modified:
                assert modified is False
            assert calls == []
            assert termios.tcgetattr(slave) == snapshot
        finally:
            os.close(slave)
            os.close(master)

    def test_enters_and_restores_when_canonical(self):
        """规范模式下临时关闭 ICANON/ECHO，退出恢复原属性。"""
        import termios

        master, slave = _open_pty()
        try:
            before = termios.tcgetattr(slave)
            assert before[3] & termios.ICANON
            assert before[3] & termios.ECHO
            with termios_noncanonical(slave) as modified:
                assert modified is True
                during = termios.tcgetattr(slave)
                assert not (during[3] & termios.ICANON)
                assert not (during[3] & termios.ECHO)
            assert termios.tcgetattr(slave) == before
        finally:
            os.close(slave)
            os.close(master)

    def test_invalid_fd_yields_false(self):
        with termios_noncanonical(-1) as modified:
            assert modified is False


class TestQueryCursorRowTermiosSafety:
    def _patch_stdout(self, monkeypatch, fileno: int):
        from src.tui import _screen

        fake = _FakeOut(fileno)
        monkeypatch.setattr(_screen.sys, "__stdout__", fake)
        return fake

    def test_zero_termios_writes_when_cbreak(self, monkeypatch):
        """已是 cbreak 时查询不写 termios（零副作用）。"""
        import termios
        import tty

        from src.tui._screen import query_cursor_row

        master, slave = _open_pty()
        try:
            tty.setcbreak(slave)
            self._patch_stdout(monkeypatch, slave)
            writes = []
            monkeypatch.setattr(
                termios, "tcsetattr",
                lambda fd, when, attrs: writes.append((fd, when)),
            )
            assert query_cursor_row(stdin_fd=slave, timeout=0.05) is None
            assert writes == []
        finally:
            os.close(slave)
            os.close(master)

    def test_concurrent_cbreak_survives_query(self, monkeypatch):
        """核心回归：查询窗口内设置的 cbreak 不被查询的恢复覆盖。

        无 ``TERMIOS_LOCK`` 时：查询（0.2s 窗口）结束用进入前的规范模式快照
        恢复 → 覆盖并发设置的 cbreak → 最终终端回到规范模式（输入失效）。
        并发设置走 ``RawModeController``（持 ``TERMIOS_LOCK``），与查询串行化。
        """
        import termios

        from src.tui._screen import query_cursor_row
        from src.tui.ink.terminal import RawModeController

        master, slave = _open_pty()
        try:
            before = termios.tcgetattr(slave)
            assert before[3] & termios.ICANON
            self._patch_stdout(monkeypatch, slave)

            result = {}

            def _query():
                result["row"] = query_cursor_row(stdin_fd=slave, timeout=0.2)

            query_thread = threading.Thread(target=_query, daemon=True)
            query_thread.start()

            # 等待查询线程持锁进入（探测锁被占用）
            deadline = time.monotonic() + 1.0
            held = False
            while time.monotonic() < deadline:
                if TERMIOS_LOCK.acquire(blocking=False):
                    TERMIOS_LOCK.release()
                    time.sleep(0.005)
                    continue
                held = True
                break
            assert held, "查询线程未进入（未持 TERMIOS_LOCK）"

            # 并发设置 cbreak（RawModeController 持锁 → 阻塞等查询释放）
            controller = RawModeController(slave)
            assert controller.enable() is True
            query_thread.join(timeout=2.0)
            assert not query_thread.is_alive()
            assert result.get("row") is None

            after = termios.tcgetattr(slave)
            assert not (after[3] & termios.ICANON), "cbreak 被查询恢复覆盖"
            assert not (after[3] & termios.ECHO), "cbreak 被查询恢复覆盖"
        finally:
            os.close(slave)
            os.close(master)


class TestEscapeMonitorTermiosLock:
    def test_apply_and_restore_use_lock(self, monkeypatch):
        """EscapeMonitor 的 cbreak 设置/恢复序列在 TERMIOS_LOCK 内执行。"""
        import sys
        import termios
        import tty as tty_mod

        from src.api.escape_monitor._monitor import EscapeMonitor

        master, slave = _open_pty()
        try:
            class _FakeStdin:
                def fileno(self):
                    return slave

            monkeypatch.setattr(sys, "stdin", _FakeStdin())

            # apply 路径经 ``tty.setcbreak``（from-import 绑定，patch 模块属性生效）
            observed_apply = []
            real_setcbreak = tty_mod.setcbreak

            def _spy_setcbreak(fd, *args, **kwargs):
                observed_apply.append(TERMIOS_LOCK._is_owned())
                return real_setcbreak(fd, *args, **kwargs)

            monkeypatch.setattr(tty_mod, "setcbreak", _spy_setcbreak)

            monitor = EscapeMonitor(input_instance=object())
            monitor.apply_monitor_settings()
            assert observed_apply and all(observed_apply), "apply_monitor_settings 未持锁"
            assert not (termios.tcgetattr(slave)[3] & termios.ICANON)

            # restore 路径经 ``termios.tcsetattr``（模块属性查找，patch 生效）
            observed_restore = []
            real_tcsetattr = termios.tcsetattr

            def _spy_tcsetattr(fd, when, attrs):
                observed_restore.append(TERMIOS_LOCK._is_owned())
                return real_tcsetattr(fd, when, attrs)

            monkeypatch.setattr(termios, "tcsetattr", _spy_tcsetattr)

            monitor.restore_terminal_settings()
            assert observed_restore and all(observed_restore), "restore_terminal_settings 未持锁"
            assert termios.tcgetattr(slave)[3] & termios.ICANON
        finally:
            os.close(slave)
            os.close(master)
