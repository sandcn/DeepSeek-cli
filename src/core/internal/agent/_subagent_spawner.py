"""
SubAgentSpawner — SubAgent 创建、提词事件发布、结果事件发布

从 ParallelExecutor 提取，封装三个职责：
1. spawn() — 从 spec 创建 SubAgent 实例并注册到 display
2. render_display() — 逐 spec 发布 SubagentPromptEvent（事件投递到 TUI 消息区）
3. publish_summary() — 批量发布 AgentResultEvent
"""

import logging
from typing import List, Dict, Any

from ...subagent import SubAgent

_logger = logging.getLogger(__name__)

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"
_ERROR_KEY = "error"
_LABEL_KEY = "label"
_RESULT_KEY = "result"
_AGENT_TYPE_KEY = "agent_type"


class SubAgentSpawner:
    """创建 SubAgent 实例、发布提词/结果事件"""

    def __init__(self, parent_agent, agent_factory, is_web: bool = False,
                 event_port=None):
        self.parent = parent_agent
        self._agent_factory = agent_factory
        self._is_web = is_web
        if event_port is not None:
            self._event_port = event_port
        else:
            from ...adapters.events import DisplayEventBusAdapter
            self._event_port = DisplayEventBusAdapter.get_default()

    # -- 公开 API --

    def spawn(self, spec: Dict[str, Any], index: int, display) -> SubAgent:
        """从 spec 创建一个 SubAgent 并注册到 display。

        Args:
            spec: agent 配置 dict（description/prompt/model）
            index: agent 序号（用于生成 label "agent-N"）
            display: ParallelDisplay 实例

        Returns:
            已配置的 SubAgent 实例
        """
        return self._spawn_subagent(spec, index, display)

    def render_display(self, specs: List[Dict[str, Any]]) -> None:
        """逐 spec 发布 SubagentPromptEvent（仅非 Web 模式，投递到 TUI 消息区）。

        事件经 EventBus → EventDispatcher → SubagentMarkdownCmd 在消息区
        渲染为独立 markdown 块；无头（无 ChatUI）模式下无消费者，不再输出。
        """
        if not self._is_web and specs:
            self._render_subagent_display(specs)

    def publish_summary(self, results: List[Dict[str, Any]]) -> None:
        """批量发布所有 subagent 的 AgentResultEvent。"""
        if results:
            self._publish_tool_summary(results)

    # -- 内部方法 --

    def _spawn_subagent(self, spec: Dict[str, Any], index: int, display) -> SubAgent:
        """创建单个 SubAgent 实例：构造标签、调用工厂、绑定 display。"""
        label = f"agent-{index + 1}"
        desc = spec.get(_DESCRIPTION_KEY, f"子任务 {index + 1}")
        agent_type = spec.get("agent_type", "execute")
        sa = self._agent_factory(
            label=label,
            description=desc,
            prompt=spec["prompt"],
            parent_agent=self.parent,
            model=spec.get("model"),
            agent_type=agent_type,
            # ★ 2026-08-17（用户需求：agent 内容合并到 dispatch_agent）：
            #   所属 dispatch_agent 的 tool_call_id（spec["tool_label"]，add_agent
            #   传入）——随 _record_to_parent 写入会话存档，load 恢复后主轨迹
            #   仍可合并；独立模式（run）spec 无 tool_label → 空串（独立记录）。
            dispatch_label=spec.get("tool_label", ""),
        )
        sa.display = display
        display.add_agent(label, desc, status="running", agent_type=agent_type)
        return sa

    def _render_subagent_display(self, specs: List[Dict[str, Any]]) -> None:
        """逐 spec 发布 SubagentPromptEvent（事件投递，替代 core 直接渲染）。

        删除原 IncrementalRenderer/get_display_target/get_terminal_width/
        sys.__stdout__ 渲染路径；渲染统一由 TUI 侧消费事件完成
        （EventDispatcher → SubagentMarkdownCmd → apply_cmd → 消息区块）。
        """
        from ....tui.events.event_types import SubagentPromptEvent as _SubagentPromptEvent
        for i, spec in enumerate(specs, 1):
            desc = spec.get(_DESCRIPTION_KEY, f"子任务 {i}")
            agent_type = spec.get("agent_type", "execute")
            prompt = spec.get("prompt", "")
            if prompt:
                self._event_port.publish_event(_SubagentPromptEvent(
                    label=f"agent-{i}",
                    description=desc,
                    prompt=prompt,
                    agent_type=agent_type,
                    index=i,
                    source="parallel",
                ))

    def _publish_tool_summary(self, results: List[Dict[str, Any]]) -> None:
        """批量发布所有 subagent 的 AgentResultEvent（全部完成后统一发送）。"""
        from ....tui.events.event_types import AgentResultEvent as _AgentResultEvent
        for i, r in enumerate(results, 1):
            self._event_port.publish_event(_AgentResultEvent(
                label=r.get(_LABEL_KEY, "?"),
                description=r.get(_DESCRIPTION_KEY, "?"),
                result=r.get(_RESULT_KEY, ""),
                error=r.get(_ERROR_KEY, ""),
                agent_type=r.get(_AGENT_TYPE_KEY, "execute"),
                index=i,
                source="parallel",
            ))
