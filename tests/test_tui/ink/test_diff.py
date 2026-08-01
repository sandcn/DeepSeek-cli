"""测试 ink/diff.py + ink/renderer.py — 行级 diff 与非全屏渲染。

精确断言 ANSI/光标序列，Mock 输出流（StringIO），无终端依赖。
"""

from __future__ import annotations

import io

from src.tui.ink.diff import first_diff_line, height_delta
from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


class TestFirstDiffLine:
    """首差异行计算。"""

    def test_identical(self):
        assert first_diff_line(_frame("a", "b"), _frame("a", "b")) == -1

    def test_first_line_diff(self):
        assert first_diff_line(_frame("a", "b"), _frame("x", "b")) == 0

    def test_last_line_diff(self):
        assert first_diff_line(_frame("a", "b"), _frame("a", "y")) == 1

    def test_prev_shorter_prefix_equal(self):
        """prev 较短且前缀一致 → 返回 prev 高度。"""
        assert first_diff_line(_frame("a", "b"), _frame("a", "b", "c")) == 2

    def test_new_shorter_prefix_equal(self):
        """new 较短且前缀一致 → 返回 new 高度。"""
        assert first_diff_line(_frame("a", "b", "c"), _frame("a", "b")) == 2

    def test_empty_frames(self):
        assert first_diff_line(Frame(), Frame()) == -1
        assert first_diff_line(Frame(), _frame("a")) == 0

    def test_style_diff_detected(self):
        from src.tui.core.style import Style
        f1 = Frame([Line.of("a", Style(fg=45))])
        f2 = Frame([Line.of("a", Style(fg=46))])
        assert first_diff_line(f1, f2) == 0


class TestHeightDelta:
    def test_positive(self):
        assert height_delta(_frame("a"), _frame("a", "b")) == 1

    def test_negative(self):
        assert height_delta(_frame("a", "b"), _frame("a")) == -1


class TestInkRenderer:
    """InkRenderer 差异渲染。"""

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_first_render_writes_full(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        assert out.getvalue() == "\ra\n\rb\n"
        assert r.cursor_row == 3

    def test_identical_render_no_output(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "b"))
        assert out.getvalue() == ""

    def test_append_lines_writes_only_new(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "b", "c"))
        # 从第 3 行（首差异=2）写起：无光标上移 + \r 归位 + 一行 + 换行
        assert out.getvalue() == "\rc\x1b[K\n"

    def test_rewrite_from_diff_line(self):
        r, out = self._new()
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "b", "X"))
        # 首差异=2 → cursor_up(3-2=1) + "\rX\x1b[K\n"
        assert out.getvalue() == "\x1b[1A" + "\rX\x1b[K\n"

    def test_rewrite_earlier_line(self):
        r, out = self._new()
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "Y", "c"))
        # 首差异=1 → cursor_up(3-1=2) + "\rY\x1b[K\n" + "\rc\x1b[K\n"
        assert out.getvalue() == "\x1b[2A" + "\rY\x1b[K\n" + "\rc\x1b[K\n"

    def test_no_up_shift_after_place_cursor(self):
        """回归：place_cursor 将光标移到输入行后，下一帧重写不向上偏移。

        旧实现假设光标恒在文档底部+1（prev_h+1），但 place_cursor 把光标
        留在输入行；每帧重写因此上移 (prev_h+1 - input_row) 行 → 「每帧上移一行」。
        """
        r, out = self._new()
        # 首帧：5 行文档
        r.render(_frame("a", "b", "c", "d", "e"))
        # 输入行在第 5 行（row=5），列 2
        r.place_cursor(5, 2)
        assert r.cursor_row == 5
        out.seek(0)
        out.truncate()
        # 下一帧：仅第 5 行变化（0-based 第 4 行）
        r.render(_frame("a", "b", "c", "d", "E"))
        # 正确：从当前光标行 5 移动到目标行 5（i=4 → i+1=5），无上移
        # 旧 buggy：cursor_up(prev_h - i = 5-4 = 1) → 上移 1 行错位
        assert out.getvalue() == "\rE\x1b[K\n", (
            f"应无光标上移（直接重写输入行），实际: {out.getvalue()!r}"
        )

    def test_shrink_clears_residual(self):
        r, out = self._new()
        r.render(_frame("a", "b", "c", "d"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "b"))
        # 前缀一致、new 较短：首差异=2 → cursor_up(4-2=2) + 2 次残留行清除
        val = out.getvalue()
        assert val.startswith("\x1b[2A")
        # 残留行 2 行（prev_h=4, new_h=2）→ 2 次 clear_line + cursor_down
        assert val.count("\r\x1b[K") == 2
        assert val.count("\x1b[1B") == 2
        assert r.cursor_row == 5  # prev_h+1

    def test_place_cursor(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        out.seek(0)
        out.truncate()
        # 光标当前行 = 3（1-based），放置到 row=2, col=1
        r.place_cursor(2, 1)
        assert out.getvalue() == "\x1b[1A\r"
        assert r.cursor_row == 2

    def test_place_cursor_forward(self):
        r, out = self._new()
        r.render(_frame("a"))
        out.seek(0)
        out.truncate()
        r.place_cursor(1, 4)
        assert out.getvalue() == "\x1b[1A" + "\r" + "\x1b[3C"
        assert r.cursor_row == 1

    def test_suspend_resets_state(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        assert r.prev_frame is not None
        r.suspend()
        assert r.prev_frame is None
        assert r.cursor_row == 0

    def test_reset_then_full_rewrite(self):
        r, out = self._new()
        r.render(_frame("a", "b"))
        r.reset()
        out.seek(0)
        out.truncate()
        r.render(_frame("x"))
        assert out.getvalue() == "\rx\n"
