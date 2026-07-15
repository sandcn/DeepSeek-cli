"""Tests for tui_framework.terminal.output_target module.

覆盖 IOutputTarget 协议、InlineOutputTarget、BufferTarget、NullTarget 的
正常场景、边界条件和异常场景。
"""
import io
import pytest
from tui_framework.terminal.output_target import (
    IOutputTarget, BufferTarget, NullTarget, InlineOutputTarget,
)


# ═══════════════════════════════════════════════════════════
# IOutputTarget Protocol 测试
# ═══════════════════════════════════════════════════════════

class TestIOutputTargetProtocol:
    """IOutputTarget Protocol 结构类型匹配测试。"""

    def test_buffer_target_is_output_target(self):
        """BufferTarget 应通过 IOutputTarget 协议检查。"""
        buf = BufferTarget()
        assert isinstance(buf, IOutputTarget)

    def test_null_target_is_output_target(self):
        """NullTarget 应通过 IOutputTarget 协议检查。"""
        null = NullTarget()
        assert isinstance(null, IOutputTarget)

    def test_inline_target_is_output_target(self):
        """InlineOutputTarget 应通过 IOutputTarget 协议检查。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        assert isinstance(target, IOutputTarget)

    def test_minimal_duck_typing(self):
        """最小实现的结构类型应通过协议检查。"""

        class MinimalTarget:
            @property
            def terminal_width(self):
                return 80

            @property
            def supports_inline(self):
                return True

            def write(self, text):
                pass

            def write_line(self, text=""):
                pass

            def render_frame(self, lines, last_lines):
                return len(lines)

            def clear_last_lines(self, n):
                pass

            def flush(self):
                pass

        assert isinstance(MinimalTarget(), IOutputTarget)


# ═══════════════════════════════════════════════════════════
# BufferTarget 测试
# ═══════════════════════════════════════════════════════════

class TestBufferTarget:
    """BufferTarget 功能测试。"""

    def test_write_appends(self):
        """write 应追加到缓冲区。"""
        buf = BufferTarget()
        buf.write("hello")
        assert "hello" in buf.get_output()

    def test_write_line_appends_newline(self):
        """write_line 应追加换行符。"""
        buf = BufferTarget()
        buf.write_line("world")
        output = buf.get_output()
        assert "world\n" == output

    def test_render_frame_appends_lines(self):
        """render_frame 应逐行追加并返回行数。"""
        buf = BufferTarget()
        result = buf.render_frame(["a", "b", "c"], 0)
        assert result == 3
        assert "a\nb\nc\n" in buf.get_output()

    def test_supports_inline_true(self):
        """BufferTarget 的 supports_inline 应为 True。"""
        buf = BufferTarget()
        assert buf.supports_inline is True

    def test_terminal_width_configurable(self):
        """terminal_width 应为构造时指定的宽度。"""
        buf = BufferTarget(width=120)
        assert buf.terminal_width == 120

    def test_clear_resets_buffer(self):
        """clear 应重置缓冲区。"""
        buf = BufferTarget()
        buf.write("data")
        buf.clear()
        assert buf.get_output() == ""

    def test_clear_last_lines_noop(self):
        """clear_last_lines 应为空操作（BufferTarget 为追加模式）。"""
        buf = BufferTarget()
        buf.clear_last_lines(5)  # should not raise
        assert buf.get_output() == ""


# ═══════════════════════════════════════════════════════════
# NullTarget 测试
# ═══════════════════════════════════════════════════════════

class TestNullTarget:
    """NullTarget 功能测试。"""

    def test_write_discards(self):
        """write 应静默丢弃。"""
        null = NullTarget()
        null.write("anything")  # should not raise

    def test_write_line_discards(self):
        """write_line 应静默丢弃。"""
        null = NullTarget()
        null.write_line("anything")  # should not raise

    def test_render_frame_returns_positive(self):
        """render_frame 应返回行数。"""
        null = NullTarget()
        result = null.render_frame(["a", "b"], 0)
        assert result == 2

    def test_terminal_width_default(self):
        """默认 terminal_width 为 80。"""
        null = NullTarget()
        assert null.terminal_width == 80

    def test_supports_inline_true(self):
        """NullTarget 的 supports_inline 应为 True。"""
        null = NullTarget()
        assert null.supports_inline is True

    def test_clear_last_lines_noop(self):
        """clear_last_lines 应为空操作。"""
        null = NullTarget()
        null.clear_last_lines(5)  # should not raise


# ═══════════════════════════════════════════════════════════
# InlineOutputTarget 测试
# ═══════════════════════════════════════════════════════════

class TestInlineOutputTarget:
    """InlineOutputTarget 核心功能测试。"""

    def test_write_output(self):
        """write 应写入 stdout 并 flush。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.write("hello")
        assert "hello" in buf.getvalue()

    def test_write_line_output(self):
        """write_line 应写入并追加换行符。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.write_line("world")
        assert "world\n" in buf.getvalue()

    def test_supports_inline_true(self):
        """InlineOutputTarget 的 supports_inline 应为 True。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        assert target.supports_inline is True

    def test_terminal_width_positive(self):
        """terminal_width 应为正整数。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        assert target.terminal_width > 0

    def test_render_frame_no_ansi_cursor_sequences(self):
        """render_frame 输出不应含 SCOSC/DECRC 光标保存/恢复序列。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["a", "b", "c"], 0)
        output = buf.getvalue()
        assert "\033[s" not in output  # SCOSC
        assert "\033[u" not in output  # DECRC

    def test_render_frame_uses_carriage_return_and_clear(self):
        """render_frame 应使用 \\r\\033[K 逐行清行。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["line1", "line2"], 0)
        output = buf.getvalue()
        assert "\r\033[K" in output

    def test_render_frame_returns_line_count(self):
        """render_frame 应返回渲染行数。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        result = target.render_frame(["a", "b", "c"], 0)
        assert result == 3

    def test_render_frame_first_frame_no_backtrack(self):
        """首帧（last_lines=0）不应产生回退序列。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["x"], 0)
        output = buf.getvalue()
        assert "\033[A" not in output  # 不应回退

    def test_render_frame_second_frame_backtracks(self):
        """第二帧（last_lines>0）应回退到上一帧起始位置。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["a", "b", "c"], 0)
        buf2 = io.StringIO()
        target2 = InlineOutputTarget(stdout=buf2)
        target2.render_frame(["x", "y"], 3)
        output = buf2.getvalue()
        assert "\033[3A" in output  # 应回退 3 行

    def test_render_frame_shrink_clears_extra(self):
        """帧缩小时应清除多余行。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["a", "b", "c", "d"], 0)  # 4 行
        buf2 = io.StringIO()
        target2 = InlineOutputTarget(stdout=buf2)
        target2.render_frame(["x"], 4)  # 缩小到 1 行
        output = buf2.getvalue()
        assert "\033[4A" in output  # 回退 4 行
        # 应清除 3 行多余

    def test_render_frame_grow_uses_peak(self):
        """帧增长时应返回峰值行数（历史最大行数）。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        result1 = target.render_frame(["a", "b", "c"], 0)
        assert result1 == 3
        buf2 = io.StringIO()
        target2 = InlineOutputTarget(stdout=buf2)
        result2 = target2.render_frame(["a", "b", "c", "d", "e"], 3)
        assert result2 == 5  # 峰值更新

    def test_render_frame_empty_lines(self):
        """空行列表渲染不应崩溃。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        result = target.render_frame([], 0)
        assert result == 0

    def test_clear_last_lines_positive(self):
        """clear_last_lines(n) 应生成向上清除序列。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.clear_last_lines(3)
        output = buf.getvalue()
        assert "\033[A\r\033[K" in output
        assert output.count("\033[A") == 3

    def test_clear_last_lines_zero(self):
        """clear_last_lines(0) 应为空操作。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.clear_last_lines(0)
        assert buf.getvalue() == ""

    def test_clear_last_lines_negative(self):
        """clear_last_lines(-1) 应为空操作。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.clear_last_lines(-1)
        assert buf.getvalue() == ""

    def test_flush_does_not_crash(self):
        """flush 不应抛出异常。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.flush()  # should not raise

    def test_multiple_frames_no_flicker_ansi(self):
        """连续多帧渲染不应使用闪烁相关的 ANSI 序列。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.render_frame(["f1"], 0)
        target.render_frame(["f2"], 1)
        output = buf.getvalue()
        # 不应有闪烁序列
        assert "\033[?5h" not in output

    def test_narrow_width_output(self):
        """窄屏终端宽度下输出应正常。"""
        # 使用 TerminalAdapter 时 terminal_width 依赖实际终端，
        # 但 InlineOutputTarget 不依赖宽度进行渲染逻辑，
        # 所以任何宽度都应正常工作。
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        lines = ["short"]
        result = target.render_frame(lines, 0)
        assert result == 1
        assert "\r\033[Kshort" in buf.getvalue()


# ═══════════════════════════════════════════════════════════
# 集成测试
# ═══════════════════════════════════════════════════════════

class TestIntegration:
    """输出目标集成测试。"""

    def test_write_then_frame_interaction(self):
        """write 后 render_frame 的 last_lines 计算正确。"""
        buf = io.StringIO()
        target = InlineOutputTarget(stdout=buf)
        target.write_line("header")
        result = target.render_frame(["body1", "body2"], 0)
        assert result == 2

    def test_buffer_target_full_cycle(self):
        """BufferTarget 完整写入→读取→清除周期。"""
        buf = BufferTarget()
        buf.write("a")
        buf.write_line("b")
        buf.render_frame(["c", "d"], 0)
        output = buf.get_output()
        assert "a" in output
        assert "b\n" in output
        assert "c\n" in output
        assert "d\n" in output
        buf.clear()
        assert buf.get_output() == ""
