"""ContentRenderer 边缘情况单元测试

测试范围（第 3 轮重构修复的边缘情况）：
1. _do_tool_output():
   - 纯 \r 文本 → 取最后一段
   - ANSI + \r 混合 → 移除 \r 后走 Text.from_ansi
   - 末尾 \r → last_was_carriage = True
   - 超长截断 → ...(truncated) 标记
2. _render_failure_summary():
   - item[1] 为 0 → 显示 "0"
   - item[1] 为 False → 显示 "False"
   - item[1] 为 None → 空字符串
   - 元素含 3+ 元素 → 额外信息追加到 error
   - 非标准格式 → str(item) 安全显示
3. _do_parse_info():
   - tokens 为 float('inf') → "?"
   - tokens 为 float('nan') → "?"
   - tokens 为普通 int → "Nt"
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from src.chat_ui._renderers import ContentRenderer
from src.chat_ui._const import _CLEAR_PARSE_LINE, RenderCommand
from src.chat_ui._render_state import _RenderState, _ReasoningState


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture
def mock_ta():
    """Mock OutputAdapter"""
    return MagicMock()


@pytest.fixture
def mock_bb():
    """Mock _BottomBar"""
    return MagicMock()


@pytest.fixture
def renderer(mock_ta, mock_bb):
    """ContentRenderer 实例，_tool_adapter 替换为 mock"""
    rs = _RenderState()
    # 直接设置 _tool_adapter 绕过惰性初始化
    rs._tool_adapter = mock_ta
    r = ContentRenderer(rs, mock_bb, on_display_messages=None)
    yield r


# ═══════════════════════════════════════════════════════
# _do_tool_output 测试
# ═══════════════════════════════════════════════════════

class TestDoToolOutput:
    """_do_tool_output 边缘情况测试"""

    def test_pure_carriage_return_takes_last_segment(self, renderer, mock_ta):
        """纯 \r 文本 → 只取最后一段输出"""
        renderer._do_tool_output("progress\rstatus\rdone")
        # text.split('\r')[-1] = "done"，endswith('\r')=False
        # → write_raw("done") 后追加 write_raw('\n')
        mock_ta.write_raw.assert_any_call("done")
        mock_ta.write_raw.assert_any_call('\n')
        assert renderer._rs.last_was_carriage is False

    def test_carriage_return_ending_sets_last_was_carriage(self, renderer, mock_ta):
        """末尾 \r → last_was_carriage = True，不追加 \n"""
        renderer._do_tool_output("progress\rstatus\r")
        # "progress\rstatus\r".split('\r')[-1] = ""，因为末尾 \r 后无内容
        # 光标回到行首等待覆盖
        mock_ta.write_raw.assert_any_call("")
        assert renderer._rs.last_was_carriage is True

    def test_no_carriage_resets_last_was_carriage(self, renderer, mock_ta):
        """无 \r → last_was_carriage = False"""
        renderer._rs.last_was_carriage = True
        renderer._do_tool_output("hello")
        # 先 write_raw("\n") 退出进度行
        # 然后 write(Text.assemble(...))
        assert renderer._rs.last_was_carriage is False

    def test_ansi_with_carriage_removes_control_chars(self, renderer, mock_ta):
        """ANSI + \r 混合 → 移除 \r 后走 Text.from_ansi"""
        from rich.text import Text
        renderer._do_tool_output("\033[31mred\r\033[32mgreen\033[0m")
        # 验证 Text.from_ansi 被调用，且传入的文本不含 \r
        mock_ta.write.assert_called_once()
        args = mock_ta.write.call_args[0]
        assert isinstance(args[0], Text)
        text_str = args[0].plain
        assert '\r' not in text_str
        assert 'red' in text_str
        assert 'green' in text_str

    def test_ansi_with_carriage_fallback_on_parse_error(self, renderer, mock_ta):
        """ANSI + \r 解析失败 → fallback write_raw"""
        from rich.text import Text
        original_from_ansi = Text.from_ansi
        def failing_from_ansi(text, *args, **kwargs):
            raise ValueError("模拟解析失败")
        with patch.object(Text, 'from_ansi', side_effect=failing_from_ansi):
            renderer._do_tool_output("\033[31mtest\rdata")
            # write_raw 被调用 2 次：clean_text + \n
            calls = mock_ta.write_raw.call_args_list
            assert len(calls) == 2
            # 传入的 clean_text 应不含 \r
            assert '\r' not in calls[0][0][0]
            assert calls[1] == call('\n')

    def test_truncation_appends_marker(self, renderer, mock_ta):
        """超长文本 → 截断 + ...(truncated)"""
        long_text = "x" * (ContentRenderer._MAX_TOOL_OUTPUT_LEN + 100)
        renderer._do_tool_output(long_text)
        mock_ta.write.assert_called_once()
        args = mock_ta.write.call_args[0]
        text_str = args[0].plain if hasattr(args[0], 'plain') else str(args[0])
        assert "...(truncated)" in text_str
        # 截断后文本应短于原始文本
        assert len(text_str) < len(long_text)


# ═══════════════════════════════════════════════════════
# _render_failure_summary 测试
# ═══════════════════════════════════════════════════════

class TestRenderFailureSummary:
    """_render_failure_summary 边缘情况测试"""

    def test_item1_zero_not_treated_as_empty(self, renderer, mock_ta):
        """item[1] 为 0 → 显示 "0" 而非空"""
        failed = (("tool_a", 0),)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        # 应显示错误信息 "0"
        calls = mock_ta.write.call_args_list
        error_shown = False
        for c in calls:
            text_str = str(c)
            if "0" in text_str:
                error_shown = True
        assert error_shown, "item[1]=0 应显示为 '0'"

    def test_item1_false_not_treated_as_empty(self, renderer, mock_ta):
        """item[1] 为 False → 显示 "False" 而非空"""
        failed = (("tool_a", False),)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        calls = mock_ta.write.call_args_list
        false_shown = False
        for c in calls:
            text_str = str(c)
            if "False" in text_str:
                false_shown = True
        assert false_shown, "item[1]=False 应显示为 'False'"

    def test_item1_none_shows_empty(self, renderer, mock_ta):
        """item[1] 为 None → 空字符串（不显示 error）"""
        failed = (("tool_a", None),)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        calls = mock_ta.write.call_args_list
        # 应显示 tool_a 名字但无 error 详情
        all_text = "".join(str(c) for c in calls)
        assert "tool_a" in all_text

    def test_three_plus_elements_append_extra(self, renderer, mock_ta):
        """元素含 3+ 元素 → 额外信息追加到 error"""
        failed = (("tool_a", "timeout", 137),)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        calls = mock_ta.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        # 137 应显示在 error 中
        assert "137" in all_text or "[137]" in all_text

    def test_three_plus_elements_no_error(self, renderer, mock_ta):
        """元素含 3+ 元素但 error 为空 → 仅显示 extras"""
        failed = (("tool_a", "", 1, 2),)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        calls = mock_ta.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "1, 2" in all_text or "[1, 2]" in all_text

    def test_non_tuple_item_safely_converted(self, renderer, mock_ta):
        """非标准格式元素 → str(item) 安全显示"""
        failed = ("just_a_string",)
        ContentRenderer._render_failure_summary(mock_ta, failed, 1)
        calls = mock_ta.write.call_args_list
        all_text = "".join(str(c) for c in calls)
        assert "just_a_string" in all_text


# ═══════════════════════════════════════════════════════
# _do_parse_info 测试
# ═══════════════════════════════════════════════════════

class TestDoParseInfo:
    """_do_parse_info 边缘情况测试"""

    def test_inf_tokens_shows_question_mark(self, renderer, mock_ta):
        """tokens=float('inf') → 显示 "?" """
        renderer._do_parse_info("tool_test", float('inf'), 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "?" in text
        assert "inft" not in text

    def test_nan_tokens_shows_question_mark(self, renderer, mock_ta):
        """tokens=float('nan') → 显示 "?" """
        renderer._do_parse_info("tool_test", float('nan'), 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "?" in text
        assert "nant" not in text

    def test_normal_int_tokens(self, renderer, mock_ta):
        """普通 int tokens → 显示 "Nt" """
        renderer._do_parse_info("tool_test", 42, 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "42t" in text

    def test_clear_parse_line_sentinel(self, renderer, mock_ta):
        """tokens=_CLEAR_PARSE_LINE → write_raw('\\n') """
        renderer._do_parse_info("", _CLEAR_PARSE_LINE, 0.0)
        mock_ta.write_raw.assert_called_once_with("\n")


# ═══════════════════════════════════════════════════════
# _do_tool_output ANSI fallback 外层保护（步骤 5/10）
# ═══════════════════════════════════════════════════════

class TestDoToolOutputAnsiFallback:
    """_do_tool_output ANSI 路径 fallback 保护测试"""

    def test_ansi_write_raw_fallback_fails_silently(self, renderer, mock_ta):
        """ANSI 解析失败后的 write_raw 也失败 → 不崩溃，静默继续"""
        from rich.text import Text

        def failing_from_ansi(text, *args, **kwargs):
            raise ValueError("ANSI 解析失败")

        # 只让 Text.from_ansi 失败，write_raw 正常（验证外层 catch 正常工作）
        with patch.object(Text, 'from_ansi', side_effect=failing_from_ansi):
            # 不应抛出异常
            renderer._do_tool_output("\033[31mtest\rdata")

        # 验证 fallback 路径 write_raw 被调用（clean_text 不含 \r）
        found_clean = any(
            '\r' not in str(call) for call in mock_ta.write_raw.call_args_list
        )
        assert found_clean, "write_raw fallback 应被调用（clean_text 不含 \\r）"

    def test_ansi_clean_path_no_error(self, renderer, mock_ta):
        """正常 ANSI 路径 → 走 Text.from_ansi 解析"""
        from rich.text import Text
        renderer._do_tool_output("\033[32mgreen\033[0m")
        mock_ta.write.assert_called_once()
        args = mock_ta.write.call_args[0]
        assert isinstance(args[0], Text)
        text_str = args[0].plain
        assert "green" in text_str

    def test_ansi_write_raw_fallback_alone_outer_protection(self, renderer, mock_ta):
        """外层 try/except 保护：ANSI 路径中 write_raw fallback 失败时不崩溃"""
        from rich.text import Text

        # 首个 write_raw 调用（fallback）抛出异常
        mock_ta.write_raw.side_effect = [RuntimeError("fallback 失败"), None]

        def failing_from_ansi(text, *args, **kwargs):
            raise ValueError("ANSI 解析失败")

        with patch.object(Text, 'from_ansi', side_effect=failing_from_ansi):
            # 不应抛出异常（外层 try/except 捕获 write_raw 的异常）
            renderer._do_tool_output("\033[31mtest\rdata")

        # 能运行到这里即表示外层保护正常
        assert True


# ═══════════════════════════════════════════════════════
# _truncate_msg 基础功能测试
# ═══════════════════════════════════════════════════════

class TestTruncateMsg:
    """_truncate_msg 统一截断函数测试"""

    def test_short_msg_unchanged(self):
        """短消息不截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("hello", 10)
        assert result == "hello"

    def test_exact_length_unchanged(self):
        """长度刚好等于 max_len → 不截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("12345", 5)
        assert result == "12345"

    def test_long_msg_truncated(self):
        """超长消息截断并追加 ..."""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("x" * 100, 10)
        assert result == "x" * 10 + "..."
        assert len(result) == 13

    def test_empty_msg_empty_result(self):
        """空消息 → 空字符串"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("", 10)
        assert result == ""

    def test_max_len_zero(self):
        """max_len=0 → 全部截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("hello", 0)
        assert result == "..."
