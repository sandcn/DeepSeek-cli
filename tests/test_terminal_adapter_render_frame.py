"""render_frame 峰值保持回归测试

验证 TerminalAdapter.render_frame() 在帧缩小时返回 max(last_lines, total)，
防止下一帧因行数不足导致终端残留行。
"""

import io
import pytest
from src.tui.terminal.adapter import TerminalAdapter


@pytest.fixture
def adapter():
    """创建写入 StringIO 的 TerminalAdapter，隔离真实终端 I/O。"""
    buf = io.StringIO()
    return TerminalAdapter(stdout=buf)


class TestRenderFramePeakTracking:
    """render_frame 峰值追踪回归测试"""

    def test_steady_state_returns_total(self, adapter):
        """稳态（帧大小不变）：返回当前 total"""
        # 首帧：last_lines=0，不触发 \033[u
        result1 = adapter.render_frame(["line1", "line2", "line3"], last_lines=0)
        assert result1 == 3

        # 次帧：帧大小不变，last_lines=total → 返回 total
        result2 = adapter.render_frame(["a", "b", "c"], last_lines=3)
        assert result2 == 3

    def test_shrink_preserves_peak(self, adapter):
        """帧缩小时返回 max(last_lines, total)，保留峰值"""
        # 大帧渲染后 last_lines=42
        large = ["line"] * 42

        # 模拟：last_lines=42 (峰值)，当前帧只有 10 行
        result = adapter.render_frame(["a"] * 10, last_lines=42)
        # 应保留峰值 42，而非返回 10
        assert result == 42, (
            f"帧缩小时应保留峰值：期望 42，实际 {result}"
        )

    def test_grow_updates_peak(self, adapter):
        """帧增长时更新峰值"""
        result = adapter.render_frame(["a"] * 50, last_lines=10)
        assert result == 50

    def test_empty_frame_preserves_peak(self, adapter):
        """空帧（total=0）时保留峰值"""
        result = adapter.render_frame([], last_lines=5)
        assert result == 5, (
            f"空帧不应归零 last_lines：期望 5，实际 {result}"
        )

    def test_shrink_then_stable(self, adapter):
        """帧缩小后稳态不再继续缩小"""
        # 第1帧：42行
        r1 = adapter.render_frame(["x"] * 42, last_lines=0)
        assert r1 == 42

        # 第2帧：缩到10行
        r2 = adapter.render_frame(["y"] * 10, last_lines=42)
        assert r2 == 42  # 保留峰值

        # 第3帧：稳态10行
        r3 = adapter.render_frame(["z"] * 10, last_lines=42)
        assert r3 == 42  # 继续保留峰值

    def test_first_frame_zero_last_lines(self, adapter):
        """首帧 last_lines=0 正常返回 total"""
        result = adapter.render_frame(["a"], last_lines=0)
        assert result == 1

    def test_peak_independent_of_terminal_state(self, adapter):
        """峰值计算不依赖终端状态（纯数学逻辑）"""
        # last_lines=100, total=3 → 返回 100
        assert adapter.render_frame(["a", "b", "c"], last_lines=100) == 100

        # last_lines=3, total=100 → 返回 100
        assert adapter.render_frame(["x"] * 100, last_lines=3) == 100


class TestRenderFrameOutput:
    """render_frame ANSI 序列输出验证"""

    def test_includes_save_cursor(self, adapter):
        """渲染后输出包含 SCOSC 保存序列"""
        adapter.render_frame(["hello"], last_lines=0)
        output = adapter._stdout.getvalue()
        assert "\033[s" in output, "应包含 SCOSC 保存序列"

    def test_includes_restore_for_non_first_frame(self, adapter):
        """非首帧（last_lines>0）包含 \033[u 恢复序列"""
        adapter.render_frame(["a"], last_lines=1)
        output = adapter._stdout.getvalue()
        assert "\033[u" in output, "非首帧应包含 SCOSC 恢复序列"

    def test_first_frame_no_restore(self, adapter):
        """首帧（last_lines=0）不包含 \033[u"""
        adapter.render_frame(["a"], last_lines=0)
        output = adapter._stdout.getvalue()
        assert "\033[u" not in output, "首帧不应包含 SCOSC 恢复序列"

    def test_extra_lines_cleared(self, adapter):
        """帧缩小时清除多余行"""
        adapter.render_frame(["a", "b"], last_lines=5)
        output = adapter._stdout.getvalue()
        # extra = 5 - 2 = 3, 应包含 3 个 \n\033[K
        assert output.count("\n\033[K") == 3, (
            f"应清除 3 个多余行，实际 {output.count(chr(10) + chr(27) + '[K')} 个"
        )

    def test_shrink_uses_peak_for_positioning(self, adapter):
        """帧缩小时 \\033[{last_lines}A 使用峰值而非当前 total"""
        # 模拟峰值 42，当前帧 10 行
        adapter.render_frame(["a"] * 10, last_lines=42)
        output = adapter._stdout.getvalue()
        # 应使用峰值 42 回退，而非当前 total 10
        assert "\033[42A" in output, (
            f"帧缩小时应使用峰值行数定位：期望 \\033[42A，实际输出: {output!r}"
        )

    def test_empty_first_frame(self, adapter):
        """空首帧（last_lines=0, total=0）返回 0"""
        result = adapter.render_frame([], last_lines=0)
        assert result == 0
