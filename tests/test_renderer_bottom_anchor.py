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
        assert "\u7a7a模式" in display[-1]
        # 输入行位于状态栏上方两行（与时间分隔线之间）
        assert "\u8f93\u5165\u6d88\u606f" in display[-3] or display[-3].startswith("> ")
