"""终端光标在窗口底部时启动 TUI 的渲染锚定回归测试（2026-10-06）。

bug：shell 提示符位于终端最后一行（屏幕已满）时启动 ``python chat.py``，
TUI 渲染错乱——标题行重复、内容与上方 shell 历史错位、底部输入区/状态栏
位置异常、启动首屏显示不全。

根因：非全屏模型从**当前光标位置**追加写入，而渲染器坐标模型以「文档顶部
= 屏幕第 1 行」（``_screen_offset`` 钳制 >= 0）为基准——光标贴底时首帧实际
把文档写在屏幕底部（逐行写入触发滚动），``_buf_h = 文档行数`` 与真实物理
缓冲不符，``_cursor_row`` 与真实光标偏移 ``height - doc_h`` 行；后续增量帧
的相对定位在「假设坐标」与真实屏幕之间逐帧漂移（标题被反复写到不同位置
形成重复行、底部区错位）。

修复：
  1. ``_write_full`` 首帧先绝对定位到文档起始行
     ``max(1, height - doc_h + 1)``（文档底部贴屏幕底部），并置
     ``_buf_h = max(height, doc_h)``、``_top_aligned = False``——坐标模型
     与实际落位一致；
  2. ``_to_screen`` 改用 ``_effective_offset``（含物理缓冲漂移、可为负），
     文档矮于屏幕时文档整体偏下 ``height - doc_h`` 行。
"""

from __future__ import annotations

import io

import pytest

from src.tui.ink.output import Frame
from src.tui.ink.renderer import InkRenderer
from src.tui.ink import Line, StyledRun

try:
    import pyte
except ImportError:  # pragma: no cover - 环境未安装 pyte 时跳过终端模拟用例
    pyte = None


def _frame(lines: list[str]) -> Frame:
    return Frame([Line([StyledRun(text, None)]) for text in lines])


class TestWriteFullBottomAnchor:
    """首帧全量写入锚定屏幕底部（height>0）。"""

    def test_short_document_anchors_to_bottom(self):
        """文档矮于屏幕：定位到 height-doc_h+1 行（底部贴底）+ 底部对齐。"""
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(8)]))
        assert r._stream.getvalue().startswith("\x1b[17;1H")
        assert r._buf_h == 24
        assert r._top_aligned is False
        assert r._cursor_row == 24

    def test_document_equal_height(self):
        """文档恰等于屏幕高度：定位到第 1 行，底部对齐。"""
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(24)]))
        assert r._stream.getvalue().startswith("\x1b[1;1H")
        assert r._buf_h == 24
        assert r._cursor_row == 24

    def test_tall_document_positions_at_top(self):
        """文档高于屏幕：定位到第 1 行，``_buf_h`` = 文档行数，光标贴底。"""
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(40)]))
        assert r._stream.getvalue().startswith("\x1b[1;1H")
        assert r._buf_h == 40
        assert r._top_aligned is False
        assert r._cursor_row == 24

    def test_height_zero_keeps_legacy_document_coords(self):
        """height=0（未知/测试场景）：不定位、仍顶部对齐（原行为零回归）。"""
        r = InkRenderer(stream=io.StringIO(), height=0)
        r.render(_frame(["A", "B"]))
        assert not r._stream.getvalue().startswith("\x1b[")
        assert r._buf_h == 2
        assert r._top_aligned is True
        assert r._cursor_row == 2

    def test_empty_frame_unchanged(self):
        """空帧：``_buf_h`` 归零，不产生定位序列。"""
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(Frame([]))
        assert r._stream.getvalue() == ""
        assert r._buf_h == 0
        assert r._top_aligned is True


class TestToScreenBottomAligned:
    """``_to_screen`` 在底部对齐 + 文档矮于屏幕时映射偏下（用 effective 偏移）。"""

    def _render_bottom(self, doc_h: int, height: int = 24) -> InkRenderer:
        r = InkRenderer(stream=io.StringIO(), height=height)
        r.render(_frame([f"L{i}" for i in range(doc_h)]))
        return r

    def test_short_document_offsets_downward(self):
        r = self._render_bottom(8, 24)
        assert r._to_screen(1, 8) == 17
        assert r._to_screen(8, 8) == 24

    def test_tall_document_visible_tail(self):
        r = self._render_bottom(40, 24)
        assert r._to_screen(17, 40) == 1
        assert r._to_screen(40, 40) == 24

    def test_bottom_row_matches_screen_bottom(self):
        r = self._render_bottom(8, 24)
        assert r._bottom_row(8) == 24

    def test_top_aligned_document_unaffected(self):
        """height=0 或未渲染时保持文档坐标（原行为零回归）。"""
        r = InkRenderer(stream=io.StringIO(), height=0)
        r.render(_frame(["A", "B"]))
        assert r._to_screen(1, 2) == 1
        assert r._to_screen(2, 2) == 2


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestBottomStartNoDuplicateTitle:
    """端到端：光标贴底启动 + 多帧渲染不再出现重复标题、界面贴底。"""

    def _build(self, height: int = 24, width: int = 80):
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import components as _components
        from src.tui.ink import _cursor
        from src.tui.app.model import AppModel
        from src.tui.app.app import App
        from src.tui.ink.element import h

        screen = pyte.Screen(width, height)
        stream = pyte.Stream(screen)
        for i in range(30):
            stream.feed(f"shell-{i}\r\n")
        stream.feed("$ python chat.py")

        model = AppModel()
        model.width = width
        model.committed_lines.append(Line([StyledRun("  \u2726 v2.2.0", None)]))
        model.committed_lines.append(Line([StyledRun(" ", None)]))
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=height)

        def render_once():
            el = h(App, {"model": model, "width": width})
            rec.render(root, el, width, height)
            frame = _components.render_frame(root, width)
            before = len(renderer._stream.getvalue())
            renderer.render(frame)
            fiber = _cursor.find_input_fiber(root)
            if fiber is not None:
                _cursor.position_cursor(renderer, width, fiber)
            stream.feed(renderer._stream.getvalue()[before:])
            return frame

        return screen, render_once

    def test_title_unique_across_frames(self):
        screen, render_once = self._build()
        render_once()
        render_once()
        display = [line.rstrip() for line in screen.display]
        titles = [line for line in display if line.startswith("\u2726 DeepSeek CLI")]
        assert len(titles) == 1, f"标题重复: {titles!r}"

    def test_interface_anchored_to_screen_bottom(self):
        screen, render_once = self._build()
        render_once()
        display = [line.rstrip() for line in screen.display]
        # 底部状态栏（模式行）应贴屏幕最后一行
        assert display[-1].strip(), "状态栏行不应为空"
        assert "shell-" not in display[-1], "界面未贴屏幕底部（残留 shell 行）"
        # 输入行位于状态栏上方两行（与时间分隔线之间）
        assert "\u8f93\u5165\u6d88\u606f" in display[-3] or display[-3].startswith("> ")


# ═══════════════════════════════════════════════════════════
# 启动起始行锚定（用户需求：锚定「当前光标行 + 1」）
# ═══════════════════════════════════════════════════════════

class TestStartRowAnchor:
    """首个全量帧按「终端光标行 + 1」锚定文档起始行（查询失败回退底部锚定）。"""

    def _renderer_with_row(self, row, height=24, doc_h=3):
        r = InkRenderer(stream=io.StringIO(), height=height)
        r.set_cursor_row_provider(lambda: row)
        r.render(_frame([f"L{i}" for i in range(doc_h)]))
        return r

    def test_start_row_is_cursor_row_plus_one(self):
        r = self._renderer_with_row(row=5, height=24, doc_h=3)
        assert r._start_row == 6
        assert r._stream.getvalue().startswith("\x1b[6;1H")
        assert r._buf_h == 6 + 3 - 1
        assert r._top_aligned is False
        assert r._cursor_row == 8

    def test_to_screen_maps_from_start_row(self):
        r = self._renderer_with_row(row=5, height=24, doc_h=3)
        assert r._effective_offset(3) == 1 - 6
        assert r._to_screen(1, 3) == 6
        assert r._to_screen(3, 3) == 8
        assert r._bottom_row(3) == 8

    def test_cursor_on_last_line_rolls_and_switches_anchor(self):
        """光标在最后一行：定位钳制到屏幕内且文档立即贴底（转底部锚定）。"""
        r = self._renderer_with_row(row=24, height=24, doc_h=3)
        assert r._stream.getvalue().startswith("\x1b[24;1H")
        assert r._start_row is None
        assert r._buf_h == 24

    def test_provider_none_falls_back_to_bottom(self):
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.set_cursor_row_provider(lambda: None)
        r.render(_frame([f"L{i}" for i in range(8)]))
        assert r._start_row is None
        assert r._stream.getvalue().startswith("\x1b[17;1H")
        assert r._buf_h == 24

    def test_provider_exception_falls_back_to_bottom(self):
        def _boom():
            raise RuntimeError("no tty")

        r = InkRenderer(stream=io.StringIO(), height=24)
        r.set_cursor_row_provider(_boom)
        r.render(_frame([f"L{i}" for i in range(8)]))
        assert r._start_row is None
        assert r._buf_h == 24

    def test_provider_invalid_value_falls_back(self):
        for bad in (0, -3, "5", True, None):
            r = InkRenderer(stream=io.StringIO(), height=24)
            r.set_cursor_row_provider(lambda v=bad: v)
            r.render(_frame([f"L{i}" for i in range(8)]))
            assert r._start_row is None, f"非法值 {bad!r} 应回退底部锚定"

    def test_without_provider_keeps_bottom_anchor(self):
        r = InkRenderer(stream=io.StringIO(), height=24)
        r.render(_frame([f"L{i}" for i in range(8)]))
        assert r._start_row is None
        assert r._buf_h == 24

    def test_growth_keeps_start_row(self):
        r = self._renderer_with_row(row=5, height=24, doc_h=3)
        r.render(_frame([f"L{i}" for i in range(4)]))
        assert r._start_row == 6
        assert r._buf_h == 6 + 4 - 1
        assert r._effective_offset(4) == 1 - 6
        assert r._to_screen(4, 4) == 9

    def test_shrink_keeps_start_row(self):
        r = self._renderer_with_row(row=5, height=24, doc_h=4)
        r.render(_frame([f"L{i}" for i in range(3)]))
        assert r._start_row == 6
        assert r._buf_h == 6 + 3 - 1

    def test_growth_into_bottom_switches_anchor(self):
        r = self._renderer_with_row(row=20, height=24, doc_h=3)
        assert r._start_row == 21
        r.render(_frame([f"L{i}" for i in range(4)]))
        assert r._start_row is None
        assert r._buf_h == 24
        assert r._effective_offset(4) == 4 - 24

    def test_provider_consumed_only_once(self):
        """起始行锚定只在首个全量帧消费一次，resize 全量重写不再查询。"""
        calls = []

        def _provider():
            calls.append(1)
            return 5

        r = InkRenderer(stream=io.StringIO(), height=24)
        r.set_cursor_row_provider(_provider)
        r.render(_frame([f"L{i}" for i in range(3)]))
        assert calls == [1]
        r.reset(full=True)
        before = len(r._stream.getvalue())
        r.render(_frame([f"L{i}" for i in range(8)]))
        assert calls == [1]
        assert r._start_row is None
        # resize 后全量重写走底部锚定（定位到 height-doc_h+1）
        assert r._stream.getvalue()[before:].startswith("\x1b[17;1H")


class _FakeOut:
    def __init__(self):
        self.data = ""

    def fileno(self):
        return 3

    def write(self, text):
        self.data += text

    def flush(self):
        pass


class _FakeIn:
    def fileno(self):
        return 0


class TestQueryCursorRow:
    """DSR/CPR 光标行查询（``ESC[6n`` → ``ESC[row;colR``）。"""

    def _patch(self, monkeypatch, chunks, isatty=True):
        from src.tui import _screen
        import select as select_mod
        import termios as termios_mod
        import tty as tty_mod

        monkeypatch.setattr(_screen.os, "isatty", lambda fd: isatty)
        fake_out = _FakeOut()
        monkeypatch.setattr(_screen.sys, "__stdout__", fake_out)
        monkeypatch.setattr(_screen.sys, "stdin", _FakeIn())
        it = iter(chunks)
        monkeypatch.setattr(_screen.os, "read", lambda fd, n: next(it, b""))
        monkeypatch.setattr(
            select_mod, "select", lambda r, w, x, t: ([3], [], []),
        )
        monkeypatch.setattr(termios_mod, "tcgetattr", lambda fd: ["saved"])
        monkeypatch.setattr(
            termios_mod, "tcsetattr", lambda fd, when, attrs: None,
        )
        monkeypatch.setattr(tty_mod, "setraw", lambda fd: None)
        return fake_out

    def test_parses_row(self, monkeypatch):
        from src.tui._screen import query_cursor_row
        fake_out = self._patch(monkeypatch, [b"\x1b[12;5R"])
        assert query_cursor_row() == 12
        assert fake_out.data == "\x1b[6n"

    def test_parses_row_across_chunks(self, monkeypatch):
        from src.tui._screen import query_cursor_row
        self._patch(monkeypatch, [b"\x1b[", b"7;1", b"R"])
        assert query_cursor_row() == 7

    def test_no_response_returns_none(self, monkeypatch):
        from src.tui._screen import query_cursor_row
        self._patch(monkeypatch, [b""])
        assert query_cursor_row(timeout=0.01) is None

    def test_non_tty_returns_none(self, monkeypatch):
        from src.tui._screen import query_cursor_row
        self._patch(monkeypatch, [b"\x1b[1;1R"], isatty=False)
        assert query_cursor_row() is None


@pytest.mark.skipif(pyte is None, reason="pyte 未安装（终端模拟依赖）")
class TestStartRowAnchorEndToEnd:
    """端到端：光标行查询成功后 TUI 从光标下一行开始、无重复行。"""

    def _render(self, shell_lines: int, height: int = 30, width: int = 80):
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import components as _components
        from src.tui.ink import _cursor
        from src.tui.app.model import AppModel
        from src.tui.app.app import App
        from src.tui.ink.element import h

        screen = pyte.Screen(width, height)
        stream = pyte.Stream(screen)
        for i in range(shell_lines):
            stream.feed(f"shell-{i}\r\n")
        stream.feed("$ python chat.py")
        cursor_row = screen.cursor.y + 1

        model = AppModel()
        model.width = width
        model.committed_lines.append(Line([StyledRun("  \u2726 v2.2.0", None)]))
        model.committed_lines.append(Line([StyledRun(" ", None)]))
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=height)
        renderer.set_cursor_row_provider(lambda: cursor_row)

        for _ in range(3):
            el = h(App, {"model": model, "width": width})
            rec.render(root, el, width, height)
            frame = _components.render_frame(root, width)
            before = len(renderer._stream.getvalue())
            renderer.render(frame)
            fiber = _cursor.find_input_fiber(root)
            if fiber is not None:
                _cursor.position_cursor(renderer, width, fiber)
            stream.feed(renderer._stream.getvalue()[before:])
        return screen, cursor_row, renderer

    def test_starts_below_cursor_without_duplicates(self):
        screen, cursor_row, renderer = self._render(shell_lines=3)
        display = [line.rstrip() for line in screen.display]
        titles = [line for line in display if line.startswith("\u2726 DeepSeek CLI")]
        assert len(titles) == 1, f"标题重复: {titles!r}"
        # 文档自光标下一行开始（屏幕行 cursor_row+1 → 0-based index cursor_row）
        assert display[cursor_row].startswith("\u2726 DeepSeek CLI")
        assert renderer._start_row == cursor_row + 1

    def test_cursor_at_bottom_keeps_interface_visible(self):
        screen, _cursor_row, renderer = self._render(shell_lines=30)
        display = [line.rstrip() for line in screen.display]
        titles = [line for line in display if line.startswith("\u2726 DeepSeek CLI")]
        assert len(titles) == 1, f"标题重复: {titles!r}"
        # 界面贴屏幕底部（起始行超出屏幕 → 转底部锚定）
        assert display[-1].strip(), "状态栏行不应为空"
        assert "shell-" not in display[-1], "界面未贴屏幕底部（残留 shell 行）"
        assert renderer._start_row is None
