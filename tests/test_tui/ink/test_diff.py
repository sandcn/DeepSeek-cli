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


class TestFirstDiffLineStablePrefix:
    """first_diff_line 稳定前缀跳过（PERF-7）。"""

    def _mk(self, prefix_lines, tail_lines):
        """构造带 stable_prefix 的 Frame：前缀 + 尾部。

        prefix_lines 复用同一列表对象（模拟 committed 前缀复用）。
        """
        prefix = [Line.of(l) for l in prefix_lines]
        tail = [Line.of(l) for l in tail_lines]
        return Frame(
            prefix + tail,
            stable_prefix=prefix,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix),
        )

    def test_same_stable_prefix_skips_prefix(self):
        """两帧 stable_prefix 同一列表 → 跳过前缀，直接定位尾部差异。"""
        prev = self._mk(["c1", "c2", "c3"], ["status", "in1"])
        new = self._mk(["c1", "c2", "c3"], ["status", "in2"])
        # 前缀 3 行相同（同一对象跳过）→ 首差异在尾部第 5 行（0-based 4）
        assert first_diff_line(prev, new) == 4

    def test_same_stable_prefix_no_diff(self):
        """两帧 stable_prefix 同一列表且尾部相同 → 无差异。"""
        prev = self._mk(["c1", "c2"], ["tail"])
        new = self._mk(["c1", "c2"], ["tail"])
        assert first_diff_line(prev, new) == -1

    def test_same_stable_prefix_height_grow(self):
        """前缀相同 + 尾部增长 → 返回 prev 高度（从该行补写）。"""
        prev = self._mk(["c1", "c2"], ["t1"])
        new = self._mk(["c1", "c2"], ["t1", "t2"])
        # 前 3 行相同 → 首差异 = 3（prev 高度）
        assert first_diff_line(prev, new) == 3

    def test_diff_stable_prefix_object_scans_all(self):
        """stable_prefix 不同对象（committed 更新）→ 不跳过，正常逐行扫描。"""
        prev = self._mk(["c1", "c2"], ["tail"])
        # 重建 prefix（新列表对象）
        prefix2 = [Line.of("c1"), Line.of("c2")]
        new = Frame(
            prefix2 + [Line.of("tail")],
            stable_prefix=prefix2,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix2),
        )
        # 前缀行是不同 Line 对象但 runs 值相等 → 无差异
        assert first_diff_line(prev, new) == -1
        # 前缀内容变化 → 检测到
        prefix3 = [Line.of("C1"), Line.of("c2")]
        new3 = Frame(
            prefix3 + [Line.of("tail")],
            stable_prefix=prefix3,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix3),
        )
        assert first_diff_line(prev, new3) == 0

    def test_nonzero_offset(self):
        """非顶部（header 在 stable_prefix 之前）→ 从 offset 起跳过。"""
        header1 = [Line.of("h1"), Line.of("h2")]
        prefix1 = [Line.of("c1"), Line.of("c2"), Line.of("c3")]
        tail1 = [Line.of("status")]
        f1 = Frame(
            header1 + prefix1 + tail1,
            stable_prefix=prefix1,
            stable_prefix_offset=2,
            stable_prefix_len=len(prefix1),
        )
        # 第二帧：header 行是不同 Line 对象（值相同）→ 不跳过（仍逐行比较）；
        # 前缀同一对象 → 跳过
        header2 = [Line.of("h1"), Line.of("h2")]
        prefix2 = prefix1  # 复用同一列表对象
        tail2 = [Line.of("status2")]
        f2 = Frame(
            header2 + prefix2 + tail2,
            stable_prefix=prefix2,
            stable_prefix_offset=2,
            stable_prefix_len=len(prefix2),
        )
        # header 两行值相同（不跳过但仍比较），前缀跳过，首差异在尾部第 6 行
        assert first_diff_line(f1, f2) == 5

    def test_stable_prefix_mismatched_len_falls_back(self):
        """stable_prefix_len 不一致（布局变化）→ 不跳过，正常扫描。"""
        prefix = [Line.of("c1"), Line.of("c2")]
        f1 = Frame(
            prefix + [Line.of("a")],
            stable_prefix=prefix,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix),
        )
        f2 = Frame(
            prefix + [Line.of("b")],
            stable_prefix=prefix,
            stable_prefix_offset=0,
            stable_prefix_len=1,  # 与 f1 不一致 → 回退全量扫描
        )
        assert first_diff_line(f1, f2) == 2


class TestHeightDelta:
    def test_positive(self):
        assert height_delta(_frame("a"), _frame("a", "b")) == 1

    def test_negative(self):
        assert height_delta(_frame("a", "b"), _frame("a")) == -1


class TestRendererStablePrefixIntegration:
    """render_frame + first_diff_line stable_prefix 端到端集成（PERF-7）。

    验证带 stable_prefix 标记的 Frame 走 diff 路径时输出与逐行扫描一致。
    """

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_render_with_stable_prefix_tail_change(self):
        """带 stable_prefix 的大帧：尾部变化仅重写尾部行（前缀不动）。"""
        r, out = self._new()
        prefix = [Line.of(f"c{i}") for i in range(50)]
        tail = [Line.of("status"), Line.of("in1")]
        f1 = Frame(
            prefix + tail,
            stable_prefix=prefix,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix),
        )
        r.render(f1)
        out.seek(0)
        out.truncate()
        # 第二帧：前缀同对象复用，尾部输入行变化
        prefix2 = prefix  # 复用同一列表对象
        tail2 = [Line.of("status"), Line.of("in2")]
        f2 = Frame(
            prefix2 + tail2,
            stable_prefix=prefix2,
            stable_prefix_offset=0,
            stable_prefix_len=len(prefix2),
        )
        r.render(f2)
        val = out.getvalue()
        # 首差异 = 51（仅 in 行）→ 从第 52 行写起：上移 1 行 + in2 + 换行
        assert "in2" in val
        assert "c49" not in val  # 前缀不重写
        assert val.count("\x1b[K") == 1
        assert r.cursor_row == 53

    def test_render_stable_prefix_matches_unmarked(self):
        """stable_prefix 标记与未标记的 Frame 在内容一致时输出相同。"""
        r1, out1 = self._new()
        r2, out2 = self._new()
        # 构造内容完全一致的两组帧
        for _ in range(2):
            prefix = [Line.of(f"c{i}") for i in range(20)]
            tail = [Line.of("tail")]
            marked = Frame(
                prefix + tail,
                stable_prefix=prefix,
                stable_prefix_offset=0,
                stable_prefix_len=len(prefix),
            )
            unmarked = Frame([Line.of(f"c{i}") for i in range(20)] + [Line.of("tail")])
            r1.render(marked)
            r2.render(unmarked)
        out1.seek(0)
        out2.seek(0)
        assert out1.getvalue() == out2.getvalue(), (
            "stable_prefix 标记帧与未标记帧渲染输出应一致"
        )


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
        # 差异区间=[(1,2)]：仅重写第 2 行（Y），不重写未变的第 3 行（c）；
        # 重写后光标移回文档底部（cursor_down 1）。修复前从首差异行重写到
        # 末尾（冗余重写 c）。
        assert out.getvalue() == "\x1b[2A" + "\rY\x1b[K\n" + "\x1b[1B"

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
        """suspend 后 prev 为空帧（非 resize 均增量：不再置 None）。"""
        r, out = self._new()
        r.render(_frame("a", "b"))
        assert r.prev_frame is not None
        r.suspend()
        # 非 resize 均增量：suspend 后 prev 为空帧（Frame([], height=0)），
        # 下一帧走增量 diff（与空帧比较 = 所有行变化 → 逐行写入）。
        assert r.prev_frame is not None
        assert r.prev_frame.height == 0
        assert r.cursor_row == 1

    def test_reset_soft_then_incremental(self):
        """reset()（非 resize）后增量渲染（不清屏，走 diff 路径）。"""
        r, out = self._new()
        r.render(_frame("a", "b"))
        r.reset()  # full=False（默认），非 resize 软重置
        out.seek(0)
        out.truncate()
        r.render(_frame("x"))
        # 增量路径：\rx\x1b[K\n（含行尾清除，与 _write_full 的裸 \rx\n 不同）
        assert out.getvalue() == "\rx\x1b[K\n"

    def test_reset_full_then_full_rewrite(self):
        """reset(full=True)（resize）后全量写入（无行尾清除，最低开销）。"""
        r, out = self._new()
        r.render(_frame("a", "b"))
        r.reset(full=True)  # resize 路径：_prev=None
        out.seek(0)
        out.truncate()
        r.render(_frame("x"))
        # _write_full：裸 \rx\n，无行尾清除
        assert out.getvalue() == "\rx\n"


class TestIncrementalRuns:
    """增量渲染细化 — 差异区间重写（头部动画不再引发整帧重写）。"""

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_header_animation_rewrites_only_line_zero(self):
        """仅首行变化（头部呼吸色）→ 只重写首行，committed/输入行不重写。

        修复前「首差异行→末尾全重写」在首行变化时整帧重写。
        """
        r, out = self._new()
        r.render(_frame("h1", "c1", "c2", "c3", "status", "in1"))
        out.seek(0)
        out.truncate()
        r.render(_frame("h2", "c1", "c2", "c3", "status", "in1"))
        val = out.getvalue()
        # 仅重写首行 h2，其余 5 行原样保留（未出现重写序列）；光标移回底部
        assert val == "\x1b[6A\rh2\x1b[K\n\x1b[5B", val
        assert val.count("\x1b[K") == 1
        assert "c1" not in val and "c3" not in val
        assert r.cursor_row == 7

    def test_sparse_diff_rewrites_only_changed_runs(self):
        """稀疏差异（头部 + 输入变化，中间静态行不动）→ 仅重写两个区间。"""
        r, out = self._new()
        r.render(_frame("h1", "c1", "c2", "c3", "status", "in1"))
        out.seek(0)
        out.truncate()
        r.render(_frame("h2", "c1", "c2", "c3", "status", "in2"))
        val = out.getvalue()
        # 首行 h2 与末行 in2 各重写一次；中间 c1/c2/c3/status 未被重写
        assert val.count("\x1b[K") == 2, val
        assert "c1" not in val and "c2" not in val and "c3" not in val
        assert "status" not in val
        assert "h2" in val and "in2" in val
        assert r.cursor_row == 7

    def test_typing_only_rewrites_input_area(self):
        """输入变化（输入区在文档尾部）→ 仅重写输入区行。"""
        r, out = self._new()
        r.render(_frame("h", "c1", "c2", "c3", "status", "input: ab"))
        # place_cursor 把光标放到输入文本行（row 6，模拟真实输入态）
        r.place_cursor(6, 9)
        out.seek(0)
        out.truncate()
        r.render(_frame("h", "c1", "c2", "c3", "status", "input: abc"))
        val = out.getvalue()
        # 仅末行重写（光标已在输入行 → 无上移/下移）
        assert val == "\rinput: abc\x1b[K\n", val
        assert val.count("\x1b[K") == 1
        assert "c3" not in val
        assert r.cursor_row == 7

    def test_diff_runs_unit(self):
        """_diff_runs 差异区间收集（身份短路 + 值相等 + 区间合并）。"""
        r, _ = self._new()
        prev = _frame("a", "b", "c", "d")
        new = _frame("x", "b", "c", "y")
        assert r._diff_runs(prev, new, 4) == [(0, 1), (3, 4)]
        # 连续差异合并
        new2 = _frame("x", "y", "z", "d")
        assert r._diff_runs(prev, new2, 4) == [(0, 3)]
        # 完全一致 → 空
        assert r._diff_runs(prev, _frame("a", "b", "c", "d"), 4) == []
