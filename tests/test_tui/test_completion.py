"""测试 _completion.py — _CmplHandler 补全交互逻辑。

测试 Tab 补全、自动补全、补全应用等核心逻辑，
使用 mock CompletionEngine 和 BottomBar。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


class TestCmplHandlerTab:
    """_CmplHandler.on_tab 测试。"""

    @pytest.fixture
    def handler(self):
        """创建 mock _CmplHandler。"""
        from src.tui._completion import _CmplHandler
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_redraw = MagicMock()
        return _CmplHandler(mock_bb, mock_engine, mock_redraw)

    def test_first_tab_with_results(self, handler):
        """首次 Tab 有结果时应显示弹窗并返回首个匹配。"""
        from src.tui._completion_engine import CompletionItem
        handler._engine.complete.return_value = [
            CompletionItem("hello world", display="hello world", start_pos=-11, item_type=""),
        ]
        handler._bb.is_completion_visible = False

        result = handler.on_tab("say hello")

        handler._bb.show_completions.assert_called_once()
        handler._request_redraw.assert_called()
        # 结果应为补全后的文本
        assert result is not None
        assert "hello world" in result

    def test_first_tab_no_results(self, handler):
        """首次 Tab 无结果时应隐藏弹窗，返回 None。"""
        handler._engine.complete.return_value = []
        handler._bb.is_completion_visible = False

        result = handler.on_tab("xyz")

        handler._bb.hide_completions.assert_called_once()
        assert result is None

    def test_cycle_tab_visible(self, handler):
        """弹窗可见时 Tab 应确认当前选中项。"""
        handler._bb.is_completion_visible = True
        handler._bb.get_selected_completion.return_value = ("replaced", -5, "orig")

        result = handler.on_tab("hello orig")

        handler._bb.get_selected_completion.assert_called_once()
        assert result == "hello replaced"


class TestCmplHandlerAuto:
    """_CmplHandler.on_auto 测试。"""

    @pytest.fixture
    def handler(self):
        """创建 mock _CmplHandler。"""
        from src.tui._completion import _CmplHandler
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_redraw = MagicMock()
        return _CmplHandler(mock_bb, mock_engine, mock_redraw)

    def test_empty_text_hides(self, handler):
        """空文本应隐藏弹窗。"""
        handler.on_auto("")
        handler._bb.hide_completions.assert_called_once()

    def test_short_text_hides(self, handler):
        """长度 < 2 且非 / 开头应隐藏弹窗。"""
        handler.on_auto("a")
        handler._bb.hide_completions.assert_called_once()

    def test_command_prefix_shows(self, handler):
        """/ 开头应触发补全。"""
        from src.tui._completion_engine import CompletionItem
        handler._engine.complete.return_value = [
            CompletionItem("/help", display="/help", start_pos=-5, item_type="command"),
        ]
        handler._bb.is_completion_visible = False

        handler.on_auto("/hel")

        handler._bb.show_completions.assert_called_once()
        handler._request_redraw.assert_called()

    def test_debounce_same_text(self, handler):
        """相同文本应跳过防抖。"""
        handler.on_auto("hello")
        first_call_count = handler._bb.show_completions.call_count

        handler.on_auto("hello")  # 相同文本
        # 不应再次调用 show_completions（防抖）
        assert handler._bb.show_completions.call_count == first_call_count

    def test_no_results_hides(self, handler):
        """无匹配结果时应隐藏弹窗。"""
        handler._engine.complete.return_value = []

        handler.on_auto("something")

        handler._bb.hide_completions.assert_called()


class TestCmplHandlerNavigation:
    """_CmplHandler.on_navigate 测试。"""

    @pytest.fixture
    def handler(self):
        """创建 mock _CmplHandler。"""
        from src.tui._completion import _CmplHandler
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_redraw = MagicMock()
        return _CmplHandler(mock_bb, mock_engine, mock_redraw)

    def test_navigate_when_visible(self, handler):
        """弹窗可见时导航应更新选中状态。"""
        handler._bb.is_completion_visible = True

        result = handler.on_navigate(1, "test")

        handler._bb.cycle_completion.assert_called_once_with(1)
        handler._request_redraw.assert_called()
        assert result == "test"  # 仅导航，不应用补全

    def test_navigate_when_hidden(self, handler):
        """弹窗不可见时导航应返回 None。"""
        handler._bb.is_completion_visible = False

        result = handler.on_navigate(1, "test")

        handler._bb.cycle_completion.assert_not_called()
        assert result is None


class TestApplyCompletion:
    """_apply_completion 纯函数测试。"""

    def test_rfind_match(self):
        """orig_prefix 通过 rfind 找到时应替换。"""
        from src.tui._completion import _apply_completion

        result = _apply_completion("say hello w", "hello world", -11, "hello w")
        assert result == "say hello world"

    def test_start_pos_negative(self):
        """start_pos < 0 时从尾部裁剪。"""
        from src.tui._completion import _apply_completion

        result = _apply_completion("hello xyz", "hello world", -3, "")
        assert result == "hello hello world"

    def test_start_pos_exceeds_len(self):
        """start_pos 负值绝对值超过文本长度时全替换。"""
        from src.tui._completion import _apply_completion

        result = _apply_completion("ab", "hello", -10, "")
        assert result == "hello"

    def test_start_pos_positive(self):
        """start_pos > 0 时从指定位置开始替换。"""
        from src.tui._completion import _apply_completion

        result = _apply_completion("hello xyz", "world", 6, "")
        assert result == "hello world"

    def test_no_prefix_no_start(self):
        """无 orig_prefix 且 start_pos=0 时全替换。"""
        from src.tui._completion import _apply_completion

        result = _apply_completion("old", "new text", 0, "")
        assert result == "new text"
