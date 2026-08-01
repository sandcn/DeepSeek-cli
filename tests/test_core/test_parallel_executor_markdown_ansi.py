"""测试 BUG-A4 修复：提词/返回 markdown 事件文本不含 ANSI 转义。

subagent 提词/返回改为事件投递到 TUI 消息区后，core 层只发布纯文本事件，
不直接渲染。本文件验证发布事件中的 markdown 文本不含 \\x1b：
  - SubAgentSpawner.render_display 发布 SubagentPromptEvent.prompt 无 ANSI
  - SubAgentSpawner.publish_summary 发布 AgentResultEvent.result 无 ANSI
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.internal.agent._subagent_spawner import SubAgentSpawner
from src.tui.events.event_types import AgentResultEvent, SubagentPromptEvent


class TestSubAgentSpawnerMarkdownNoAnsi:
    """BUG-A4：subagent 提词/返回事件文本不含 \\x1b。"""

    def test_render_display_publishes_prompt_no_ansi(self) -> None:
        """render_display 发布 SubagentPromptEvent，prompt 纯文本无 ANSI。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), event_port=mock_port)
        specs = [{"description": "t1", "prompt": "p1", "agent_type": "execute"}]

        spawner.render_display(specs)

        assert mock_port.publish_event.call_count == 1
        ev = mock_port.publish_event.call_args[0][0]
        assert isinstance(ev, SubagentPromptEvent)
        assert "\x1b" not in ev.prompt
        assert ev.prompt == "p1"
        assert ev.label == "agent-1"
        assert ev.index == 1
        assert ev.agent_type == "execute"

    def test_publish_summary_publishes_result_no_ansi(self) -> None:
        """publish_summary 发布 AgentResultEvent，result 纯文本无 ANSI。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), event_port=mock_port)
        results = [
            {"label": "agent-1", "description": "t1", "result": "ok", "error": ""},
        ]

        spawner.publish_summary(results)

        assert mock_port.publish_event.call_count == 1
        ev = mock_port.publish_event.call_args[0][0]
        assert isinstance(ev, AgentResultEvent)
        assert "\x1b" not in ev.result
        assert ev.result == "ok"
        assert ev.index == 1
        assert ev.agent_type == "execute"

    def test_publish_summary_error_no_ansi(self) -> None:
        """error 场景同样无 ANSI。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), event_port=mock_port)
        results = [
            {"label": "agent-1", "description": "t1", "result": "", "error": "boom"},
        ]

        spawner.publish_summary(results)

        ev = mock_port.publish_event.call_args[0][0]
        assert isinstance(ev, AgentResultEvent)
        assert "\x1b" not in ev.error
        assert ev.error == "boom"
