"""_apply_completion 函数单元测试

测试 _apply_completion 的三阶段定位逻辑：
  1. rfind 全文搜索 — 修复上下键导航文本累加 bug
  2. start_pos 裁剪回退
  3. 返回 repl_text 兜底
"""

import pytest
from src.chat_ui import _apply_completion


class TestApplyCompletionRfind:
    """rfind 全文搜索路径 — 核心修复"""

    def test_basic_match_rfind(self):
        """基础匹配：orig_prefix 在文本中，rfind 定位替换"""
        result = _apply_completion("/m", "/model", -2, "/m")
        assert result == "/model"

    def test_text_grown_orig_prefix_still_present(self):
        """核心 bug 场景：文本已被补全修改，orig_prefix 仍是子串。

        用户输入 /m → Tab 补全为 /model → 按 ↓ 切换到 /model-pro：
        text="/model", orig_prefix="/m"。rfind 找到 "/m" 并替换。
        """
        result = _apply_completion("/model", "/model-pro", -2, "/m")
        assert result == "/model-pro"

    def test_multi_occurrence_rfind_picks_last(self):
        """前缀多次出现：rfind 选最后一次匹配（光标附近）。"""
        result = _apply_completion("ecec", "echo", -2, "ec")
        assert result == "ececho"

    def test_orig_prefix_at_end(self):
        """orig_prefix 恰好在文本末尾（典型路径补全场景）。"""
        result = _apply_completion("hel", "hello", -3, "hel")
        assert result == "hello"

    def test_orig_prefix_at_beginning(self):
        """orig_prefix 在文本开头（正常首次补全）。"""
        result = _apply_completion("/m", "/model-pro", -2, "/m")
        assert result == "/model-pro"


class TestApplyCompletionFallback:
    """start_pos 裁剪回退路径 — orig_prefix 不在文本中时"""

    def test_start_pos_negative_trim(self):
        """start_pos < 0：从文本末尾裁剪 |start_pos| 字符后拼接。"""
        result = _apply_completion("xyz", "echo", -3, "/m")
        assert result == "echo"

    def test_start_pos_negative_partial_trim(self):
        """start_pos < 0 且 trim_len < len(text)：裁剪后拼接。"""
        result = _apply_completion("abcdef", "X", -2, "gh")
        assert result == "abcdX"

    def test_orig_prefix_empty_falls_to_fallback(self):
        """orig_prefix 为空 → 直接走 fallback。"""
        result = _apply_completion("hello", "world", -3, "")
        assert result == "heworld"

    def test_start_pos_zero_fallback(self):
        """start_pos=0 且 orig_prefix 不存在 → 返回 repl_text。"""
        result = _apply_completion("abc", "xyz", 0, "nonexistent")
        assert result == "xyz"


class TestApplyCompletionEdgeCases:
    """边界用例"""

    def test_replace_empty_text(self):
        """空文本 + start_pos=0 → 返回 repl_text。"""
        result = _apply_completion("", "new", 0, "")
        assert result == "new"

    def test_prefix_longer_than_text(self):
        """orig_prefix 比文本长 → in 检查失败 → 走 fallback。"""
        result = _apply_completion("ab", "repl", -2, "abcdef")
        assert result == "repl"

    def test_param_completion_navigate(self):
        """参数补全导航：/model deeps → 选 deepseek-v4-pro。"""
        result = _apply_completion(
            "/model deeps", "deepseek-v4-pro", -5, "deeps",
        )
        assert result == "/model deepseek-v4-pro"

    def test_command_with_space_navigate(self):
        """命令+参数已输入后，补全新命令名。"""
        result = _apply_completion(
            "/model deepseek-v4", "/theme", -12, "/mode",
        )
        assert result == "/theme"


class TestApplyCompletionStartPosPositive:
    """start_pos > 0 分支 — 保留供非 CompletionEngine 来源的调用"""

    def test_start_pos_positive_rfind_match(self):
        """orig_prefix 被 rfind 命中 → 走阶段 1（非 start_pos>0 分支）。"""
        result = _apply_completion("hello world", "X", 6, "w")
        assert result == "hello X"

    def test_start_pos_positive_no_rfind_match(self):
        """orig_prefix 不在文本中且 0 < start_pos < len(text) → 走阶段 3。"""
        result = _apply_completion("hello world", "X", 5, "zzz")
        assert result == "helloX"

    def test_start_pos_positive_beyond_len(self):
        """start_pos >= len(text)：兜底返回 repl_text。"""
        result = _apply_completion("ab", "xyz", 5, "c")
        assert result == "xyz"


class TestApplyCompletionCrossCoverage:
    """交叉路径覆盖 — rfind + start_pos 组合"""

    def test_start_pos_zero_with_orig_prefix_match(self):
        """start_pos=0 且 orig_prefix 在文本中：走 rfind 阶段 1。"""
        result = _apply_completion("hello abc", "X", 0, "ab")
        assert result == "hello X"

    def test_start_pos_zero_orig_prefix_empty(self):
        """start_pos=0 且 orig_prefix 为空：走阶段 3 兜底。"""
        result = _apply_completion("abc", "xyz", 0, "")
        assert result == "xyz"


class TestCursorVisualPosFromCache:
    """_cursor_visual_pos_from_cache 在 \\n 后边界的光标位置计算（P1 修复回归测试）

    验证当光标位于 \\n 后边界（段尾且存在下一段）时，
    正确返回下一段起始 (i+1, 0) 而非当前段末尾 (i, col)。
    """

    def test_newline_boundary_returns_next_segment_start(self):
        """\\n 后边界 → 返回下一段起始。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        # 模拟 _draw_input_lines_locked 已执行后的缓存状态
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=3 在 \\n 之后、'c' 之前 → 期望 (1, 0)
        assert bb._cursor_visual_pos_from_cache(text, 3, 80) == (1, 0)

    def test_newline_boundary_last_line_no_next(self):
        """文件末尾（无下一段）→ 走正常分支返回段内 col。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=4 在最后一段 'c' 上 → 期望 (1, 0)
        assert bb._cursor_visual_pos_from_cache(text, 4, 80) == (1, 0)

    def test_mid_segment_no_boundary(self):
        """段内位置（非边界）→ 走正常分支。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "abcdef"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["abcdef"]
        # cursor_pos=2 在 'c' 上 → 期望 (0, 2)
        assert bb._cursor_visual_pos_from_cache(text, 2, 80) == (0, 2)


class TestCursorVisualPosFromCache:
    """_cursor_visual_pos_from_cache 在 \n 后边界的光标位置计算（P1 修复回归测试）

    验证当光标位于 \n 后边界（段尾且存在下一段）时，
    正确返回下一段起始 (i+1, 0) 而非当前段末尾 (i, col)。
    """

    def test_newline_boundary_returns_next_segment_start(self):
        """\n 后边界 → 返回下一段起始。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        assert bb._cursor_visual_pos_from_cache(text, 3, 80) == (1, 0)

    def test_newline_boundary_last_line_no_next(self):
        """文件末尾（无下一段）→ 走正常分支返回段尾 col。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=5（末尾在'd'之后）无下一段 → 走正常分支返回段尾 col=2
        assert bb._cursor_visual_pos_from_cache(text, 5, 80) == (1, 2)

    def test_mid_segment_no_boundary(self):
        """段内位置（非边界）→ 走正常分支。"""
        from src.ui._bottom_bar import _BottomBar
        bb = _BottomBar()
        text = "abcdef"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["abcdef"]
        assert bb._cursor_visual_pos_from_cache(text, 2, 80) == (0, 2)
