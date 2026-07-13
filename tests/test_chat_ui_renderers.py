"""ContentRenderer 边缘情况单元测试

测试范围：
1. _do_parse_info():
   - tokens 为 float('inf') → "?"
   - tokens 为 float('nan') → "?"
   - tokens 为普通 int → "Nt"
2. _truncate_msg 基础功能
3. 各 _do_* 方法直接输出到 OutputAdapter 的行为
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from rich.text import Text

from src.chat_ui.renderer import TuiRenderer as ContentRenderer
from src.chat_ui.const import _CLEAR_PARSE_LINE, RenderCommand
from src.chat_ui.renderer import _RenderState
from src.chat_ui.render_state import _ReasoningState


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
    """ContentRenderer 实例，构造注入路径全部 mock。

    由于 ChatUIConsumer 负责创建 OutputAdapter 并注入到 ContentRenderer，
    测试环境直接传入 mock_ta 作为 output_adapter 参数，避免依赖真实终端。
    """
    rs = _RenderState()
    r = ContentRenderer(rs, mock_ta, mock_bb, on_display_messages=None)
    yield r


# ═══════════════════════════════════════════════════════
# _do_parse_info 测试（通过 sys.__stdout__ 输出）
# ═══════════════════════════════════════════════════════

class TestDoParseInfo:
    """_do_parse_info 边缘情况测试

    注：新实现直接写入 sys.__stdout__，不再委托 ParseInfoControl。
    """

    def test_inf_tokens_shows_question_mark(self, renderer):
        """tokens=float('inf') → 显示 "?" """
        with patch('sys.__stdout__') as mock_stdout:
            renderer._do_parse_info("tool_test", float('inf'), 1.5)
            mock_stdout.write.assert_called_once()
            text = mock_stdout.write.call_args[0][0]
            assert "?" in text
            assert "inf" not in text

    def test_nan_tokens_shows_question_mark(self, renderer):
        """tokens=float('nan') → 显示 "?" """
        with patch('sys.__stdout__') as mock_stdout:
            renderer._do_parse_info("tool_test", float('nan'), 1.5)
            mock_stdout.write.assert_called_once()
            text = mock_stdout.write.call_args[0][0]
            assert "?" in text
            assert "nan" not in text

    def test_normal_int_tokens(self, renderer):
        """普通 int tokens → 显示 "Nt" """
        with patch('sys.__stdout__') as mock_stdout:
            renderer._do_parse_info("tool_test", 42, 1.5)
            mock_stdout.write.assert_called_once()
            text = mock_stdout.write.call_args[0][0]
            assert "42t" in text

    def test_clear_parse_line_sentinel(self, renderer):
        """tokens=_CLEAR_PARSE_LINE → write('\\n') """
        with patch('sys.__stdout__') as mock_stdout:
            renderer._do_parse_info("", _CLEAR_PARSE_LINE, 0.0)
            mock_stdout.write.assert_called_once_with("\n")
            mock_stdout.flush.assert_called_once()


# ═══════════════════════════════════════════════════════
# _truncate_msg 基础功能测试
# ═══════════════════════════════════════════════════════

class TestTruncateMsg:
    """_truncate_msg 统一截断函数测试"""

    def test_short_msg_unchanged(self):
        """短消息不截断"""
        from src.chat_ui.utils import _truncate_msg
        result = _truncate_msg("hello", 10)
        assert result == "hello"

    def test_exact_length_unchanged(self):
        """长度刚好等于 max_len → 不截断"""
        from src.chat_ui.utils import _truncate_msg
        result = _truncate_msg("12345", 5)
        assert result == "12345"

    def test_long_msg_truncated(self):
        """超长消息截断并追加 ..."""
        from src.chat_ui.utils import _truncate_msg
        result = _truncate_msg("x" * 100, 10)
        assert result == "x" * 10 + "..."
        assert len(result) == 13

    def test_empty_msg_empty_result(self):
        """空消息 → 空字符串"""
        from src.chat_ui.utils import _truncate_msg
        result = _truncate_msg("", 10)
        assert result == ""

    def test_max_len_zero(self):
        """max_len=0 → 全部截断"""
        from src.chat_ui.utils import _truncate_msg
        result = _truncate_msg("hello", 0)
        assert result == "..."


# ═══════════════════════════════════════════════════════
# _do_reasoning 测试
# ═══════════════════════════════════════════════════════

class TestDoReasoning:
    """_do_reasoning 渲染命令测试"""

    def test_first_reasoning_writes_header(self, renderer, mock_ta):
        """INACTIVE 状态下首次调用 → 写入 _THINKING_HEADER + 内容。"""
        renderer._do_reasoning("hello")
        assert renderer._rs.reasoning is not None
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE

    def test_second_reasoning_no_header(self, renderer, mock_ta):
        """ACTIVE 状态下再次调用 → 仅写入内容，无 header。"""
        renderer._do_reasoning("first")
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE
        renderer._do_reasoning("second")
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE

    def test_reasoning_after_closed_reopens(self, renderer, mock_ta):
        """CLOSED 状态下调用 → reopen_reasoning → 重新创建渲染器。"""
        renderer._rs.close_reasoning()
        assert renderer._rs.reasoning_state == _ReasoningState.CLOSED
        renderer._do_reasoning("after closed")
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE
        assert renderer._rs.reasoning is not None

    def test_reasoning_empty_text_creates_control(self, renderer, mock_ta):
        """空文本 → 仍创建渲染器（跳过滤器在 dispatcher 层，render 层不做过滤）。"""
        renderer._do_reasoning("")
        # render 层不跳过空文本，dispatcher 负责过滤
        assert renderer._rs.reasoning is not None


# ═══════════════════════════════════════════════════════
# _do_content 测试
# ═══════════════════════════════════════════════════════

class TestDoContent:
    """_do_content 渲染命令测试"""

    def test_content_normal(self, renderer, mock_ta):
        """正常写入 → 创建内容渲染器并写入"""
        renderer._do_content("hello")
        assert renderer._rs.content is not None

    def test_content_closes_reasoning(self, renderer, mock_ta):
        """推理进行中时写入内容 → 关闭推理渲染器"""
        renderer._do_reasoning("reasoning...")
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE
        renderer._do_content("content")
        assert renderer._rs.reasoning_state == _ReasoningState.CLOSED

    def test_content_after_reasoning_closed(self, renderer, mock_ta):
        """推理已关闭时写入内容 → 不再关闭（幂等）"""
        renderer._rs.close_reasoning()
        renderer._do_content("content")
        assert renderer._rs.content is not None


# ═══════════════════════════════════════════════════════
# _do_phase_done 测试
# ═══════════════════════════════════════════════════════

class TestDoPhaseDone:
    """_do_phase_done 渲染命令测试"""

    def test_phase_done_reasoning(self, renderer, mock_ta):
        """phase='reasoning' → 关闭推理"""
        renderer._do_reasoning("thinking")
        assert renderer._rs.reasoning_state == _ReasoningState.ACTIVE
        renderer._do_phase_done("reasoning")
        assert renderer._rs.reasoning_state == _ReasoningState.CLOSED

    def test_phase_done_content(self, renderer, mock_ta):
        """phase='content' → 关闭内容渲染器"""
        renderer._do_content("output")
        assert renderer._rs.content is not None
        renderer._do_phase_done("content")
        assert renderer._rs.content is None


# ═══════════════════════════════════════════════════════
# _do_tool_count 测试
# ═══════════════════════════════════════════════════════

class TestDoToolCount:
    """工具计数渲染命令测试"""

    def test_tool_count_inc(self, renderer, mock_bb):
        """TOOL_COUNT_INC → 委托 _bb.increment_tool()"""
        renderer._do_tool_count_inc()
        mock_bb.increment_tool.assert_called_once()

    def test_tool_count_dec(self, renderer, mock_bb):
        """TOOL_COUNT_DEC → 委托 _bb.decrement_tool()"""
        renderer._do_tool_count_dec()
        mock_bb.decrement_tool.assert_called_once()

    def test_tool_fail_inc(self, renderer, mock_bb):
        """TOOL_FAIL_INC → 委托 _bb.increment_tool_fail()"""
        renderer._do_tool_fail_inc()
        mock_bb.increment_tool_fail.assert_called_once()


# ═══════════════════════════════════════════════════════
# _do_tool_output 测试（直接通过 OutputAdapter 输出）
# ═══════════════════════════════════════════════════════

class TestDoToolOutput:
    """_do_tool_output 渲染命令测试

    不再委托 ToolOutputControl，直接在方法内处理 \\r 和 ANSI 后
    通过 OutputAdapter 输出。
    """

    def test_tool_output_normal(self, renderer, mock_ta):
        """正常文本（无 \\r）→ adapter.write 以 dim 样式 + 左边缘 │ 输出"""
        renderer._do_tool_output("output text")
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        # 宽屏模式（默认）下输出含左边缘 │ 字符
        assert "output text" in text_arg.plain

    def test_tool_output_empty_text(self, renderer, mock_ta):
        """空文本 → 仍调用 adapter.write"""
        renderer._do_tool_output("")
        mock_ta.write.assert_called_once()

    def test_tool_output_long_text_truncated(self, renderer, mock_ta):
        """超长文本 → 截断后输出（含呼吸色左边缘 │）"""
        long_text = "x" * 10050
        renderer._do_tool_output(long_text)
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        # 宽屏模式（默认）下输出含呼吸色左边缘 "  │   " 前缀 + 截断文本
        assert "x" * 10000 in text_arg.plain
        assert "...(truncated)" in text_arg.plain

    def test_tool_output_with_carriage_return(self, renderer, mock_ta):
        """含 \\r 的文本 → 取最后一段通过 write_raw 输出，续写入 \\n"""
        renderer._do_tool_output("first\rsecond")
        assert mock_ta.write_raw.call_count == 2
        assert mock_ta.write_raw.call_args_list[0][0][0] == "second"
        assert mock_ta.write_raw.call_args_list[1][0][0] == "\n"

    def test_tool_output_carriage_ends_with_r(self, renderer, mock_ta):
        """以 \\r 结尾 → 不追加额外 \\n"""
        renderer._do_tool_output("first\r")
        mock_ta.write_raw.assert_called_once()
        # 最后一段是空字符串
        assert mock_ta.write_raw.call_args[0][0] == ""

    def test_tool_output_carriage_with_newline(self, renderer, mock_ta):
        """含 \\r 且不以 \\r 结尾 → 末尾追加 \\n"""
        renderer._do_tool_output("a\rb")
        assert mock_ta.write_raw.call_count == 2
        assert mock_ta.write_raw.call_args_list[0][0][0] == "b"
        assert mock_ta.write_raw.call_args_list[1][0][0] == "\n"


# ═══════════════════════════════════════════════════════
# _do_tool_summary 测试（直接通过 OutputAdapter 格式化输出）
# ═══════════════════════════════════════════════════════

class TestDoToolSummary:
    """_do_tool_summary 渲染命令测试

    不再委托 ToolSummaryControl，直接在方法内格式化后通过 OutputAdapter 输出。
    """

    def test_summary_all_success(self, renderer, mock_ta):
        """全成功 → adapter.write 输出成功信息"""
        renderer._do_tool_summary(("tool_a", "tool_b"), ())
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        assert "2工具完成" in text_arg.plain

    def test_summary_partial_failure(self, renderer, mock_ta):
        """部分失败 → adapter.write 输出失败信息"""
        renderer._do_tool_summary(("tool_a",), (("tool_b", "error"),))
        mock_ta.write.assert_called()
        texts = " ".join(
            c[0][0].plain for c in mock_ta.write.call_args_list
            if c[0] and hasattr(c[0][0], 'plain')
        )
        assert "失败" in texts
        assert "1/2" in texts

    def test_summary_all_failure(self, renderer, mock_ta):
        """全失败 → adapter.write 输出全部失败信息"""
        renderer._do_tool_summary((), (("tool_a", "err1"), ("tool_b", "err2")))
        mock_ta.write.assert_called()
        texts = " ".join(
            c[0][0].plain for c in mock_ta.write.call_args_list
            if c[0] and hasattr(c[0][0], 'plain')
        )
        assert "失败" in texts
        assert "全部失败" in texts

    def test_summary_empty_skip(self, renderer, mock_ta):
        """空摘要 → 不输出任何内容"""
        renderer._do_tool_summary((), ())
        mock_ta.write.assert_not_called()


# ═══════════════════════════════════════════════════════
# _do_user_message 测试（直接通过 OutputAdapter 输出）
# ═══════════════════════════════════════════════════════

class TestDoUserMessage:
    """_do_user_message 渲染命令测试

    不再委托 UserMsgControl，直接通过 OutputAdapter 输出。
    """

    def test_user_message_normal(self, renderer, mock_ta):
        """正常文本 → adapter.write 以 bold 样式输出"""
        renderer._do_user_message("hello")
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        assert "hello" in text_arg.plain

    def test_user_message_empty(self, renderer, mock_ta):
        """空文本 → adapter.write 仍被调用"""
        renderer._do_user_message("")
        mock_ta.write.assert_called_once()


# ═══════════════════════════════════════════════════════
# _do_notification 测试（直接通过 OutputAdapter 输出）
# ═══════════════════════════════════════════════════════

class TestDoNotification:
    """_do_notification 渲染命令测试

    不再委托 NotifControl，直接通过 OutputAdapter 输出。
    """

    def test_notification_normal(self, renderer, mock_ta):
        """正常文本 → adapter.write 以 success 样式输出"""
        renderer._do_notification("notify")
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        assert "notify" in text_arg.plain


# ═══════════════════════════════════════════════════════
# _do_error 测试（直接通过 OutputAdapter 输出）
# ═══════════════════════════════════════════════════════

class TestDoError:
    """_do_error 渲染命令测试

    不再委托 ErrorControl，直接在方法内截断后通过 OutputAdapter 输出。
    """

    def test_error_normal(self, renderer, mock_ta):
        """正常消息 → adapter.write 以 error 样式输出"""
        renderer._do_error("error msg")
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        assert "error msg" in text_arg.plain

    def test_error_truncated(self, renderer, mock_ta):
        """超长消息 → 截断后输出"""
        from src.chat_ui.const import _MAX_ERROR_LENGTH
        long_msg = "x" * (_MAX_ERROR_LENGTH + 50)
        renderer._do_error(long_msg)
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)
        assert "..." in text_arg.plain
        # 截断后的消息主体（不含前缀）不应超过 _MAX_ERROR_LENGTH + 3（...）
        body = text_arg.plain.replace("\n  ! ", "", 1)
        assert len(body) <= _MAX_ERROR_LENGTH + 3


# ═══════════════════════════════════════════════════════
# _do_write_line 测试（直接通过 OutputAdapter 输出）
# ═══════════════════════════════════════════════════════

class TestDoWriteLine:
    """_do_write_line 渲染命令测试

    不再委托 LineControl，直接通过 OutputAdapter 输出。
    """

    def test_write_line_plain(self, renderer, mock_ta):
        """纯文本 → adapter.write_raw 输出并追加换行"""
        renderer._do_write_line("plain text")
        mock_ta.write_raw.assert_called_once_with("plain text\n")

    def test_write_line_ansi(self, renderer, mock_ta):
        """ANSI 文本 → adapter.write(Text.from_ansi(...))"""
        renderer._do_write_line("\033[31mred\033[0m")
        mock_ta.write.assert_called_once()
        text_arg = mock_ta.write.call_args[0][0]
        assert isinstance(text_arg, Text)

    def test_write_line_empty(self, renderer, mock_ta):
        """空文本 → 仅输出换行"""
        renderer._do_write_line("")
        mock_ta.write_raw.assert_called_once_with("\n")


# ═══════════════════════════════════════════════════════
# _do_display_messages 测试
# ═══════════════════════════════════════════════════════

class TestDoDisplayMessages:
    """_do_display_messages 渲染命令测试"""

    def test_display_messages_with_callback(self):
        """有回调时 → 调用 _on_display_messages()"""
        mock_cb = MagicMock()
        rs = _RenderState()
        r = ContentRenderer(rs, MagicMock(), MagicMock(), on_display_messages=mock_cb)
        msgs = [{"role": "user", "content": "hello"}]
        r._do_display_messages(msgs, speed=2)
        mock_cb.assert_called_once_with(msgs, speed=2)

    def test_display_messages_without_callback(self, renderer, mock_ta):
        """无回调时 → 安全跳过（不崩溃）"""
        renderer._do_display_messages([{"role": "user", "content": "hi"}], speed=1)
        # 不应崩溃


# ═══════════════════════════════════════════════════════
# render() 分发测试
# ═══════════════════════════════════════════════════════

class TestRender:
    """render() 命令分发测试"""

    def test_render_dispatch_has_15_entries(self):
        """_RENDER_DISPATCH 应包含全部 15 种命令类型"""
        from src.chat_ui.renderer import _RENDER_DISPATCH
        assert len(_RENDER_DISPATCH) == 15, (
            f"期望 15 种渲染命令，实际 {len(_RENDER_DISPATCH)} 种"
        )

    def test_render_known_command(self, renderer, mock_ta):
        """已知命令 → 正确分发到对应 _do_* 方法"""
        method_name = "do_" + RenderCommand.NOTIFICATION.name.lower()
        with patch.object(renderer, f"_{method_name}") as m_method:
            renderer.render((RenderCommand.NOTIFICATION, "test"))
            m_method.assert_called_once_with("test")

    def test_render_dropped_notification(self, renderer, mock_ta):
        """丢弃通知格式的 NOTIFICATION 命令正确分发到 _do_notification"""
        method_name = "do_" + RenderCommand.NOTIFICATION.name.lower()
        with patch.object(renderer, f"_{method_name}") as m_method:
            renderer.render((RenderCommand.NOTIFICATION, "渲染队列已丢弃 100 条命令"))
            m_method.assert_called_once_with("渲染队列已丢弃 100 条命令")

    def test_render_unknown_command_logs_error(self, renderer, mock_ta):
        """未知命令 ID → 记录日志（不崩溃）"""
        with patch('src.chat_ui.renderer._logger.error') as m_log:
            renderer.render((255,))
            m_log.assert_called_once()


# ═══════════════════════════════════════════════════════
# ContentRenderer.refresh_width() 测试
# ═══════════════════════════════════════════════════════

@pytest.mark.skip(reason="refresh_width() 已从 ContentRenderer 移除")
class TestContentRendererRefreshWidth:
    """ContentRenderer.refresh_width() 委托路径测试（已移除）"""

    def test_refresh_width_delegates_to_adapter(self, renderer, mock_ta):
        """refresh_width() 调用 OutputAdapter.force_refresh_width()（已移除）"""
        pass

    def test_refresh_width_no_crash(self, renderer, mock_ta):
        """任何时候 refresh_width() 不崩溃（已移除）"""
        pass


# ═══════════════════════════════════════════════════════
# 组件动效装饰（左边缘呼吸边框）测试
# ═══════════════════════════════════════════════════════

class TestGlowBorder:
    """组件左边缘呼吸边框装饰 — 宽屏/窄屏降级行为测试

    验证各组件在宽屏时有呼吸边框字符 │，窄屏时降级为无边框。
    """

    # ── WriteLineBlock ──

    @patch('src.chat_ui.components._write_line.is_narrow')
    def test_write_line_widescreen_has_border(self, mock_narrow):
        """WriteLineBlock 宽屏 → 输出含 │ 边框字符（纯文本走 write_raw）"""
        from src.chat_ui.components._write_line import WriteLineBlock
        mock_narrow.return_value = False
        mock_adapter = MagicMock()
        block = WriteLineBlock("test text")
        block.render_to_adapter(mock_adapter)
        # 纯文本 + 宽屏 → adapter.write_raw(f"  {edge_ansi} {text}\n")
        written = mock_adapter.write_raw.call_args
        assert written is not None, "应调用 adapter.write_raw"
        text_arg = written[0][0]
        assert "\u2502" in text_arg

    @patch('src.chat_ui.components._write_line.is_narrow')
    def test_write_line_narrow_no_border(self, mock_narrow):
        """WriteLineBlock 窄屏 → 输出无 │ 边框字符"""
        from src.chat_ui.components._write_line import WriteLineBlock
        mock_narrow.return_value = True
        mock_adapter = MagicMock()
        block = WriteLineBlock("test text")
        block.render_to_adapter(mock_adapter)
        # 纯文本 + is_narrow() → adapter.write_raw(text + "\n")
        written = mock_adapter.write_raw.call_args
        assert written is not None, "应调用 adapter.write_raw"
        text_arg = written[0][0]
        assert "\u2502" not in text_arg

    @patch('src.chat_ui.components._write_line.is_narrow')
    def test_write_line_widescreen_ansi_border(self, mock_narrow):
        """WriteLineBlock 宽屏+ANSI文本 → 输出含 │ 边框字符"""
        from src.chat_ui.components._write_line import WriteLineBlock
        mock_narrow.return_value = False
        mock_adapter = MagicMock()
        block = WriteLineBlock("\033[31mred\033[0m")
        block.render_to_adapter(mock_adapter)
        written = mock_adapter.write.call_args
        assert written is not None, "应调用 adapter.write"
        text_arg = written[0][0]
        assert isinstance(text_arg, Text)
        assert "\u2502" in text_arg.plain

    @patch('src.chat_ui.components._write_line.is_narrow')
    def test_write_line_narrow_ansi_no_border(self, mock_narrow):
        """WriteLineBlock 窄屏+ANSI文本 → 输出无 │ 边框字符"""
        from src.chat_ui.components._write_line import WriteLineBlock
        mock_narrow.return_value = True
        mock_adapter = MagicMock()
        block = WriteLineBlock("\033[31mred\033[0m")
        block.render_to_adapter(mock_adapter)
        written = mock_adapter.write.call_args
        assert written is not None, "应调用 adapter.write"
        text_arg = written[0][0]
        assert isinstance(text_arg, Text)
        assert "\u2502" not in text_arg.plain

    # ── ToolOutputBlock ──

    @patch('src.chat_ui.components._tool_output.is_narrow')
    def test_tool_output_widescreen_has_border(self, mock_narrow):
        """ToolOutputBlock 宽屏 → 输出含 │ 边框字符"""
        from src.chat_ui.components._tool_output import ToolOutputBlock
        mock_narrow.return_value = False
        mock_adapter = MagicMock()
        block = ToolOutputBlock("tool output")
        block.render_to_adapter(mock_adapter)
        written = mock_adapter.write.call_args
        assert written is not None, "应调用 adapter.write"
        text_arg = written[0][0]
        assert isinstance(text_arg, Text)
        assert "\u2502" in text_arg.plain

    @patch('src.chat_ui.components._tool_output.is_narrow')
    def test_tool_output_narrow_no_border(self, mock_narrow):
        """ToolOutputBlock 窄屏 → 输出无 │ 边框字符"""
        from src.chat_ui.components._tool_output import ToolOutputBlock
        mock_narrow.return_value = True
        mock_adapter = MagicMock()
        block = ToolOutputBlock("tool output")
        block.render_to_adapter(mock_adapter)
        written = mock_adapter.write.call_args
        assert written is not None, "应调用 adapter.write"
        text_arg = written[0][0]
        assert isinstance(text_arg, Text)
        assert "\u2502" not in text_arg.plain

    # ── ErrorBlock ──

    @patch('src.chat_ui.components._error.is_narrow')
    def test_error_widescreen_has_border(self, mock_narrow):
        """ErrorBlock 宽屏 → 输出含 │ 边框字符"""
        from src.chat_ui.components._error import ErrorBlock
        mock_narrow.return_value = False
        block = ErrorBlock("error message")
        text = block.render()
        assert isinstance(text, Text)
        assert "\u2502" in text.plain

    @patch('src.chat_ui.components._error.is_narrow')
    def test_error_narrow_no_border(self, mock_narrow):
        """ErrorBlock 窄屏 → 输出无 │ 边框字符"""
        from src.chat_ui.components._error import ErrorBlock
        mock_narrow.return_value = True
        block = ErrorBlock("error message")
        text = block.render()
        assert isinstance(text, Text)
        assert "\u2502" not in text.plain

    # ── NotificationBlock ──

    @patch('src.chat_ui.components._notification.is_narrow')
    def test_notification_widescreen_has_border(self, mock_narrow):
        """NotificationBlock 宽屏 → 输出含 │ 边框字符"""
        from src.chat_ui.components._notification import NotificationBlock
        mock_narrow.return_value = False
        block = NotificationBlock("notification")
        text = block.render()
        assert isinstance(text, Text)
        assert "\u2502" in text.plain

    @patch('src.chat_ui.components._notification.is_narrow')
    def test_notification_narrow_no_border(self, mock_narrow):
        """NotificationBlock 窄屏 → 输出无 │ 边框字符"""
        from src.chat_ui.components._notification import NotificationBlock
        mock_narrow.return_value = True
        block = NotificationBlock("notification")
        text = block.render()
        assert isinstance(text, Text)
        assert "\u2502" not in text.plain


# ═══════════════════════════════════════════════════════
# _do_subagent_frame 测试（委托给 BottomBar）
# ═══════════════════════════════════════════════════════

class TestDoSubagentFrame:
    """_do_subagent_frame 渲染命令测试 — 委托给 BottomBar"""

    def test_subagent_frame_delegates_to_bottom_bar(self, renderer, mock_bb):
        """SUBAGENT_FRAME 命令 → 调用 mock_bb.set_subagent_frame"""
        mock_lines = ["summary line", "agent line 1", "agent line 2"]
        frame_data = (mock_lines, 25, 0, "\033[K")
        renderer._do_subagent_frame(frame_data)
        mock_bb.set_subagent_frame.assert_called_once_with(mock_lines)

    def test_subagent_frame_empty_lines_skipped(self, renderer, mock_bb):
        """空 frame_lines → 不调用 set_subagent_frame"""
        renderer._do_subagent_frame(None)
        mock_bb.set_subagent_frame.assert_not_called()
        renderer._do_subagent_frame(())
        mock_bb.set_subagent_frame.assert_not_called()

    def test_subagent_frame_no_set_subagent_method(self, renderer, mock_bb):
        """mock_bb 无 set_subagent_frame 方法 → 静默跳过不崩溃"""
        from unittest.mock import MagicMock
        # 用 spec 限制的 mock，不含 set_subagent_frame
        restricted_bb = MagicMock(spec=['force_redraw', 'increment_tool'])
        renderer._bb = restricted_bb
        frame_data = (["line1"], 25, 0, "\033[K")
        # 不应抛出异常；若抛出，pytest 自动失败并输出完整 traceback
        renderer._do_subagent_frame(frame_data)
