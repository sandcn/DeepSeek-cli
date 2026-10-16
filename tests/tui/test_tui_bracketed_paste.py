"""括号粘贴（bracketed paste）端到端测试 — 解析 → 分发 → 输入缓冲。

覆盖：终端协商序列（``CSI ?2004h/l``）在会话生命周期中的启用/还原、
``InputParser`` 对 ``ESC[200~ … ESC[201~`` 的解析、``InputDispatcher``
把粘贴内容整段插入输入缓冲（内容中的换行不再触发提交）、以及配置开关键。
"""

from __future__ import annotations

import io
import os
import time
from pathlib import Path

from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class _FakeIO:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def read_with_timeout(self, timeout, fd=None):
        if self.pos >= len(self.data):
            return None
        chunk = self.data[self.pos:self.pos + 1]
        self.pos += 1
        return chunk

    def read_bulk(self, fd, max_bytes, timeout):
        if self.pos >= len(self.data):
            return None
        chunk = self.data[self.pos:self.pos + max_bytes]
        self.pos += len(chunk)
        return chunk

    def prepend_pending(self, data: bytes) -> None:
        self.data = data + self.data[self.pos:]
        self.pos = 0


def _make_dispatcher(pipe_r: int):
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


def _drain(dispatcher: InputDispatcher, rounds: int = 6, gap: float = 0.03) -> None:
    for _ in range(rounds):
        time.sleep(gap)
        dispatcher.process_events()


# ── 解析层 ────────────────────────────────────────────────

def test_parser_esc_sequence_reads_bracketed_paste():
    parser = InputParser(io=_FakeIO(b"[200~hi\x1b[201~"))
    event = parser._parse_escape_sequence(0)
    assert event.kind == "paste"
    assert event.char == "hi"


# ── 分发层（真实 pipe） ───────────────────────────────────

def test_bracketed_paste_inserted_without_submit():
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"\x1b[200~hello\nworld\x1b[201~")
    _drain(d)
    # 换行为粘贴内容的一部分，未被解释为 Enter 提交（缓冲仍保留全文）
    assert be.get_current_text() == "hello\nworld"
    os.close(r)
    os.close(w)


def test_bracketed_paste_single_char_arrives_as_paste():
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"\x1b[200~x\x1b[201~")
    _drain(d)
    assert be.get_current_text() == "x"
    os.close(r)
    os.close(w)


def test_bracketed_paste_then_typed_char():
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"\x1b[200~ab\x1b[201~")
    _drain(d)
    os.write(w, b"c")
    _drain(d)
    assert be.get_current_text() == "abc"
    os.close(r)
    os.close(w)


def test_plain_multi_char_without_markers_still_pastes():
    """未协商括号粘贴的终端：多字符突发仍按粘贴整段插入（兼容旧行为）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"abcdef")
    _drain(d)
    assert be.get_current_text() == "abcdef"
    os.close(r)
    os.close(w)


# ── 会话生命周期（终端协商序列） ───────────────────────────

def test_session_terminal_modes_enable_and_restore():
    from src.tui.ink._render_api import _SimpleModel
    from src.tui.ink.session import InkSession

    buf = _TTYBuffer()
    session = InkSession(model=_SimpleModel(), stream=buf)
    session.set_terminal_modes(bracketed_paste=True, mouse=True)
    assert "\x1b[?2004h" in buf.getvalue()
    assert "\x1b[?1006h" in buf.getvalue()
    session.exit_terminal_modes()
    out = buf.getvalue()
    assert "\x1b[?2004l" in out
    assert "\x1b[?1006l" in out
    session.stop()


def test_session_set_raw_mode_reports_support():
    from src.tui.ink._render_api import _SimpleModel
    from src.tui.ink.session import InkSession

    session = InkSession(model=_SimpleModel(), stream=io.StringIO())
    session.set_interactive(True, stdin_fd=None)
    assert session.interactive is True
    assert session.raw_mode_supported is False      # 无 fd → 不支持，安全降级
    assert session.set_raw_mode(True) is False
    session.stop()


# ── 配置开关键 ────────────────────────────────────────────

def test_config_defaults_and_spec():
    from src.config.defaults import CONFIG_KEYS, DEFAULTS

    assert DEFAULTS["tui_bracketed_paste"] is True
    spec = CONFIG_KEYS["TUI_BRACKETED_PASTE"]
    assert spec["rc_path"] == ("tui_bracketed_paste",)
    assert spec["default"] is True


def test_tui_config_bracketed_paste_default_and_override():
    from src.tui._config import TuiConfig

    assert TuiConfig.defaults().bracketed_paste is True
    assert TuiConfig.defaults().with_overrides(bracketed_paste=False).bracketed_paste is False


def test_rc_bracketed_paste_reader_falls_back():
    from src.tui._assembly_steps import _rc_bracketed_paste

    assert isinstance(_rc_bracketed_paste(), bool)


def test_input_facade_delegates_mouse_fallback():
    from src.tui._input import Input

    r, w = os.pipe()
    try:
        inp = Input(fd=r)
    except Exception:  # pragma: no cover - 环境不支持时跳过
        os.close(r)
        os.close(w)
        return
    seen = []
    inp.set_mouse_fallback_callback(seen.append)
    inp.set_mouse_fallback_callback(None)
    os.close(r)
    os.close(w)
