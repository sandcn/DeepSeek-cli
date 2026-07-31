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
        # 结果应为原始输入文本（首次 Tab 不自动应用补全）
        assert result is not None
        assert result == "say hello"

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


class TestThemeParamCompletion:
    """/theme 参数补全测试 — 验证返回真实主题名（幽灵导入修复回归）。"""

    def test_theme_completion_returns_real_themes(self):
        """/theme 补全应返回真实主题名（来自 core 层 CommandUiAdapter）。"""
        from src.tui._completion_engine import CompletionEngine

        engine = CompletionEngine()
        items = engine.complete("/theme")
        names = [item.text for item in items]
        # CommandUiAdapter.get_theme_names_with_desc 返回真实主题名（default）
        assert "default" in names

    def test_theme_completion_item_type(self):
        """/theme 补全项类型应为 param。"""
        from src.tui._completion_engine import CompletionEngine

        engine = CompletionEngine()
        items = engine.complete("/theme")
        assert items
        assert all(item.item_type == "param" for item in items)


class TestCompletionShowDedup:
    """方向F·步骤13 补全弹窗显示去重回归测试（_show_completions_for helper）。"""

    def _make_handler(self):
        from src.tui._completion import _CmplHandler
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_redraw = MagicMock()
        return _CmplHandler(mock_bb, mock_engine, mock_redraw), mock_bb, mock_engine, mock_redraw

    def test_show_completions_for_helper_regression(self):
        """helper 直接调用时 show_completions 参数正确（display/texts/start_pos/orig_prefix/types/match_prefix）。"""
        from src.tui._completion import _show_completions_for
        from src.tui._completion_engine import CompletionItem
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_engine.complete.return_value = [
            CompletionItem("hello world", display="hello world", start_pos=-11, item_type=""),
        ]

        result = _show_completions_for(mock_bb, mock_engine, "say hello")

        assert result is True
        mock_bb.show_completions.assert_called_once_with(
            ["hello world"], 0,
            texts=["hello world"],
            start_pos=-11,
            orig_prefix="hello",
            types=[""],
            match_prefix="hello",
        )

    def test_show_completions_for_no_items_regression(self):
        """helper 无候选项时返回 False 且不调用 show_completions。"""
        from src.tui._completion import _show_completions_for
        mock_bb = MagicMock()
        mock_engine = MagicMock()
        mock_engine.complete.return_value = []

        result = _show_completions_for(mock_bb, mock_engine, "xyz")

        assert result is False
        mock_bb.show_completions.assert_not_called()

    def test_first_tab_uses_helper_regression(self):
        """_first_tab 经 helper 显示弹窗且参数与旧版一致。"""
        from src.tui._completion_engine import CompletionItem
        handler, mock_bb, mock_engine, mock_redraw = self._make_handler()
        mock_engine.complete.return_value = [
            CompletionItem("hello world", display="hello world", start_pos=-11, item_type=""),
        ]
        mock_bb.is_completion_visible = False

        result = handler._first_tab("say hello")

        assert result == "say hello"
        mock_bb.show_completions.assert_called_once_with(
            ["hello world"], 0,
            texts=["hello world"],
            start_pos=-11,
            orig_prefix="hello",
            types=[""],
            match_prefix="hello",
        )
        mock_redraw.assert_called()

    def test_first_tab_no_items_hides_via_helper_regression(self):
        """_first_tab 无候选项时经 helper 返回 False 后 hide + request_redraw。"""
        handler, mock_bb, mock_engine, mock_redraw = self._make_handler()
        mock_engine.complete.return_value = []
        mock_bb.is_completion_visible = False

        result = handler._first_tab("xyz")

        assert result is None
        mock_bb.hide_completions.assert_called_once()
        mock_bb.show_completions.assert_not_called()
        mock_redraw.assert_called()

    def test_on_auto_uses_helper_regression(self):
        """on_auto 经 helper 显示弹窗且 _last_auto_text 更新。"""
        from src.tui._completion_engine import CompletionItem
        handler, mock_bb, mock_engine, mock_redraw = self._make_handler()
        mock_engine.complete.return_value = [
            CompletionItem("/help", display="/help", start_pos=-5, item_type="command"),
        ]
        mock_bb.is_completion_visible = False

        handler.on_auto("/hel")

        mock_bb.show_completions.assert_called_once()
        mock_redraw.assert_called()
        assert handler._last_auto_text == "/hel"

    def test_on_auto_no_items_hides_via_helper_regression(self):
        """on_auto 无候选项时经 helper 返回 False 后 hide + 防抖更新。"""
        handler, mock_bb, mock_engine, mock_redraw = self._make_handler()
        mock_engine.complete.return_value = []

        handler.on_auto("something")

        mock_bb.hide_completions.assert_called_once()
        mock_bb.show_completions.assert_not_called()
        assert handler._last_auto_text == "something"
