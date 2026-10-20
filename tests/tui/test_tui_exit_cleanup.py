"""TUI 退出清理回归测试。

覆盖两轮修复：

1. 2026-10 review：退出时最后一帧的底部栏（输入区/状态栏）残留，
   ``Goodbye!`` 覆盖首行后其余行仍留在屏幕上（实测 ``░░░░░░] 1.8%``）——
   当时的修复是「退出前清屏」。
2. 2026-10-20（用户需求）：退出时**不再清屏**，改为把「再见   恢复:
   python chat.py --load <sid>」打印在**全部 TUI 内容下面一行**：
   - ``prepare_exit`` 只 flush（不再 ``full_clear``）；
   - 提示文本经 ``InkSession.set_exit_note`` 登记，``stop()`` 在渲染线程
     结束后由 ``InkRenderer.emit_exit_note`` 输出到文档最后一行下方一行；
   - 交互式模式不再追加 ``Goodbye!``（见 ``AppMode.shows_goodbye``）。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from src.tui import _lifecycle

try:
    import pyte
except ImportError:  # pragma: no cover - 环境未安装 pyte 时跳过终端模拟用例
    pyte = None


# ═══════════════════════════════════════════════════════════
# 1. prepare_exit：保留 TUI 内容（不清屏）
# ═══════════════════════════════════════════════════════════

def test_ink_session_prepare_exit_keeps_frame():
    """退出准备只刷出最终帧，**不得**清屏（清屏会抹掉整屏 TUI 与退出提示）。"""
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    calls: list[str] = []
    session._ink_renderer = SimpleNamespace(
        full_clear=lambda: calls.append("clear"),
        flush=lambda: calls.append("flush"),
    )
    session.prepare_exit()
    assert calls == ["flush"]


def test_ink_session_prepare_exit_swallows_error():
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)

    def _boom():
        raise OSError("no tty")

    session._ink_renderer = SimpleNamespace(flush=_boom, full_clear=_boom)
    session.prepare_exit()  # 非关键路径：异常不传播


# ═══════════════════════════════════════════════════════════
# 2. set_exit_note / stop：提示输出在 TUI 下方一行
# ═══════════════════════════════════════════════════════════

def _stop_with_stubs(monkeypatch, session):
    """在桩环境下调用 ``InkSession.stop()``（不启动渲染线程/终端交互）。"""
    from src.tui.ink.session import InkSession

    monkeypatch.setattr(InkSession, "_join_render_thread", lambda self: True)
    monkeypatch.setattr(InkSession, "_drain_queue_safe", lambda self, **kw: None)
    monkeypatch.setattr(InkSession, "exit_terminal_modes", lambda self: None)
    session._render_thread = None
    session._system_monitor = None
    session.stop()


def test_stop_emits_registered_exit_note(monkeypatch):
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    notes: list[str] = []
    session._ink_renderer = SimpleNamespace(
        suspend=lambda: None,
        emit_exit_note=lambda text: notes.append(text),
    )
    session.set_exit_note("再见   恢复: python chat.py --load abc123")

    _stop_with_stubs(monkeypatch, session)
    assert notes == ["再见   恢复: python chat.py --load abc123"]


def test_stop_does_not_repeat_exit_note(monkeypatch):
    """幂等：提示输出后清空，重复 stop 不重复打印。"""
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    notes: list[str] = []
    session._ink_renderer = SimpleNamespace(
        suspend=lambda: None,
        emit_exit_note=lambda text: notes.append(text),
    )
    session.set_exit_note("再见")
    _stop_with_stubs(monkeypatch, session)
    _stop_with_stubs(monkeypatch, session)
    assert notes == ["再见"]


def test_set_exit_note_ignores_empty():
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    session._exit_note = None
    for value in ("", "   ", None):
        session.set_exit_note(value)
        assert session._exit_note is None


def test_stop_without_note_writes_nothing(monkeypatch):
    from src.tui.ink.session import InkSession

    session = object.__new__(InkSession)
    notes: list[str] = []
    session._ink_renderer = SimpleNamespace(
        suspend=lambda: None,
        emit_exit_note=lambda text: notes.append(text),
    )
    _stop_with_stubs(monkeypatch, session)
    assert notes == []


# ═══════════════════════════════════════════════════════════
# 3. InkRenderer.emit_exit_note：定位在文档最后一行下方
# ═══════════════════════════════════════════════════════════

def _frame(lines: list[str]):
    from src.tui.ink import Line, StyledRun
    from src.tui.ink.output import Frame

    return Frame([Line([StyledRun(text, None)]) for text in lines])


class TestEmitExitNote:
    def test_writes_below_document_without_clear(self):
        from src.tui.ink.renderer import InkRenderer

        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(8)]))
        before = len(r.stream.getvalue())
        r.emit_exit_note("再见   恢复: python chat.py --load abc")
        out = r.stream.getvalue()[before:]
        assert out == "\r\n再见   恢复: python chat.py --load abc\r\n"
        assert "\x1b[2J" not in out, "退出提示不得清屏"

    def test_moves_from_cursor_to_document_bottom_first(self):
        """光标停在输入行（文档中部）时先下移到最后一行再换行。"""
        from src.tui.ink.renderer import InkRenderer

        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(8)]))
        # 模拟 place_cursor 把光标放到文档第 6 行（屏幕第 22 行）
        r._cursor_row = 22
        before = len(r.stream.getvalue())
        r.emit_exit_note("bye")
        out = r.stream.getvalue()[before:]
        # 文档底部在第 24 行：先 cursor_down(2)，再换行写提示
        assert out.startswith("\x1b[2B")
        assert out.endswith("\r\nbye\r\n")
        assert "\x1b[2J" not in out

    def test_empty_text_is_noop(self):
        from src.tui.ink.renderer import InkRenderer

        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame(["A"]))
        before = len(r.stream.getvalue())
        r.emit_exit_note("")
        assert r.stream.getvalue()[before:] == ""

    def test_height_unknown_still_writes_relative(self):
        from src.tui.ink.renderer import InkRenderer

        r = InkRenderer(stream=io.StringIO(), height=0)
        r.render(_frame(["A", "B"]))
        before = len(r.stream.getvalue())
        r.emit_exit_note("bye")
        assert r.stream.getvalue()[before:] == "\r\nbye\r\n"


# ═══════════════════════════════════════════════════════════
# 4. 端到端（pyte）：提示在全部 TUI 内容下面一行、界面不清屏
# ═══════════════════════════════════════════════════════════

class _FixedSizeCache:
    """固定尺寸的终端尺寸缓存桩。"""

    def __init__(self, width: int = 80, height: int = 24) -> None:
        self.width = width
        self.height = height

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def poll(self, ttl: float = 0.0) -> None:
        return None


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestExitNoteEndToEnd:
    def _run(self, note: str = "再见   恢复: python chat.py --load abc123"):
        from src.tui._config import TuiConfig
        from src.tui.ink import Line, StyledRun
        from src.tui.ink.element import h
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        from src.tui.app.app import App

        height, width = 30, 80
        screen = pyte.Screen(width, height)
        term = pyte.Stream(screen)
        for i in range(3):
            term.feed(f"shell-{i}\r\n")
        term.feed("$ python chat.py\r\n")
        cursor_row = screen.cursor.y + 1

        model = AppModel()
        model.width = width
        model.committed_lines.append(Line([StyledRun("  \u2726 v2.2.0", None)]))
        model.committed_lines.append(Line([StyledRun(" ", None)]))

        out = io.StringIO()
        session = InkSession(
            model=model,
            build_tree=lambda m, w: h(App, {"model": m, "width": w}),
            stream=out,
            width_cache=_FixedSizeCache(width, height),
            config=TuiConfig.defaults(),
        )
        session._ink_renderer.set_cursor_row_provider(lambda: cursor_row)
        session._render_frame_impl()
        session.set_exit_note(note)
        session.stop()
        term.feed(out.getvalue())
        return screen, out.getvalue()

    def test_note_is_one_line_below_last_tui_line(self):
        screen, raw = self._run()
        display = [line.rstrip() for line in screen.display]
        note_rows = [i for i, line in enumerate(display) if "再见" in line]
        assert len(note_rows) == 1, f"退出提示行数异常: {note_rows!r}"
        row = note_rows[0]
        # 提示行上面一行必须是非空的 TUI 内容（状态栏 = 文档最后一行）
        assert display[row - 1].strip(), "提示不在 TUI 内容正下方（上一行为空）"
        assert "空模式" in display[row - 1] or display[row - 1].strip()
        # 提示文本完整（含恢复命令行）
        assert "python chat.py --load abc123" in display[row]
        # 保存完整：TUI 未被清屏，标题仍在
        titles = [line for line in display if line.startswith("\u2726 DeepSeek CLI")]
        assert len(titles) == 1, f"退出时 TUI 被清屏/标题丢失: {titles!r}"
        assert display[0].startswith("shell-0")
        # 全程无清屏序列
        assert "\x1b[2J" not in raw


# ═══════════════════════════════════════════════════════════
# 5. 生命周期顺序：flush → prepare_exit → stop（既有契约）
# ═══════════════════════════════════════════════════════════

class _Engine:
    def __init__(self, calls: list, *, fail_prepare: bool = False):
        self._calls = calls
        self._line_tracker = None
        self._fail_prepare = fail_prepare

    def flush(self) -> None:
        self._calls.append("flush")

    def prepare_exit(self) -> None:
        self._calls.append("prepare")
        if self._fail_prepare:
            raise OSError("flush failed")

    def stop(self) -> None:
        self._calls.append("stop")


def _stop_lifecycle(engine, monkeypatch):
    monkeypatch.setattr(_lifecycle, "flush_history_disk", lambda timeout: None)
    lc = _lifecycle.TuiLifecycle(engine, object(), object(), object(), object())
    lc._started = True
    lc.stop()


def test_lifecycle_stop_prepares_exit_before_stop(monkeypatch):
    calls: list = []
    _stop_lifecycle(_Engine(calls), monkeypatch)
    assert calls == ["flush", "prepare", "stop"]


def test_lifecycle_stop_continues_when_prepare_exit_fails(monkeypatch):
    calls: list = []
    _stop_lifecycle(_Engine(calls, fail_prepare=True), monkeypatch)
    assert calls == ["flush", "prepare", "stop"]


def test_lifecycle_stop_without_prepare_exit_still_stops(monkeypatch):
    calls: list = []

    class _OldEngine:
        _line_tracker = None

        def flush(self):
            calls.append("flush")

        def stop(self):
            calls.append("stop")

    _stop_lifecycle(_OldEngine(), monkeypatch)
    assert calls == ["flush", "stop"]
