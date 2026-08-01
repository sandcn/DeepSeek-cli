"""测试 subagent 提词改为事件投递：render_display 逐 spec 发布 SubagentPromptEvent。

覆盖：
  - 每个 spec 发布一个 SubagentPromptEvent
  - label/index/agent_type/prompt 字段正确（index 为 1 基序号）
  - 空 prompt 的 spec 不发布事件
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.internal.agent._subagent_spawner import SubAgentSpawner
from src.tui.events.event_types import SubagentPromptEvent


class TestSubagentPromptEventPublish:
    """render_display → SubagentPromptEvent 事件发布。"""

    def test_render_display_publishes_prompt_events(self) -> None:
        """逐 spec 发布，label/index/agent_type/prompt 正确。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), event_port=mock_port)
        specs = [
            {"description": "t1", "prompt": "p1", "agent_type": "execute"},
            {"description": "t2", "prompt": "p2", "agent_type": "plan"},
        ]

        spawner.render_display(specs)

        assert mock_port.publish_event.call_count == 2
        events = [c[0][0] for c in mock_port.publish_event.call_args_list]
        assert all(isinstance(e, SubagentPromptEvent) for e in events)

        ev1, ev2 = events
        assert ev1.label == "agent-1"
        assert ev1.index == 1
        assert ev1.agent_type == "execute"
        assert ev1.prompt == "p1"
        assert ev2.label == "agent-2"
        assert ev2.index == 2
        assert ev2.agent_type == "plan"
        assert ev2.prompt == "p2"

    def test_empty_prompt_skips_event(self) -> None:
        """prompt 为空的 spec 不发布事件。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), event_port=mock_port)
        specs = [
            {"description": "t1", "prompt": "", "agent_type": "execute"},
            {"description": "t2", "prompt": "p2", "agent_type": "execute"},
        ]

        spawner.render_display(specs)

        assert mock_port.publish_event.call_count == 1
        ev = mock_port.publish_event.call_args[0][0]
        assert ev.index == 2  # 仅非空 prompt 的 spec 发布

    def test_web_mode_skips_publish(self) -> None:
        """Web 模式（_is_web=True）不发布提词事件（由 webui 前端自处理）。"""
        mock_port = MagicMock()
        spawner = SubAgentSpawner(MagicMock(), MagicMock(), is_web=True,
                                  event_port=mock_port)
        specs = [{"description": "t1", "prompt": "p1", "agent_type": "execute"}]

        spawner.render_display(specs)

        mock_port.publish_event.assert_not_called()
