"""ContentRenderer 边缘情况单元测试

测试范围：
1. _do_parse_info():
   - tokens 为 float('inf') → "?"
   - tokens 为 float('nan') → "?"
   - tokens 为普通 int → "Nt"
2. _truncate_msg 基础功能

注：_do_tool_output / _render_failure_summary 测试已迁移到
test_chat_ui_controls.py（ToolOutputControl / ToolSummaryControl）。
宽度刷新由 ControlList.refresh_width_all() 统一管理，对应测试在
test_chat_ui_controls.py TestControlList。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
    """ContentRenderer 实例，构造注入路径全部 mock。

    由于 ChatUIConsumer 负责创建 OutputAdapter 并注入到 ContentRenderer，
    测试环境直接传入 mock_ta 作为 output_adapter 参数，避免依赖真实终端。
    """
    rs = _RenderState()
    r = ContentRenderer(rs, mock_ta, mock_bb, on_display_messages=None)
    yield r


# ═══════════════════════════════════════════════════════
# _do_parse_info 测试（通过 ParseInfoControl 代理）
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
# _truncate_msg 基础功能测试
# ═══════════════════════════════════════════════════════

class TestTruncateMsg:
    """_truncate_msg 统一截断函数测试"""

    def test_short_msg_unchanged(self):
        """短消息不截断"""
        from src.chat_ui._utils import _truncate_msg
        result = _truncate_msg("hello", 10)
        assert result == "hello"

    def test_exact_length_unchanged(self):
        """长度刚好等于 max_len → 不截断"""
        from src.chat_ui._utils import _truncate_msg
        result = _truncate_msg("12345", 5)
        assert result == "12345"

    def test_long_msg_truncated(self):
        """超长消息截断并追加 ..."""
        from src.chat_ui._utils import _truncate_msg
        result = _truncate_msg("x" * 100, 10)
        assert result == "x" * 10 + "..."
        assert len(result) == 13

    def test_empty_msg_empty_result(self):
        """空消息 → 空字符串"""
        from src.chat_ui._utils import _truncate_msg
        result = _truncate_msg("", 10)
        assert result == ""

    def test_max_len_zero(self):
        """max_len=0 → 全部截断"""
        from src.chat_ui._utils import _truncate_msg
        result = _truncate_msg("hello", 0)
        assert result == "..."


# ═══════════════════════════════════════════════════════
# 注：ContentRenderer._check_and_refresh_width 已迁移到
# RenderEngine._check_resize() → 测试移至 test_chat_ui_engine.py
# 宽度刷新由 ControlList.refresh_width_all() 统一管理 → 测试移至
# test_chat_ui_controls.py TestControlList
# ═══════════════════════════════════════════════════════


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
# _do_tool_output 测试
# ═══════════════════════════════════════════════════════

class TestDoToolOutput:
    """_do_tool_output 渲染命令测试"""

    def test_tool_output_normal(self, renderer, mock_ta):
        """正常文本 → 委托 _tool_output_ctrl.write()"""
        with patch.object(renderer._tool_output_ctrl, 'write') as m_write:
            renderer._do_tool_output("output text")
            m_write.assert_called_once_with("output text")

    def test_tool_output_recreates_when_closed(self, renderer, mock_ta):
        """控件已关闭 → 自动重建"""
        renderer._tool_output_ctrl.close()
        old_ctrl = renderer._tool_output_ctrl
        renderer._do_tool_output("new output")
        assert renderer._tool_output_ctrl is not old_ctrl
        assert renderer._tool_output_ctrl.is_closed is False

    def test_tool_output_empty_text(self, renderer, mock_ta):
        """空文本 → 仍委托（控件处理跳过逻辑）"""
        with patch.object(renderer._tool_output_ctrl, 'write') as m_write:
            renderer._do_tool_output("")
            m_write.assert_called_once_with("")

    def test_tool_output_long_text(self, renderer, mock_ta):
        """超长文本 → 透传到控件（截断由 ToolOutputControl 内部处理）。"""
        from src.chat_ui._controls import ToolOutputControl
        long_text = "x" * (ToolOutputControl._MAX_OUTPUT_LEN + 50)
        with patch.object(renderer._tool_output_ctrl, 'write') as m_write:
            renderer._do_tool_output(long_text)
            # ContentRenderer 不做截断，透传原始文本
            m_write.assert_called_once_with(long_text)


# ═══════════════════════════════════════════════════════
# _do_tool_summary 测试
# ═══════════════════════════════════════════════════════

class TestDoToolSummary:
    """_do_tool_summary 渲染命令测试"""

    def test_summary_all_success(self, renderer, mock_ta):
        """全成功 → 委托 summarize + close"""
        with patch.object(renderer._tool_summary_ctrl, 'summarize') as m_sum:
            renderer._do_tool_summary(("tool_a", "tool_b"), ())
            m_sum.assert_called_once_with(("tool_a", "tool_b"), ())
            assert renderer._tool_summary_ctrl.is_closed is True

    def test_summary_partial_failure(self, renderer, mock_ta):
        """部分失败 → 委托 summarize + close"""
        with patch.object(renderer._tool_summary_ctrl, 'summarize') as m_sum:
            renderer._do_tool_summary(("tool_a",), (("tool_b", "error"),))
            m_sum.assert_called_once_with(("tool_a",), (("tool_b", "error"),))

    def test_summary_all_failure(self, renderer, mock_ta):
        """全失败 → 委托 summarize + close"""
        with patch.object(renderer._tool_summary_ctrl, 'summarize') as m_sum:
            renderer._do_tool_summary((), (("tool_a", "err1"), ("tool_b", "err2")))
            m_sum.assert_called_once_with((), (("tool_a", "err1"), ("tool_b", "err2")))

    def test_summary_empty_skip(self, renderer, mock_ta):
        """空摘要 → summarize 仍被调用（控件内部跳过逻辑）"""
        renderer._do_tool_summary((), ())
        assert renderer._tool_summary_ctrl.is_closed is True


# ═══════════════════════════════════════════════════════
# _do_user_message 测试
# ═══════════════════════════════════════════════════════

class TestDoUserMessage:
    """_do_user_message 渲染命令测试"""

    def test_user_message_normal(self, renderer, mock_ta):
        """正常文本 → 委托 _user_msg_ctrl.write()"""
        with patch.object(renderer._user_msg_ctrl, 'write') as m_write:
            renderer._do_user_message("hello")
            m_write.assert_called_once_with("hello")

    def test_user_message_empty(self, renderer, mock_ta):
        """空文本 → 委托 _user_msg_ctrl.write()"""
        with patch.object(renderer._user_msg_ctrl, 'write') as m_write:
            renderer._do_user_message("")
            m_write.assert_called_once_with("")


# ═══════════════════════════════════════════════════════
# _do_notification 测试
# ═══════════════════════════════════════════════════════

class TestDoNotification:
    """_do_notification 渲染命令测试"""

    def test_notification_normal(self, renderer, mock_ta):
        """正常文本 → 委托 _notif_ctrl.write()"""
        with patch.object(renderer._notif_ctrl, 'write') as m_write:
            renderer._do_notification("notify")
            m_write.assert_called_once_with("notify")


# ═══════════════════════════════════════════════════════
# _do_error 测试
# ═══════════════════════════════════════════════════════

class TestDoError:
    """_do_error 渲染命令测试"""

    def test_error_normal(self, renderer, mock_ta):
        """正常消息 → 委托 _error_ctrl.write()"""
        with patch.object(renderer._error_ctrl, 'write') as m_write:
            renderer._do_error("error msg")
            m_write.assert_called_once_with("error msg")

    def test_error_truncated(self, renderer, mock_ta):
        """超长消息 → 截断后委托"""
        from src.chat_ui._const import _MAX_ERROR_LENGTH
        long_msg = "x" * (_MAX_ERROR_LENGTH + 50)
        with patch.object(renderer._error_ctrl, 'write') as m_write:
            renderer._do_error(long_msg)
            args = m_write.call_args[0][0]
            assert len(args) <= _MAX_ERROR_LENGTH + 3  # +3 for "..."
            assert args.endswith("...")


# ═══════════════════════════════════════════════════════
# _do_write_line 测试
# ═══════════════════════════════════════════════════════

class TestDoWriteLine:
    """_do_write_line 渲染命令测试"""

    def test_write_line_plain(self, renderer, mock_ta):
        """纯文本 → 委托 _line_ctrl.write_raw()"""
        with patch.object(renderer._line_ctrl, 'write_raw') as m_raw:
            renderer._do_write_line("plain text")
            m_raw.assert_called_once_with("plain text\n")

    def test_write_line_ansi(self, renderer, mock_ta):
        """ANSI 文本 → 委托 _line_ctrl.write_ansi()"""
        with patch.object(renderer._line_ctrl, 'write_ansi') as m_ansi:
            renderer._do_write_line("\033[31mred\033[0m")
            m_ansi.assert_called_once_with("\033[31mred\033[0m")

    def test_write_line_empty(self, renderer, mock_ta):
        """空文本 → 委托 _line_ctrl.write_raw() 含换行"""
        with patch.object(renderer._line_ctrl, 'write_raw') as m_raw:
            renderer._do_write_line("")
            m_raw.assert_called_once_with("\n")


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

    def test_render_known_command(self, renderer, mock_ta):
        """已知命令 → 正确分发到对应 _do_* 方法"""
        method_name = "do_" + RenderCommand.NOTIFICATION.name.lower()
        with patch.object(renderer, f"_{method_name}") as m_method:
            renderer.render((RenderCommand.NOTIFICATION, "test"))
            m_method.assert_called_once_with("test")

    def test_render_unknown_command_logs_error(self, renderer, mock_ta):
        """未知命令 ID → 记录日志（不崩溃）"""
        with patch('src.chat_ui._renderers._logger.error') as m_log:
            renderer.render((255,))
            m_log.assert_called_once()


# ═══════════════════════════════════════════════════════
# TestContentRendererRefreshWidth — 宽度刷新委托
# ═══════════════════════════════════════════════════════

class TestContentRendererRefreshWidth:
    """ContentRenderer.refresh_width() 委托路径测试

    验证 refresh_width() 委托给 ControlList.refresh_width_all()，
    不再单独遍历 _RenderState 的 reasoning/content MarkdownControl。
    """

    def test_refresh_width_delegates_to_control_list(self, renderer):
        """refresh_width() 调用 ControlList.refresh_width_all()"""
        with patch.object(renderer._control_list, 'refresh_width_all') as m_refresh:
            renderer.refresh_width()
            m_refresh.assert_called_once()

    def test_refresh_width_no_crash_when_empty(self, renderer):
        """空 ControlList 时 refresh_width() 不崩溃"""
        # 清空所有控件
        renderer._control_list.close_all()
        # 不应抛异常
        renderer.refresh_width()

    def test_refresh_width_skips_closed_controls(self, renderer):
        """已关闭的控件不会被 refresh_width。"""
        # 关闭所有控件
        for ctrl in renderer._control_list._controls:
            ctrl.close()
        with patch.object(renderer._control_list, 'refresh_width_all') as m_refresh:
            renderer.refresh_width()
            m_refresh.assert_called_once()
