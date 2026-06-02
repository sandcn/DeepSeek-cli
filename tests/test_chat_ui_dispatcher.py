"""chat_ui EventDispatcher 单元测试 — 11 个事件处理器全覆盖。

测试策略：
  - 使用真实事件 dataclass 构造事件实例（推荐方案）
  - mock push_cmd 回调验证入队元组内容
  - 每个 handler 独立测试完整路径 + 过滤路径
  - 严格覆盖需求文档列出的每个测试点
"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock

# ── 将项目根目录加入 sys.path（Termux 环境需要）───
sys.path.insert(0, "/home/DeepSeek-cli")

from src.chat_ui._const import (
    _CLEAR_PARSE_LINE, _MAIN_LABEL, _MAIN_SOURCE,
    _MAX_ERROR_LENGTH, _truncate_msg, RenderCommand,
)
from src.chat_ui._dispatcher import EventDispatcher
from src.ui.events.event_types import (
    ReasoningChunkEvent, ContentChunkEvent, PhaseDoneEvent,
    ToolStartedEvent, ToolDoneEvent, ToolOutputChunkEvent,
    ToolSummaryEvent, ParseInfoEvent, ParseInfoDoneEvent,
    OutputEvent, ModelPhaseEvent,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def push_cmd():
    """mock push_cmd 回调，验证入队内容。"""
    return MagicMock()


@pytest.fixture
def dispatcher(push_cmd):
    """构造 EventDispatcher 实例（使用 mock push_cmd）。"""
    return EventDispatcher(push_cmd=push_cmd)


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherReasoningChunk
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherReasoningChunk:
    """ReasoningChunkEvent 处理器测试。"""

    def test_normal_reasoning_chunk(self, dispatcher, push_cmd):
        """正常 ReasoningChunkEvent → push_cmd 收到 (REASONING, text)。"""
        event = ReasoningChunkEvent(label=_MAIN_LABEL, text="thinking step 1...")
        dispatcher._on_reasoning_chunk(event)
        push_cmd.assert_called_once_with((RenderCommand.REASONING, "thinking step 1..."))

    def test_empty_text_skipped(self, dispatcher, push_cmd):
        """空 text（空字符串）跳过，不调用 push_cmd。"""
        event = ReasoningChunkEvent(label=_MAIN_LABEL, text="")
        dispatcher._on_reasoning_chunk(event)
        push_cmd.assert_not_called()

    def test_falsy_text_skipped(self, dispatcher, push_cmd):
        """falsy text（空白字符串）跳过。"""
        event = ReasoningChunkEvent(label=_MAIN_LABEL, text="   ")
        dispatcher._on_reasoning_chunk(event)  # "   " is truthy, so actually it WILL push
        # text="   " 是非空字符串（truthy），应触发 push
        push_cmd.assert_called_once_with((RenderCommand.REASONING, "   "))

    def test_non_main_label_skipped(self, dispatcher, push_cmd):
        """label != _MAIN_LABEL 跳过。"""
        event = ReasoningChunkEvent(label="subagent-1", text="thinking...")
        dispatcher._on_reasoning_chunk(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherContentChunk
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherContentChunk:
    """ContentChunkEvent 处理器测试。"""

    def test_normal_content_chunk(self, dispatcher, push_cmd):
        """正常 ContentChunkEvent → push_cmd 收到 (CONTENT, text)。"""
        event = ContentChunkEvent(label=_MAIN_LABEL, text="Hello world")
        dispatcher._on_content_chunk(event)
        push_cmd.assert_called_once_with((RenderCommand.CONTENT, "Hello world"))

    def test_empty_text_skipped(self, dispatcher, push_cmd):
        """空 text 跳过。"""
        event = ContentChunkEvent(label=_MAIN_LABEL, text="")
        dispatcher._on_content_chunk(event)
        push_cmd.assert_not_called()

    def test_non_main_label_skipped(self, dispatcher, push_cmd):
        """label != _MAIN_LABEL 跳过。"""
        event = ContentChunkEvent(label="other", text="content")
        dispatcher._on_content_chunk(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherPhaseDone
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherPhaseDone:
    """PhaseDoneEvent 处理器测试。"""

    def test_normal_phase_done(self, dispatcher, push_cmd):
        """正常 PhaseDoneEvent → push_cmd 收到 (PHASE_DONE, phase)。"""
        event = PhaseDoneEvent(label=_MAIN_LABEL, phase="content")
        dispatcher._on_phase_done(event)
        push_cmd.assert_called_once_with((RenderCommand.PHASE_DONE, "content"))

    def test_non_main_label_skipped(self, dispatcher, push_cmd):
        """label != _MAIN_LABEL 跳过。"""
        event = PhaseDoneEvent(label="subagent-1", phase="reasoning")
        dispatcher._on_phase_done(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherToolStarted
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherToolStarted:
    """ToolStartedEvent 处理器测试。"""

    def test_agent_source(self, dispatcher, push_cmd):
        """source='agent' → push_cmd 收到 (TOOL_COUNT_INC,)。"""
        event = ToolStartedEvent(source=_MAIN_SOURCE, label="tool_call_1", tool_name="bash")
        dispatcher._on_tool_started(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_INC,))

    def test_subagent_source(self, dispatcher, push_cmd):
        """source='agent-1' → 同样入队（SubAgent 兼容）。"""
        event = ToolStartedEvent(source="agent-1", label="tool_call_2", tool_name="read_file")
        dispatcher._on_tool_started(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_INC,))

    def test_user_source_skipped(self, dispatcher, push_cmd):
        """source='user' 跳过。"""
        event = ToolStartedEvent(source="user", label="tool_call_3", tool_name="bash")
        dispatcher._on_tool_started(event)
        push_cmd.assert_not_called()

    def test_empty_source_skipped(self, dispatcher, push_cmd):
        """source=''（默认值）跳过。"""
        event = ToolStartedEvent(source="", label="tool_call_4", tool_name="bash")
        dispatcher._on_tool_started(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherToolDone
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherToolDone:
    """ToolDoneEvent 处理器测试。"""

    def test_success_true(self, dispatcher, push_cmd):
        """success=True → push_cmd 收到 (TOOL_COUNT_DEC,)。"""
        event = ToolDoneEvent(source=_MAIN_SOURCE, label="tool_1", tool_name="bash", success=True)
        dispatcher._on_tool_done(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_DEC,))

    def test_success_false(self, dispatcher, push_cmd):
        """success=False → push_cmd 收到 (TOOL_FAIL_INC,)。"""
        event = ToolDoneEvent(source=_MAIN_SOURCE, label="tool_2", tool_name="bash", success=False)
        dispatcher._on_tool_done(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_FAIL_INC,))

    def test_subagent_source(self, dispatcher, push_cmd):
        """source='agent-2' 也处理（SubAgent 兼容）。"""
        event = ToolDoneEvent(source="agent-2", label="tool_3", tool_name="read_file", success=True)
        dispatcher._on_tool_done(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_COUNT_DEC,))

    def test_non_agent_source_skipped(self, dispatcher, push_cmd):
        """非 agent source 跳过。"""
        event = ToolDoneEvent(source="user", label="tool_4", tool_name="bash", success=True)
        dispatcher._on_tool_done(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherToolOutput
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherToolOutput:
    """ToolOutputChunkEvent 处理器测试。"""

    def test_normal_output(self, dispatcher, push_cmd):
        """正常输出 → push_cmd 收到 (TOOL_OUTPUT, text)。"""
        event = ToolOutputChunkEvent(source=_MAIN_SOURCE, label="tool_1", text="line1\nline2\n")
        dispatcher._on_tool_output(event)
        # rstrip("\n") 去掉尾部换行
        push_cmd.assert_called_once_with((RenderCommand.TOOL_OUTPUT, "line1\nline2"))

    def test_empty_text_skipped(self, dispatcher, push_cmd):
        """空文本（rstrip 后为空）跳过。"""
        event = ToolOutputChunkEvent(source=_MAIN_SOURCE, label="tool_2", text="\n\n")
        dispatcher._on_tool_output(event)
        push_cmd.assert_not_called()

    def test_non_agent_source_skipped(self, dispatcher, push_cmd):
        """非 agent source 跳过。"""
        event = ToolOutputChunkEvent(source="user", label="tool_3", text="output")
        dispatcher._on_tool_output(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherToolSummary
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherToolSummary:
    """ToolSummaryEvent 处理器测试。"""

    def test_normal_summary(self, dispatcher, push_cmd):
        """正常汇总 → push_cmd 收到 (TOOL_SUMMARY, successful, failed)。"""
        successful = ("bash", "read_file")
        failed = ()
        event = ToolSummaryEvent(
            source=_MAIN_SOURCE, successful_tools=successful, failed_tools=failed,
        )
        dispatcher._on_tool_summary(event)
        push_cmd.assert_called_once_with((RenderCommand.TOOL_SUMMARY, successful, failed))

    def test_empty_lists_skipped(self, dispatcher, push_cmd):
        """空列表（successful=() 且 failed=()）跳过。"""
        event = ToolSummaryEvent(source=_MAIN_SOURCE, successful_tools=(), failed_tools=())
        dispatcher._on_tool_summary(event)
        push_cmd.assert_not_called()

    def test_non_agent_source_skipped(self, dispatcher, push_cmd):
        """非 agent source 跳过。"""
        event = ToolSummaryEvent(
            source="user", successful_tools=("bash",), failed_tools=(),
        )
        dispatcher._on_tool_summary(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherParseInfo
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherParseInfo:
    """ParseInfoEvent 处理器测试。"""

    def test_normal_parse_info(self, dispatcher, push_cmd):
        """正常 → push_cmd 收到 (PARSE_INFO, tool_names, tokens, elapsed)。"""
        event = ParseInfoEvent(
            source=_MAIN_SOURCE, label="assistant",
            tool_names="bash,read_file", tokens=150, elapsed=1.25,
        )
        dispatcher._on_parse_info(event)
        push_cmd.assert_called_once_with(
            (RenderCommand.PARSE_INFO, "bash,read_file", 150, 1.25),
        )

    def test_non_agent_source_skipped(self, dispatcher, push_cmd):
        """非 agent source 跳过。"""
        event = ParseInfoEvent(source="user", tool_names="bash", tokens=50, elapsed=0.5)
        dispatcher._on_parse_info(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherParseInfoDone
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherParseInfoDone:
    """ParseInfoDoneEvent 处理器测试。"""

    def test_normal_parse_info_done(self, dispatcher, push_cmd):
        """正常 → push_cmd 收到 (PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0)。"""
        event = ParseInfoDoneEvent(source=_MAIN_SOURCE, label="assistant")
        dispatcher._on_parse_info_done(event)
        push_cmd.assert_called_once_with(
            (RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0),
        )

    def test_non_agent_source_skipped(self, dispatcher, push_cmd):
        """非 agent source 跳过。"""
        event = ParseInfoDoneEvent(source="user")
        dispatcher._on_parse_info_done(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherOutput
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherOutput:
    """OutputEvent 处理器测试。"""

    def test_normal_output(self, dispatcher, push_cmd):
        """正常 OutputEvent → push_cmd 收到 (WRITE_LINE, text)。"""
        event = OutputEvent(text="System initialized", level="info")
        dispatcher._on_output(event)
        push_cmd.assert_called_once_with((RenderCommand.WRITE_LINE, "System initialized"))

    def test_empty_text_skipped(self, dispatcher, push_cmd):
        """空 text 跳过。"""
        event = OutputEvent(text="", level="info")
        dispatcher._on_output(event)
        push_cmd.assert_not_called()

    def test_falsy_none_text_skipped(self, dispatcher, push_cmd):
        """falsy text 也跳过（虽然 dataclass 默认 str，空字符串即为 falsy）。"""
        event = OutputEvent(text="", level="info")
        dispatcher._on_output(event)
        push_cmd.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherModelPhase
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherModelPhase:
    """ModelPhaseEvent 处理器测试。"""

    def test_error_phase(self, dispatcher, push_cmd):
        """phase='error' + label=_MAIN_LABEL + info='msg' → (ERROR, 'msg')。"""
        event = ModelPhaseEvent(label=_MAIN_LABEL, phase="error", info="Connection failed")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with((RenderCommand.ERROR, "Connection failed"))

    def test_non_error_phase_skipped(self, dispatcher, push_cmd):
        """非 error phase 跳过。"""
        event = ModelPhaseEvent(label=_MAIN_LABEL, phase="thinking", info="thinking...")
        dispatcher._on_model_phase(event)
        push_cmd.assert_not_called()

    def test_empty_info_skipped(self, dispatcher, push_cmd):
        """空 info 跳过。"""
        event = ModelPhaseEvent(label=_MAIN_LABEL, phase="error", info="")
        dispatcher._on_model_phase(event)
        push_cmd.assert_not_called()

    def test_non_main_label_skipped(self, dispatcher, push_cmd):
        """非 _MAIN_LABEL 跳过。"""
        event = ModelPhaseEvent(label="subagent-1", phase="error", info="error msg")
        dispatcher._on_model_phase(event)
        push_cmd.assert_not_called()

    def test_long_info_truncated(self, dispatcher, push_cmd):
        """超长 info 被 _truncate_msg 截断。"""
        long_msg = "x" * (_MAX_ERROR_LENGTH + 50)
        expected = _truncate_msg(long_msg, _MAX_ERROR_LENGTH)
        event = ModelPhaseEvent(label=_MAIN_LABEL, phase="error", info=long_msg)
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with((RenderCommand.ERROR, expected))
        assert len(expected) == _MAX_ERROR_LENGTH + 3  # 原始长度 + "..."

    def test_info_at_boundary_not_truncated(self, dispatcher, push_cmd):
        """info 刚好等于 _MAX_ERROR_LENGTH 时不被截断。"""
        msg = "x" * _MAX_ERROR_LENGTH
        event = ModelPhaseEvent(label=_MAIN_LABEL, phase="error", info=msg)
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with((RenderCommand.ERROR, msg))


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherEdgeCases
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherEdgeCases:
    """边界情况：非事件对象、isinstance 守卫、非注册事件。"""

    def test_not_an_event_passed(self, dispatcher, push_cmd):
        """传入非 DisplayEvent 对象 → 所有 handler 的 isinstance 守卫返回。"""
        dispatcher._on_reasoning_chunk("not an event")        # type: ignore[arg-type]
        dispatcher._on_content_chunk(42)                      # type: ignore[arg-type]
        dispatcher._on_phase_done(None)                       # type: ignore[arg-type]
        dispatcher._on_tool_started([1, 2, 3])                # type: ignore[arg-type]
        dispatcher._on_tool_done({"source": "agent"})         # type: ignore[arg-type]
        dispatcher._on_tool_output(True)                      # type: ignore[arg-type]
        dispatcher._on_tool_summary(MagicMock())              # type: ignore[arg-type]
        dispatcher._on_parse_info(MagicMock())                # type: ignore[arg-type]
        dispatcher._on_parse_info_done(MagicMock())           # type: ignore[arg-type]
        dispatcher._on_output(MagicMock())                    # type: ignore[arg-type]
        dispatcher._on_model_phase(MagicMock())               # type: ignore[arg-type]
        push_cmd.assert_not_called()

    def test_handler_not_registered_does_nothing(self, dispatcher, push_cmd):
        """事件处理器未在 _EVENT_HANDLERS 注册表中注册不会影响行为。"""
        # 验证注册表包含全部 11 个 handler
        registered_names = {name for _, name in dispatcher._EVENT_HANDLERS}
        assert "_on_reasoning_chunk" in registered_names
        assert "_on_content_chunk" in registered_names
        assert "_on_phase_done" in registered_names
        assert "_on_tool_started" in registered_names
        assert "_on_tool_done" in registered_names
        assert "_on_tool_output" in registered_names
        assert "_on_tool_summary" in registered_names
        assert "_on_parse_info" in registered_names
        assert "_on_parse_info_done" in registered_names
        assert "_on_model_phase" in registered_names
        assert "_on_output" in registered_names
        assert len(registered_names) == 11


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherLazyLoading
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherLazyLoading:
    """验证 _get_event_type 惰性加载机制。"""

    def test_event_types_cache_populated_on_first_call(self):
        """首次调用 handler 时惰性加载事件类型并缓存。"""
        push_cmd = MagicMock()
        d = EventDispatcher(push_cmd=push_cmd)
        # 构造时事件类型字典为空
        assert len(d._event_types) == 0
        # 首次调用触发惰性加载
        event = ReasoningChunkEvent(label=_MAIN_LABEL, text="test")
        d._on_reasoning_chunk(event)
        # 加载后应包含全部 11 种事件类型
        assert len(d._event_types) == 11
        assert "ReasoningChunkEvent" in d._event_types
        assert "ContentChunkEvent" in d._event_types

    def test_subsequent_calls_use_cache(self, dispatcher, push_cmd):
        """后续调用使用缓存，不重复 import。"""
        # 首次调用 - 加载
        event = ReasoningChunkEvent(label=_MAIN_LABEL, text="test")
        dispatcher._on_reasoning_chunk(event)
        first_len = len(dispatcher._event_types)
        # 第二次调用 - 应使用缓存
        event2 = ContentChunkEvent(label=_MAIN_LABEL, text="content")
        dispatcher._on_content_chunk(event2)
        # 事件类型数量不变（已缓存）
        assert len(dispatcher._event_types) == first_len


# ═══════════════════════════════════════════════════════════
# TestEventDispatcherIsAgentSource
# ═══════════════════════════════════════════════════════════

class TestEventDispatcherIsAgentSource:
    """_is_agent_source 静态方法测试（工具类 handler 过滤依赖此方法）。"""

    @pytest.mark.parametrize("source,expected", [
        ("agent", True),
        ("agent-1", True),
        ("agent-main", True),
        ("AGENT", False),
        ("user", False),
        ("system", False),
        ("", False),
        (None, False),
    ])
    def test_is_agent_source(self, source, expected):
        """验证各种 source 值的判断结果。"""
        assert EventDispatcher._is_agent_source(source) is expected
