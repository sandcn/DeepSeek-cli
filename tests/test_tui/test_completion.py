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
        # CommandUiAdapter.get_theme_names_with_desc 返回真实主题名（dark/light/high-contrast）
        assert "dark" in names
        assert "light" in names
        assert "high-contrast" in names

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
            descriptions=[""],
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
            descriptions=[""],
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


# ═══════════════════════════════════════════════════════════
# 方向D 步骤13 — 命令/参数候选语义排序增强
# ═══════════════════════════════════════════════════════════

class TestCommandCompletionSorting:
    """方向D 步骤13 — 命令补全候选语义排序（精确 > 前缀 > 子串，长度升序）。"""

    @staticmethod
    def _engine_with_commands(commands):
        from src.tui._completion_engine import CompletionEngine
        return CompletionEngine(commands_source=lambda: list(commands))

    def test_command_exact_match_first_regression(self):
        """输入 /m 时精确项 /model 优先于 /model-check（精确 > 前缀）。"""
        engine = self._engine_with_commands(["/model-check", "/model", "/theme"])
        items = engine._complete_command("/m")
        texts = [i.text for i in items]
        assert texts[0] == "/model"
        assert texts.index("/model") < texts.index("/model-check")

    def test_command_prefix_shorter_first_regression(self):
        """输入 /mo 时 /model 排在 /model-check 前（前缀长度升序）。"""
        engine = self._engine_with_commands(["/model-check", "/model", "/theme"])
        items = engine._complete_command("/mo")
        texts = [i.text for i in items]
        assert texts.index("/model") < texts.index("/model-check")

    def test_command_substring_after_prefix_regression(self):
        """子串包含排在前缀匹配之后（防御用例：验证 _ranked 分级）。"""
        from src.tui._completion_engine import _ranked
        # 子串匹配需 prefix 完整出现在命令非开头位置——构造含第二斜杠的命令名
        # （命令注册表一般不含，本用例防御验证 _ranked 分级逻辑）。
        commands = ["/model-check", "/model", "/x/model"]
        texts = _ranked(commands, "/model")
        assert texts[0] == "/model"                          # 精确最前
        assert texts.index("/model-check") < texts.index("/x/model")  # 前缀 < 子串

    def test_command_same_prefix_alphabetical_regression(self):
        """同优先级按字母序（稳定排序）。"""
        engine = self._engine_with_commands(["/theme", "/model", "/help"])
        items = engine._complete_command("/")
        texts = [i.text for i in items]
        # 空前缀语义：所有命令均为前缀匹配（cmd.startswith("/")），长度均为 5+ →
        # 按字母序：/help /model /theme
        assert texts == ["/help", "/model", "/theme"]

    def test_command_empty_prefix_defensive_regression(self):
        """空前缀防御：_ranked 返回全部命令按字母序（complete() 主入口已短路）。"""
        engine = self._engine_with_commands(["/theme", "/model", "/help"])
        items = engine._complete_command("")
        assert [i.text for i in items] == ["/help", "/model", "/theme"]

    def test_command_single_char_prefix_regression(self):
        """单字符前缀 /m：前缀匹配（长度升序）+ 字母序。"""
        engine = self._engine_with_commands(["/model-check", "/model", "/theme", "/modify"])
        items = engine._complete_command("/m")
        texts = [i.text for i in items]
        # 前缀匹配长度升序：/model(6) /modify(7) /model-check(12)
        assert texts.index("/model") < texts.index("/modify") < texts.index("/model-check")

    def test_command_case_sensitive_startswith_regression(self):
        """startswith 大小写敏感保持现状：大写命令不匹配小写前缀。"""
        engine = self._engine_with_commands(["/Model", "/model"])
        items = engine._complete_command("/m")
        texts = [i.text for i in items]
        assert texts == ["/model"]

    def test_command_no_match_empty_regression(self):
        """无匹配时返回空。"""
        engine = self._engine_with_commands(["/model", "/theme"])
        items = engine._complete_command("/zzz")
        assert items == []

    def test_command_empty_registry_empty_regression(self):
        """命令注册表为空时返回空。"""
        engine = self._engine_with_commands([])
        assert engine._complete_command("/m") == []


class TestParamCompletionSorting:
    """方向D 步骤13 — 参数补全候选语义排序（model/theme/session）。"""

    @staticmethod
    def _engine_with_models(models):
        import time
        from src.tui._completion_engine import CompletionEngine
        engine = CompletionEngine()
        engine._models_cache._value = list(models)
        engine._models_cache._expires = time.monotonic() + 100
        return engine

    def test_param_model_exact_first_regression(self):
        """/model 参数精确匹配优先。"""
        engine = self._engine_with_models(["deepseek-chat", "deepseek-reasoner", "deepseek"])
        items = engine._complete_param("/model deepseek")
        texts = [i.text for i in items]
        assert texts[0] == "deepseek"

    def test_param_model_prefix_shorter_first_regression(self):
        """/model 参数前缀匹配长度升序：deepseek-chat 排在 deepseek-reasoner 前。"""
        engine = self._engine_with_models(["deepseek-reasoner", "deepseek-chat"])
        items = engine._complete_param("/model deepseek")
        texts = [i.text for i in items]
        assert texts.index("deepseek-chat") < texts.index("deepseek-reasoner")

    def test_param_model_substring_after_prefix_regression(self):
        """/model 参数子串包含排在前缀之后。"""
        engine = self._engine_with_models(["gpt-4o", "deepseek-chat"])
        # prefix="ee"：无前缀匹配（无 "ee" 开头），deepseek-chat 为子串包含
        items = engine._complete_param("/model ee")
        texts = [i.text for i in items]
        assert texts == ["deepseek-chat"]

    def test_param_model_no_match_empty_regression(self):
        """/model 参数无匹配返回空。"""
        engine = self._engine_with_models(["deepseek-chat"])
        assert engine._complete_param("/model zzz") == []


class TestLoadSessionCompletion:
    """P1-1 回归 — /load 会话补全：title 匹配不被 sid 二次过滤丢弃。"""

    @staticmethod
    def _engine_with_sessions(sessions):
        import time
        from src.tui._completion_engine import CompletionEngine
        engine = CompletionEngine()
        engine._sessions_cache._value = list(sessions)
        engine._sessions_cache._expires = time.monotonic() + 100
        return engine

    def test_load_title_match_survives_ranking_regression(self):
        """title 匹配但 sid 不匹配的会话返回（修复前被 _ranked 二次过滤丢空）。"""
        engine = self._engine_with_sessions([
            {"id": "sess-0001-aaaa", "title": "调研 TUI 架构"},
            {"id": "sess-0002-bbbb", "title": "编写单元测试"},
        ])
        items = engine._complete_param("/load 调研")
        texts = [i.text for i in items]
        # title 前缀匹配（sid 不含 "调研"）→ 不再被丢弃
        assert texts == ["sess-0001-aaaa"]
        assert items[0].item_type == "session"

    def test_load_sid_exact_beats_title_prefix_regression(self):
        """sid 精确 > sid 前缀 > title 前缀 加权排序（多键不互斥）。"""
        engine = self._engine_with_sessions([
            {"id": "abc", "title": "xyz-proj"},
            {"id": "abcd", "title": "zzz"},
            {"id": "other", "title": "abc-doc"},
        ])
        items = engine._complete_param("/load abc")
        texts = [i.text for i in items]
        assert texts[0] == "abc"      # sid 精确
        assert texts.index("abcd") < texts.index("other")  # sid 前缀 < title 前缀

    def test_load_title_prefix_before_sid_substring_regression(self):
        """title 前缀（cat 2）排在 sid 子串（cat 3）之前。"""
        engine = self._engine_with_sessions([
            {"id": "sess-abc", "title": "文档部署"},
            {"id": "abc-文档", "title": "部署"},
        ])
        items = engine._complete_param("/load 文档")
        texts = [i.text for i in items]
        # "文档" 命中 title 前缀（sess-abc）与 sid 子串（abc-文档）→ title 前缀优先
        assert texts == ["sess-abc", "abc-文档"]

    def test_load_empty_prefix_keeps_order_regression(self):
        """/load（无参数）→ 空前缀保持注册表顺序。"""
        engine = self._engine_with_sessions([
            {"id": "b-id", "title": "B"},
            {"id": "a-id", "title": "A"},
        ])
        items = engine._complete_param("/load")
        assert [i.text for i in items] == ["b-id", "a-id"]


class TestCommandDescription:
    """Claude TUI parity 步骤 3.7 — 斜杠命令补全带描述。"""

    def test_command_completion_has_description(self):
        """/help 补全项含命令描述（来自注册表 help）。"""
        from src.tui._completion_engine import CompletionEngine
        engine = CompletionEngine()
        items = engine._complete_command("/h")
        help_item = next((i for i in items if i.text == "/help"), None)
        assert help_item is not None
        assert isinstance(help_item.desc, str)
        # /help 有描述（注册表有 help 文本）；至少非 None
        assert help_item.desc  # 非空

    def test_get_command_help_direct(self):
        """get_command_help 返回注册表描述；未知命令返回空串。"""
        from src.core.internal.commands._command_core import get_command_help
        h = get_command_help("/clear")
        assert isinstance(h, str)
        # /clear 已注册且有描述（或至少不抛异常）
        assert get_command_help("/不存在的命令") == ""
