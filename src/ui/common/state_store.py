# DEPRECATED: 此文件仅为向后兼容保留，请直接导入 src.ui.state.agent_state
"""Agent 状态存储 — CLI 和 WebUI 共用的状态管理模块

从 ui/state/agent_state.py 重新导出，提供统一的公共入口。
CLI 和 WebUI 的显示层均可从此模块导入 AgentStateStore。
"""

from __future__ import annotations

from ..state.agent_state import AgentStateStore, AgentSlot, ToolRecord

__all__ = ["AgentStateStore", "AgentSlot", "ToolRecord"]
