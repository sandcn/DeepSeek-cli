"""测试渲染器核心 — EventDispatcher 事件→命令映射 + 内容命令单一真源。

2026-08-01 ink 重构迁移说明：
  - TuiEngine 队列/优先级/批处理/崩溃恢复 → tests/test_tui/ink/test_session.py
  - TuiRenderer 命令分发 → tests/test_tui/test_apply.py（AppModel 迁移）
  - ChatRenderState captured 机制 → tests/test_tui/test_render_state.py
  - EventDispatcher 保留（src/tui/_dispatcher.py，本文件）
"""

from __future__ import annotations

from unittest.mock import MagicMock


class TestEventDispatcher:
    """测试 EventDispatcher 事件→命令映射。"""

    def test_list_handlers(self):
        from src.tui._dispatcher import EventDispatcher
        dispatcher = EventDispatcher(MagicMock())
        handlers = dispatcher.list_handlers()
        assert len(handlers) == 15

    def test_on_reasoning_chunk(self):
        """main label 的 reasoning chunk → push ReasoningCmd（加固：非仅不抛异常）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui._const import ReasoningCmd
        from src.tui.events.event_types import ReasoningChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd, main_label="main")
        event = ReasoningChunkEvent(text="hello", label="main")
        dispatcher._on_reasoning_chunk(event)
        push_cmd.assert_called_once_with(ReasoningCmd(text="hello"))

    def test_on_reasoning_chunk_filters_non_main_label(self):
        """非 main label 的 reasoning chunk 不入队（label 过滤）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ReasoningChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd, main_label="main")
        event = ReasoningChunkEvent(text="hello", label="agent-1")
        dispatcher._on_reasoning_chunk(event)
        push_cmd.assert_not_called()

    def test_on_content_chunk(self):
        """main label 的 content chunk → push ContentCmd（加固：非仅不抛异常）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui._const import ContentCmd
        from src.tui.events.event_types import ContentChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd, main_label="main")
        event = ContentChunkEvent(text="world", label="main")
        dispatcher._on_content_chunk(event)
        push_cmd.assert_called_once_with(ContentCmd(text="world"))

    def test_on_content_chunk_filters_non_main_label(self):
        """非 main label 的 content chunk 不入队（label 过滤）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ContentChunkEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd, main_label="main")
        event = ContentChunkEvent(text="world", label="agent-1")
        dispatcher._on_content_chunk(event)
        push_cmd.assert_not_called()

    def test_on_tool_started(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolStartedEvent
        from src.tui._const import ToolCountIncCmd, ToolOpenCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolStartedEvent(source="agent", tool_name="read_file")
        dispatcher._on_tool_started(event)
        # 打开 box + 计数 +1
        assert push_cmd.call_count == 2
        push_cmd.assert_any_call(ToolOpenCmd(tool_name="read_file", tool_id="", detail=""))
        push_cmd.assert_any_call(ToolCountIncCmd())

    def test_on_tool_started_dispatch_agent_no_toolcard(self):
        """调用 subagent（dispatch_agent/Task）不建普通工具卡，仅计数。

        回归：dispatch_agent 的 tool_start（source='agent'）此前创建
        ``⚙ Task`` 普通工具卡，与 SubAgent 面板卡（┌─ ● ⚡ 子代理 ─┐）
        重复显示。修复后只计数、不上屏 box。
        """
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolStartedEvent
        from src.tui._const import ToolCountIncCmd, ToolOpenCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolStartedEvent(
            source="agent", tool_name="dispatch_agent", tool_id="call_x",
        )
        dispatcher._on_tool_started(event)
        # 仅计数 +1，无 ToolOpenCmd
        push_cmd.assert_called_once_with(ToolCountIncCmd())
        for call in push_cmd.call_args_list:
            assert not isinstance(call.args[0], ToolOpenCmd)

    def test_on_tool_done_success(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolCountDecCmd, ToolCloseCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", success=True)
        dispatcher._on_tool_done(event)
        # 关闭 box + 计数 -1
        assert push_cmd.call_count == 2
        push_cmd.assert_any_call(ToolCloseCmd(tool_id="", success=True))
        push_cmd.assert_any_call(ToolCountDecCmd())

    def test_on_tool_done_fail(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolFailIncCmd, ToolCountDecCmd, ToolCloseCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", success=False)
        dispatcher._on_tool_done(event)
        assert push_cmd.call_count == 3
        push_cmd.assert_any_call(ToolCloseCmd(tool_id="", success=False))
        push_cmd.assert_any_call(ToolFailIncCmd())
        push_cmd.assert_any_call(ToolCountDecCmd())

    def test_on_tool_done_dispatch_agent_no_close(self):
        """调用 subagent（dispatch_agent）done 不推 ToolCloseCmd（无卡可关）。

        回归：dispatch_agent 未开工具卡，若仍推 ToolCloseCmd 会经
        close_tool_box 找不到 box 而 debug 丢弃（无实际影响但属冗余路径）。
        """
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolCloseCmd, ToolCountDecCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", tool_name="dispatch_agent", success=True)
        dispatcher._on_tool_done(event)
        # 仅计数 -1，无 ToolCloseCmd
        push_cmd.assert_called_once_with(ToolCountDecCmd())
        for call in push_cmd.call_args_list:
            assert not isinstance(call.args[0], ToolCloseCmd)

    def test_on_tool_done_dispatch_agent_fail_still_counts(self):
        """dispatch_agent 失败仍计数（失败递增 + 计数递减），只是不关卡。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ToolDoneEvent
        from src.tui._const import ToolFailIncCmd, ToolCountDecCmd, ToolCloseCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ToolDoneEvent(source="agent", tool_name="dispatch_agent", success=False)
        dispatcher._on_tool_done(event)
        assert push_cmd.call_count == 2
        push_cmd.assert_any_call(ToolFailIncCmd())
        push_cmd.assert_any_call(ToolCountDecCmd())
        for call in push_cmd.call_args_list:
            assert not isinstance(call.args[0], ToolCloseCmd)

    def test_on_parse_info(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ParseInfoEvent
        from src.tui._const import ParseInfoCmd
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = ParseInfoEvent(source="agent", tool_names="test", tokens=100, elapsed=0.5)
        dispatcher._on_parse_info(event)
        push_cmd.assert_called_once_with(ParseInfoCmd(tool_names="test", tokens=100, elapsed=0.5))

    def test_on_subagent_prompt(self):
        """SubagentPromptEvent → SubagentMarkdownCmd（含标题+提词）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui._const import SubagentMarkdownCmd
        from src.tui.events.event_types import SubagentPromptEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = SubagentPromptEvent(
            label="agent-1", description="t1", prompt="do the thing",
            agent_type="execute", index=1,
        )
        dispatcher._on_subagent_prompt(event)
        push_cmd.assert_called_once_with(
            SubagentMarkdownCmd(text="### 1. [ex] t1\ndo the thing")
        )

    def test_on_subagent_prompt_empty_prompt_skipped(self):
        """空 prompt 不入队。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import SubagentPromptEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = SubagentPromptEvent(description="t1", prompt="")
        dispatcher._on_subagent_prompt(event)
        push_cmd.assert_not_called()

    def test_on_agent_result(self):
        """AgentResultEvent → SubagentMarkdownCmd（含标题+结果）。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui._const import SubagentMarkdownCmd
        from src.tui.events.event_types import AgentResultEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = AgentResultEvent(
            label="agent-1", description="t1", result="done ok",
            agent_type="execute", index=1,
        )
        dispatcher._on_agent_result(event)
        push_cmd.assert_called_once_with(
            SubagentMarkdownCmd(text="### 1. [ex] t1\ndone ok")
        )

    def test_on_agent_result_error(self):
        """错误场景 → 含错误引用块。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui._const import SubagentMarkdownCmd
        from src.tui.events.event_types import AgentResultEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = AgentResultEvent(
            label="agent-1", description="t1", error="boom", index=1,
        )
        dispatcher._on_agent_result(event)
        cmd = push_cmd.call_args[0][0]
        assert isinstance(cmd, SubagentMarkdownCmd)
        assert "> 错误: boom" in cmd.text

    def test_on_agent_result_empty_skipped(self):
        """result/error 均空不入队。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import AgentResultEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        event = AgentResultEvent(label="agent-1", description="t1")
        dispatcher._on_agent_result(event)
        push_cmd.assert_not_called()

    def test_register_handler(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import OutputEvent
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)
        custom = MagicMock()
        dispatcher.register_handler(OutputEvent, custom)
        handlers = dispatcher.list_handlers()
        assert OutputEvent in handlers
        assert handlers[OutputEvent] is custom

    def test_list_handlers_cache_regression(self):
        """list_handlers() 结果缓存，register_handler 后失效重建。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import OutputEvent, SessionStarted
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)

        h1 = dispatcher.list_handlers()
        h2 = dispatcher.list_handlers()
        assert h1 is h2, "list_handlers() 应返回同一缓存对象"
        assert len(h1) == 15

        custom = MagicMock()
        dispatcher.register_handler(SessionStarted, custom)
        h3 = dispatcher.list_handlers()
        assert h3 is not h2, "register_handler 后应重新构建缓存"
        assert SessionStarted in h3
        assert h3[SessionStarted] is custom
        assert len(h3) == 16

    def test_register_group_regression(self):
        """register_group 注册声明式订阅组并合并进 list_handlers。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import SessionStarted, SessionStopped
        push_cmd = MagicMock()
        dispatcher = EventDispatcher(push_cmd)

        group_handler = MagicMock()
        dispatcher.register_group(
            "test_group",
            {SessionStarted: group_handler},
        )
        handlers = dispatcher.list_handlers()
        assert SessionStarted in handlers
        assert handlers[SessionStarted] is group_handler
        assert len(handlers) == 16

    def test_on_model_phase_thinking(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import MainPhaseCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, main_label=cfg.main_label)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="thinking", info="")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once_with(MainPhaseCmd(phase="thinking"))

    def test_on_model_phase_error(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand, ErrorCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(push_cmd, main_label=cfg.main_label)
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info="something went wrong")
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert call_args.cid == RenderCommand.ERROR

    def test_on_model_phase_error_truncates_to_max_length(self):
        """_on_model_phase error 时消息截断到 max_error_length。"""
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import ModelPhaseEvent
        from src.tui._const import RenderCommand, ErrorCmd
        push_cmd = MagicMock()
        from src.tui.consumer.chat_config import ChatConfig
        cfg = ChatConfig.defaults()
        dispatcher = EventDispatcher(
            push_cmd, main_label=cfg.main_label, max_error_length=50,
        )
        long_info = "E" * 100
        event = ModelPhaseEvent(label=cfg.main_label or "main", phase="error", info=long_info)
        dispatcher._on_model_phase(event)
        push_cmd.assert_called_once()
        call_args = push_cmd.call_args[0][0]
        assert isinstance(call_args, ErrorCmd)
        assert call_args.cid == RenderCommand.ERROR
        assert len(call_args.message) == 50


class TestContentCommandsSingleSource:
    """验证 _CONTENT_COMMANDS 收敛至 _const.CONTENT_COMMANDS 单一真源。"""

    def test_content_commands_set_contents(self):
        from src.tui._const import CONTENT_COMMANDS, RenderCommand
        assert RenderCommand.REASONING in CONTENT_COMMANDS
        assert RenderCommand.SPLASH in CONTENT_COMMANDS
        assert RenderCommand.TOOL_COUNT_INC not in CONTENT_COMMANDS
        assert RenderCommand.SUBAGENT_FRAME not in CONTENT_COMMANDS

    def test_session_module_alias_matches_source(self):
        from src.tui.ink.session import _CONTENT_COMMANDS as session_cmds
        from src.tui._const import CONTENT_COMMANDS
        assert session_cmds is CONTENT_COMMANDS
