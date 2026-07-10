"""
SubAgentSpawner — SubAgent 创建、终端显示渲染、结果事件发布

从 ParallelExecutor 提取，封装三个职责：
1. spawn() — 从 spec 创建 SubAgent 实例并注册到 display
2. render_display() — 打印任务摘要（终端模式）
3. publish_summary() — 批量发布 AgentResultEvent
"""

import logging
from typing import List, Dict, Any

from ..subagent import SubAgent
from ...ui.events import DisplayEventBus
from ...ui.events.event_types import AgentResultEvent
from ...ui.parallel._tool_icons import AGENT_TYPE_ABBREV
from ..constants import RED, RESET

_logger = logging.getLogger(__name__)

# ── 结果字典键常量 ─────────────────────────────────
_DESCRIPTION_KEY = "description"
_ERROR_KEY = "error"
_LABEL_KEY = "label"
_RESULT_KEY = "result"


class SubAgentSpawner:
    """创建 SubAgent 实例、渲染终端显示、发布结果事件"""

    def __init__(self, parent_agent, agent_factory, is_web: bool = False):
        self.parent = parent_agent
        self._agent_factory = agent_factory
        self._is_web = is_web

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
        """打印任务摘要到终端（仅非 Web 模式）。"""
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
        )
        sa.display = display
        display.add_agent(label, desc, status="running", agent_type=agent_type)
        return sa

    def _render_subagent_display(self, specs: List[Dict[str, Any]]) -> None:
        """在执行前打印任务摘要 — 使用 IncrementalRenderer 渲染 markdown。

        路由策略（与 parallel_executor._stream_results_markdown 修复一致）：
        1. ChatUIConsumer 激活 → StringIO 捕获 ANSI → chat_ui.write_line()
           （尊重 DECSTBM 分屏布局，prompt markdown 正确显示在屏幕上）
        2. ChatUIConsumer 未激活 → IncrementalRenderer 直接写 sys.__stdout__
           （适用于非分屏模式，原逻辑不变）
        """
        from src.chat_ui import get_active_chat_ui

        # ── 构造完整的 markdown 文本 ──────────────────────────────────
        md_parts: list[str] = []
        for i, spec in enumerate(specs, 1):
            desc = spec.get(_DESCRIPTION_KEY, f"子任务 {i}")
            agent_type = spec.get("agent_type", "execute")
            abbr = AGENT_TYPE_ABBREV.get(agent_type, "??")
            prompt = spec.get("prompt", "")
            if prompt:
                md_parts.append(f"### {i}. {RED}[{abbr}]{RESET} {desc}")
                md_parts.append(prompt)

        md_text = "\n".join(md_parts)
        if not md_text.strip():
            return

        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            # ChatUI 激活 → StringIO 捕获 ANSI → chat_ui.write_line() 统一上屏
            # （与 parallel_executor._stream_results_via_chatui 模式一致）
            import io
            import os
            from src.renderer import IncrementalRenderer

            try:
                term_width = os.get_terminal_size().columns
            except (OSError, PermissionError):
                term_width = 80
            buf = io.StringIO()
            renderer = IncrementalRenderer(
                typing_speed=0, show_indicator=False, _file=buf, width=term_width,
            )
            try:
                renderer.write(md_text)
            finally:
                renderer.close()

            output = buf.getvalue()
            if output:
                chat_ui.write_line("")  # 开头空行
                for line in output.rstrip("\n").split("\n"):
                    chat_ui.write_line(line)
                chat_ui.write_line("")  # 结尾空行
        else:
            # ChatUI 未激活 → 直接写 __stdout__（原逻辑）
            import sys as _sys
            from src.renderer import IncrementalRenderer

            renderer = IncrementalRenderer(
                typing_speed=0, show_indicator=False, _file=_sys.__stdout__,
            )
            try:
                for i, spec in enumerate(specs, 1):
                    desc = spec.get(_DESCRIPTION_KEY, f"子任务 {i}")
                    agent_type = spec.get("agent_type", "execute")
                    abbr = AGENT_TYPE_ABBREV.get(agent_type, "??")
                    prompt = spec.get("prompt", "")
                    if prompt:
                        renderer.write(f"### {i}. {RED}[{abbr}]{RESET} {desc}")
                        renderer.write(prompt)
            finally:
                renderer.close()

    def _publish_tool_summary(self, results: List[Dict[str, Any]]) -> None:
        """批量发布所有 subagent 的 AgentResultEvent（全部完成后统一发送）。"""
        for r in results:
            DisplayEventBus.get_default().publish(AgentResultEvent(
                label=r.get(_LABEL_KEY, "?"),
                description=r.get(_DESCRIPTION_KEY, "?"),
                result=r.get(_RESULT_KEY, ""),
                error=r.get(_ERROR_KEY, ""),
                source="parallel",
            ))
