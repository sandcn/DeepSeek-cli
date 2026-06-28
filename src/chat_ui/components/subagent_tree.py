"""SubagentTree — SubAgent 数据 → TreeNode 树适配器。

提供 subagent_slots_to_tree 函数，将 TuiState.subagent_slots 格式的
subagent 槽位数据转换为 Tree 组件可渲染的 TreeNode 树结构。
"""

from __future__ import annotations

import time

from .tree import TreeNode
from ..bottom_bar._theme import _SUBAGENT_TYPE_ABBR


# Tool phase → TreeNode status 映射
_TOOL_PHASE_STATUS: dict[str, str] = {
    "parsing": "running",
    "running": "running",
    "done": "done",
    "fail": "fail",
}


def subagent_slots_to_tree(slots: dict | None) -> TreeNode | None:
    """将 TuiState.subagent_slots 转换为 TreeNode 树。

    每个 agent 为顶层节点，其 tool_history 为子节点。
    节点 label 格式: `[{agent_type缩写}] {description}`
    子节点 label 格式: `{tool_name} {status_icon}`

    Args:
        slots: TuiState.subagent_slots 格式的 dict，
               key 为 label（如 "agent-1"），value 为 slot dict。
               slot dict 结构参考 ParallelDisplay._slots：
               {
                   "description": str,
                   "agent_type": str,
                   "status": str,        # "running" / "done" / "fail"
                   "output_tokens": int,
                   "start_time": float,
                   "end_time": float,
                   "model_phase": str,
                   "tool_history": [{"tool_name": str, "phase": str}, ...],
               }

    Returns:
        TreeNode 根节点（label 为空，作为容器），或 None 当 slots 为空时。

    示例:
        >>> slots = {
        ...     "agent-1": {
        ...         "description": "分析代码",
        ...         "agent_type": "map",
        ...         "status": "running",
        ...         "tool_history": [
        ...             {"tool_name": "read_file", "phase": "done"},
        ...             {"tool_name": "search", "phase": "running"},
        ...         ],
        ...     },
        ... }
        >>> root = subagent_slots_to_tree(slots)
        >>> root.label
        ''
        >>> len(root.children)
        1
    """
    if not slots:
        return None

    children: list[TreeNode] = []

    for label, slot in slots.items():
        if not isinstance(slot, dict) or not slot:
            continue

        # ── 提取字段 ──
        desc = slot.get("description", label)
        agent_type = slot.get("agent_type", "")
        status = slot.get("status", "running")
        start_time = slot.get("start_time", 0.0)
        end_time = slot.get("end_time", 0.0)
        output_tokens = slot.get("output_tokens", 0) + slot.get("live_output_tokens", 0)
        model_phase = slot.get("model_phase", "")
        tool_history = slot.get("tool_history", [])

        # ── 映射 status ──
        if status not in ("running", "done", "fail"):
            if status in ("completed",):
                status = "done"
            else:
                status = "running"

        # ── 构建 agent 节点 ──
        abbr = _SUBAGENT_TYPE_ABBR.get(agent_type, agent_type[:4]) if agent_type else ""
        tag = f"[{abbr}]" if abbr else ""

        # 计算 elapsed
        elapsed = 0.0
        now = time.time()
        if status == "running" and start_time > 0:
            elapsed = now - start_time
        elif end_time > 0 and start_time > 0:
            elapsed = end_time - start_time

        elapsed_str = _format_elapsed(elapsed)
        token_str = f"{output_tokens}t" if output_tokens > 0 else ""
        phase_str = f" [{model_phase}]" if model_phase else ""

        # 组装 label
        parts = [tag, desc]
        if token_str:
            extras_parts = [token_str]
            if elapsed > 0 and elapsed_str:
                extras_parts.append(elapsed_str)
            if extras_parts:
                parts.append("(" + "  ".join(extras_parts) + ")")
        elif elapsed > 0 and elapsed_str:
            parts.append(f"({elapsed_str})")
        if phase_str:
            parts.append(phase_str)
        label_text = " ".join(parts)

        # ── 构建 tool_history 子节点（只保留最近 3 条）──
        tool_children: list[TreeNode] = []
        if isinstance(tool_history, list):
            recent_tools = tool_history[-3:]  # 只显示最近 3 条工具历史
            for tool in recent_tools:
                if not isinstance(tool, dict):
                    continue
                tool_name = tool.get("tool_name", "?")
                t_detail = tool.get("detail", "")
                tool_phase = tool.get("phase", "running")
                t_start = tool.get("start_time", 0)
                t_end = tool.get("end_time", 0)
                tool_status = _TOOL_PHASE_STATUS.get(tool_phase, "running")
                if tool_phase in ("running", "parsing") and t_start > 0:
                    t_elapsed = time.time() - t_start
                elif t_end > 0:
                    t_elapsed = t_end - t_start
                else:
                    t_elapsed = 0.0
                tool_desc = f"{tool_name} {t_detail}" if t_detail else tool_name
                t_elapsed_str = f" {t_elapsed:.1f}s" if t_elapsed > 0 else ""
                tool_label = f"{tool_desc}{t_elapsed_str}"
                tool_children.append(TreeNode(
                    label=tool_label,
                    status=tool_status,
                    is_expanded=True,
                ))

        # ── 构建 agent 节点 ──
        agent_node = TreeNode(
            label=label_text,
            status=status,
            children=tool_children,
            metadata={
                "agent_type": agent_type,
                "elapsed": elapsed,
                "tokens": output_tokens,
                "model_phase": model_phase,
                "label_key": label,
            },
        )
        children.append(agent_node)

    # 返回以空 label 为根的容器节点
    return TreeNode(
        label="",
        status="running",
        children=children,
        metadata={"type": "subagent_root"},
    )


def _format_elapsed(seconds: float) -> str:
    """格式化耗时为人类可读字符串。

    Args:
        seconds: 秒数。

    Returns:
        格式化字符串，如 "1.2s" / "2m" / "1h30m"。
    """
    seconds = max(0.0, seconds)
    if seconds < 1.0:
        return f"{seconds:.1f}s"
    elif seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s}s" if s > 0 else f"{m}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m" if m > 0 else f"{h}h"
