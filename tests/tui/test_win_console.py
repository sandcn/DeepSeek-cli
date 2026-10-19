"""原生 Windows 控制台输入后端（``src.tui._win_console``）回归测试。

背景：Windows 的 ``select`` 只支持 socket，控制台 stdin 会抛
``OSError(10038/10093)``——既有 select+os.read 输入路径在 cmd 下完全失效
（启动后无法输入）。本模块经 ``ReadConsoleInputW`` 读取按键事件并翻译为
TUI 解析器使用的 ANSI/UTF-8 字节流。

覆盖：
- 常用键翻译（字符 / Enter / Backspace / Tab / Shift+Tab / Esc）；
- 方向键、Home/End、Insert/Delete/PgUp/PgDn、F1-F12 与修饰键序列；
- Ctrl+字母（含 Ctrl+C 中断、Ctrl+H 与 Backspace 的区分）；
- Alt+字符（ESC 前缀）、重复次数、UTF-8 多字节字符；
- ``InputDispatcher.read_stdin_once`` 在控制台后端可用时优先走
  ``ReadConsoleInputW``（不再调用 Windows 上必然失败的 select）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from src.tui import _win_console
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser

_SHIFT = 0x0010
_LEFT_CTRL = 0x0008
_LEFT_ALT = 0x0002


# ── 键翻译 ────────────────────────────────────────────


def test_translate_basic_keys():
    """字符 / Enter / Backspace / Tab / Shift+Tab / Esc 翻译。"""
    assert _win_console.translate_key_event(0x41, "a", 0) == b"a"
    assert _win_console.translate_key_event(0x0D, "\r", 0) == b"\r"
    # Windows 控制台 Backspace 报告 0x08——必须映射为 0x7f，否则会被当作
    # Ctrl+H 打开轨迹视图。
    assert _win_console.translate_key_event(0x08, "\x08", 0) == b"\x7f"
    assert _win_console.translate_key_event(0x09, "\t", 0) == b"\t"
    assert _win_console.translate_key_event(0x09, "\t", _SHIFT) == b"\x1b[Z"
    assert _win_console.translate_key_event(0x1B, "\x1b", 0) == b"\x1b"


def test_translate_navigation_and_function_keys():
    """方向键 / Home / End / Delete / PgUp / F1 / F12 及 Ctrl+方向键。"""
    assert _win_console.translate_key_event(0x26, "", 0) == b"\x1b[A"
    assert _win_console.translate_key_event(0x28, "", 0) == b"\x1b[B"
    assert _win_console.translate_key_event(0x27, "", 0) == b"\x1b[C"
    assert _win_console.translate_key_event(0x25, "", 0) == b"\x1b[D"
    assert _win_console.translate_key_event(0x24, "", 0) == b"\x1b[H"
    assert _win_console.translate_key_event(0x23, "", 0) == b"\x1b[F"
    assert _win_console.translate_key_event(0x2E, "", 0) == b"\x1b[3~"
    assert _win_console.translate_key_event(0x21, "", 0) == b"\x1b[5~"
    assert _win_console.translate_key_event(0x70, "", 0) == b"\x1bOP"
    assert _win_console.translate_key_event(0x7B, "", 0) == b"\x1b[24~"
    # Ctrl+↑ / Shift+↑（xterm 修饰键参数：1 + Shift + 2*Alt + 4*Ctrl）
    assert _win_console.translate_key_event(0x26, "", _LEFT_CTRL) == b"\x1b[1;5A"
    assert _win_console.translate_key_event(0x26, "", _SHIFT) == b"\x1b[1;2A"


def test_translate_ctrl_alt_and_repeat():
    """Ctrl+字母 / Ctrl+H 与 Backspace 区分 / Alt 前缀 / 长按重复。"""
    assert _win_console.translate_key_event(0x43, "\x03", _LEFT_CTRL) == b"\x03"
    assert _win_console.translate_key_event(0x48, "\x08", _LEFT_CTRL) == b"\x08"
    assert _win_console.translate_key_event(0x41, "a", _LEFT_ALT) == b"\x1ba"
    assert _win_console.translate_key_event(0x41, "a", 0, repeat=3) == b"aaa"


def test_translate_unicode():
    """中文等多字节字符按 UTF-8 输出。"""
    assert _win_console.translate_key_event(0x4E, "中", 0) == "中".encode()


def test_translate_ignores_unknown_event():
    """无字符且非特殊键的事件不产生输出。"""
    assert _win_console.translate_key_event(0xE5, "", 0) == b""


# ── dispatcher 接线 ───────────────────────────────────


def _make_dispatcher():
    """构造真实 dispatcher（pipe fd 模拟 stdin）；返回 (d, be, r, w)。"""
    r, w = os.pipe()
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    d = InputDispatcher(io, be, InputParser(io=io))
    return d, be, r, w


def test_dispatcher_prefers_console_backend(monkeypatch):
    """控制台后端可用时经 ReadConsoleInputW 读取（select 不再参与）。"""
    d, be, r, w = _make_dispatcher()
    calls = {"read": 0}

    def _fake_read() -> bytes:
        calls["read"] += 1
        return b"a"

    def _fail_select(*_args, **_kwargs):
        # 模拟原生 Windows 的 select：控制台 fd 抛 WSAENOTSOCK。粘贴探测
        # 等内部路径会调用 select，须安全降级（不影响控制台字节分发）。
        raise OSError(10038, "select 不支持控制台句柄")

    # 控制台后端仅对 fd 0 生效（pipe fd 等嵌入式场景保持 select 路径）
    d._io.fd = 0
    monkeypatch.setattr(_win_console, "supported", lambda: True, raising=False)
    monkeypatch.setattr(_win_console, "read_bytes", _fake_read, raising=False)
    monkeypatch.setattr("select.select", _fail_select)
    try:
        assert d.read_stdin_once() is True
        assert calls["read"] == 1
        assert be.get_current_text() == "a"
    finally:
        os.close(r)
        os.close(w)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="原生 Windows select 不支持管道 fd；非控制台输入路径由 POSIX/Cygwin 用例覆盖",
)
def test_dispatcher_falls_back_to_select_when_console_unavailable(monkeypatch):
    """非控制台环境（重定向/其它平台）保持既有 select 路径。"""
    d, be, r, w = _make_dispatcher()
    monkeypatch.setattr(_win_console, "supported", lambda: False, raising=False)
    try:
        os.write(w, b"b")
        assert d.read_stdin_once() is True
        assert be.get_current_text() == "b"
    finally:
        os.close(r)
        os.close(w)
