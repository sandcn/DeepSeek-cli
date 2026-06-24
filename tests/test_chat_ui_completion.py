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
        from src.chat_ui.bottom_bar._bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        # 模拟 _draw_input_lines_locked 已执行后的缓存状态
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=3 在 \\n 之后、'c' 之前 → 期望 (1, 0)
        assert bb._cursor_visual_pos_from_cache(text, 3, 80) == (1, 0)

    def test_newline_boundary_last_line_no_next(self):
        """文件末尾（无下一段）→ 走正常分支返回段内 col。"""
        from src.chat_ui.bottom_bar._bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=4 在最后一段 'c' 上 → 期望 (1, 0)
        assert bb._cursor_visual_pos_from_cache(text, 4, 80) == (1, 0)

    def test_mid_segment_no_boundary(self):
        """段内位置（非边界）→ 走正常分支。"""
        from src.chat_ui.bottom_bar._bar import _BottomBar
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
        from src.chat_ui.bottom_bar._bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        assert bb._cursor_visual_pos_from_cache(text, 3, 80) == (1, 0)

    def test_newline_boundary_last_line_no_next(self):
        """文件末尾（无下一段）→ 走正常分支返回段尾 col。"""
        from src.chat_ui.bottom_bar._bar import _BottomBar
        bb = _BottomBar()
        text = "ab\ncd"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["ab", "cd"]
        # cursor_pos=5（末尾在'd'之后）无下一段 → 走正常分支返回段尾 col=2
        assert bb._cursor_visual_pos_from_cache(text, 5, 80) == (1, 2)

    def test_mid_segment_no_boundary(self):
        """段内位置（非边界）→ 走正常分支。"""
        from src.chat_ui.bottom_bar._bar import _BottomBar
        bb = _BottomBar()
        text = "abcdef"
        bb._cached_wrapped_for = text
        bb._cached_wrapped_lines = ["abcdef"]
        assert bb._cursor_visual_pos_from_cache(text, 2, 80) == (0, 2)


class TestCmplHandlerOnAuto:
    """_CmplHandler.on_auto 自动弹出补全测试"""

    @staticmethod
    def _make_handler(bb, engine):
        """创建 _CmplHandler 并注入 mock request_redraw。"""
        from unittest.mock import MagicMock
        from src.chat_ui._completion import _CmplHandler
        request_redraw = MagicMock()
        return _CmplHandler(bb, engine, request_redraw=request_redraw), request_redraw

    def test_empty_text_hides_completions(self):
        """空文本 → 隐藏弹窗。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("")

        bb.hide_completions.assert_called_once()
        request_redraw.assert_called_once()
        engine.complete.assert_not_called()

    def test_short_non_command_skips_completion(self):
        """普通文本 < 2 字符 → 隐藏弹窗。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("h")

        bb.hide_completions.assert_called_once()
        request_redraw.assert_called_once()
        engine.complete.assert_not_called()

    def test_single_forward_slash_triggers_completion(self):
        """/ 单字符 → 应触发补全（命令前缀）。"""
        from unittest.mock import MagicMock
        from src.ui._completion import CompletionItem

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        engine.complete.return_value = [
            CompletionItem("/help", "/help 显示帮助", -1),
            CompletionItem("/model", "/model 切换模型", -2),
        ]
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("/")

        engine.complete.assert_called_once_with("/")
        bb.show_completions.assert_called_once()
        request_redraw.assert_called_once()
        args, kwargs = bb.show_completions.call_args
        assert kwargs["start_pos"] == -1
        assert kwargs["orig_prefix"] == "/"
        assert len(kwargs["texts"]) == 2

    def test_no_completions_hides(self):
        """无候选项 → 隐藏弹窗。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        engine = MagicMock()
        engine.complete.return_value = []
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("/xyz")

        engine.complete.assert_called_once_with("/xyz")
        bb.hide_completions.assert_called_once()
        request_redraw.assert_called_once()

    def test_two_char_non_command_triggers_completion(self):
        """普通文本 ≥ 2 字符 → 触发补全。"""
        from unittest.mock import MagicMock
        from src.ui._completion import CompletionItem

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        engine.complete.return_value = [
            CompletionItem("hello", "hello", -5),
        ]
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("he")

        engine.complete.assert_called_once_with("he")
        bb.show_completions.assert_called_once()
        request_redraw.assert_called_once()
        args, kwargs = bb.show_completions.call_args
        assert kwargs["orig_prefix"] == "he"
        assert len(kwargs["texts"]) == 1

    def test_command_with_param_uses_last_word(self):
        """命令+参数 → orig_prefix 取最后一个词。"""
        from unittest.mock import MagicMock
        from src.ui._completion import CompletionItem

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        engine.complete.return_value = [
            CompletionItem("deepseek-v4-pro", "deepseek-v4-pro", -5),
        ]
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("/model deep")

        engine.complete.assert_called_once_with("/model deep")
        bb.show_completions.assert_called_once()
        request_redraw.assert_called_once()
        args, kwargs = bb.show_completions.call_args
        assert kwargs["orig_prefix"] == "deep"

    def test_updates_existing_completion(self):
        """已有弹窗可见 → 重新计算并更新。"""
        from unittest.mock import MagicMock
        from src.ui._completion import CompletionItem

        bb = MagicMock()
        bb.is_completion_visible = True
        engine = MagicMock()
        engine.complete.return_value = [
            CompletionItem("/model-pro", "/model-pro", -2),
        ]
        handler, request_redraw = self._make_handler(bb, engine)
        handler.on_auto("/m")

        engine.complete.assert_called_once_with("/m")
        bb.show_completions.assert_called_once()
        request_redraw.assert_called_once()


class TestCmplHandlerTab:
    """_CmplHandler.on_tab / _cycle_tab / _first_tab 测试

    验证：
      - 弹窗可见时 Tab = 确认当前选中项（不循环到下一项）
      - 弹窗不可见时 Tab = 首次显示并应用第一项
    """

    @staticmethod
    def _make_handler(bb, engine):
        from src.chat_ui._completion import _CmplHandler
        from unittest.mock import MagicMock
        request_redraw = MagicMock()
        return _CmplHandler(bb, engine, request_redraw=request_redraw), request_redraw

    def test_tab_when_visible_confirms_current_item(self):
        """弹窗可见时按 Tab → 确认当前选中项（不循环到下一项）。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = True
        # 当前选中的是第二项（index=1）
        bb.get_selected_completion.return_value = ("/model-pro", -2, "/m")
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_tab("/m")

        # 应获取当前选中项，且不应调用 request_redraw（Tab 确认不产生新渲染）
        bb.get_selected_completion.assert_called_once()
        request_redraw.assert_not_called()  # Tab 确认不触发重绘
        assert result == "/model-pro"

    def test_tab_when_visible_no_selection_returns_none(self):
        """弹窗可见但无选中项 → 返回 None（回退为插入制表符）。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = True
        bb.get_selected_completion.return_value = ("", 0, "")
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_tab("text")

        bb.get_selected_completion.assert_called_once()
        assert result is None

    def test_tab_when_not_visible_shows_and_applies_first(self):
        """弹窗不可见时按 Tab → 计算候选项，显示弹窗，返回首个匹配。"""
        from unittest.mock import MagicMock
        from src.ui._completion import CompletionItem

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        engine.complete.return_value = [
            CompletionItem("/help", "/help 显示帮助", -1),
            CompletionItem("/model", "/model 切换模型", -2),
        ]
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_tab("/")

        engine.complete.assert_called_once_with("/")
        bb.show_completions.assert_called_once()
        request_redraw.assert_called_once()  # 请求 render 线程重绘
        assert result == "/help"  # 返回第一项

    def test_tab_when_not_visible_no_results_returns_none(self):
        """弹窗不可见且无候选项 → 返回 None（回退为插入制表符）。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        engine.complete.return_value = []
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_tab("/xyz")

        engine.complete.assert_called_once_with("/xyz")
        bb.hide_completions.assert_called_once()
        request_redraw.assert_called_once()  # hide 后请求重绘
        assert result is None


class TestCmplHandlerNavigate:
    """_CmplHandler.on_navigate 测试

    验证：箭头键只移动高亮（导航），不应用补全到输入缓冲区。
    状态设置后请求 render 线程重绘。
    """

    @staticmethod
    def _make_handler(bb, engine):
        from src.chat_ui._completion import _CmplHandler
        from unittest.mock import MagicMock
        request_redraw = MagicMock()
        return _CmplHandler(bb, engine, request_redraw=request_redraw), request_redraw

    def test_navigate_when_visible_returns_original_text(self):
        """弹窗可见时按 ↑ → 更新选中状态 + 请求重绘，返回原始文本。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = True
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_navigate(-1, "/model")

        bb.cycle_completion.assert_called_once_with(-1)
        request_redraw.assert_called_once()
        bb.get_selected_completion.assert_not_called()  # 不应获取选中项
        assert result == "/model"  # 返回原始文本，不应用补全

    def test_navigate_down_when_visible_returns_original_text(self):
        """弹窗可见时按 ↓ → 更新选中状态 + 请求重绘，返回原始文本。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = True
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_navigate(1, "/model")

        bb.cycle_completion.assert_called_once_with(1)
        request_redraw.assert_called_once()
        assert result == "/model"

    def test_navigate_when_not_visible_returns_none(self):
        """弹窗不可见时按 ↑/↓ → 返回 None（回退历史浏览）。"""
        from unittest.mock import MagicMock

        bb = MagicMock()
        bb.is_completion_visible = False
        engine = MagicMock()
        handler, request_redraw = self._make_handler(bb, engine)

        result = handler.on_navigate(-1, "text")

        request_redraw.assert_not_called()
        assert result is None
