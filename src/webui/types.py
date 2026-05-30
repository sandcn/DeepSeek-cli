"""前端消息类型常量与消息构建器 — 集中管理 WebSocket 消息类型

所有 WebSocket 消息的 type 字段定义在此处，消除散落在各个模块中的字符串字面量。
消息构建函数确保前后端消息契约的一致性：后端只负责构建正确格式的消息，
不关心发送细节（发送逻辑在 _base_sender.py 中）。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


# ═══════════════════════════════════════════════════════════════
# 消息类型常量 — 前端 WebSocket 消息的 type 字段
# ═══════════════════════════════════════════════════════════════

# ── 共享常量 ──
_ORPHANED_ATTR = "_orphaned_task"
_MESSAGE_PREVIEW_LENGTH = 200  # 消息预览截断长度（字符）
_MAX_MSG_LENGTH = 100_000      # 单条消息最大长度（100KB）

class WSMsgType(str, Enum):
    """WebSocket 消息类型常量集。"""

    # ── 生命周期 ──
    DISPLAY_STARTED = "display_started"
    DISPLAY_STOPPED = "display_stopped"

    # ── 内容流 ──
    CONTENT_CHUNK = "content_chunk"
    REASONING_CHUNK = "reasoning_chunk"
    PHASE_DONE = "phase_done"

    # ── 工具调用 ──
    TOOL_PARSING = "tool_parsing"
    TOOL_STARTED = "tool_started"
    TOOL_DONE = "tool_done"
    TOOL_OUTPUT_CHUNK = "tool_output_chunk"
    TOOL_STATUS = "tool_status"
    TOOL_SUMMARY = "tool_summary"
    TOOL_BATCH_START = "tool_batch_start"
    PARSE_INFO = "parse_info"

    # ── Agent 生命周期 ──
    AGENT_ADDED = "agent_added"
    AGENT_STATUS = "agent_status"
    AGENT_PHASE = "agent_phase"
    AGENT_USAGE = "agent_usage"
    AGENT_RESULT = "agent_result"

    # ── Sub-Agent 工具事件 ──
    AGENT_TOOL_PARSING = "agent_tool_parsing"
    AGENT_TOOL_STARTED = "agent_tool_started"
    AGENT_TOOL_DONE = "agent_tool_done"

    # ── 模型阶段/用量 ──
    MODEL_PHASE = "model_phase"
    USAGE_UPDATE = "usage_update"
    COMMAND_OUTPUT = "command_output"

    # ── 用户交互 ──
    USER_SELECT_NEEDED = "user_select_needed"

    # ── 沙盒 ──
    SANDBOX_FILES = "sandbox_files"
    SANDBOX_FILE_DIFF = "sandbox_file_diff"

    # ── 消息编辑 ──
    MESSAGES_LIST = "messages_list"
    EDIT_MESSAGES_RESULT = "edit_messages_result"

    # ── 模型切换 ──
    MODELS_LIST = "models_list"
    MODEL_CHANGED = "model_changed"

    # ── 实时指标 ──
    SPEED_UPDATE = "speed_update"
    LIVE_INPUT = "live_input"
    LIVE_OUTPUT = "live_output"

    # ── 会话历史 ──
    SESSIONS_LIST = "sessions_list"
    SESSION_DELETED = "session_deleted"
    SESSION_LOADED = "session_loaded"
    SESSION_RENAMED = "session_renamed"

    # ── 会话统计与费用 ──
    ROUND_COST = "round_cost"

    # ── 输出帧（IOutputTarget 帧渲染） ──
    OUTPUT_FRAME = "output_frame"


# ═══════════════════════════════════════════════════════════════
# 消息构建函数 — 构建发送给前端的消息 dict
# ═══════════════════════════════════════════════════════════════

def msg_content_chunk(text: str, label: str) -> dict:
    return {"type": WSMsgType.CONTENT_CHUNK, "text": text, "label": label}


def msg_reasoning_chunk(text: str, label: str) -> dict:
    return {"type": WSMsgType.REASONING_CHUNK, "text": text, "label": label}


def msg_phase_done(phase: str, label: str) -> dict:
    return {"type": WSMsgType.PHASE_DONE, "phase": phase, "label": label}


def msg_tool_parsing(label: str, tool_name: str, arguments: str = "") -> dict:
    return {"type": WSMsgType.TOOL_PARSING, "label": label, "tool_name": tool_name, "arguments": arguments}


def msg_tool_started(label: str, tool_name: str, detail: str = "",
                     metadata: dict | None = None) -> dict:
    return {"type": WSMsgType.TOOL_STARTED, "label": label, "tool_name": tool_name,
            "detail": detail, "metadata": metadata or {}}


def msg_tool_done(label: str, tool_name: str = "", success: bool = True,
                  metadata: dict | None = None) -> dict:
    return {"type": WSMsgType.TOOL_DONE, "label": label, "tool_name": tool_name,
            "success": success, "metadata": metadata or {}}


def msg_agent_tool_parsing(label: str, tool_name: str, arguments: str = "") -> dict:
    """构建子Agent工具解析中消息。"""
    return {"type": WSMsgType.AGENT_TOOL_PARSING, "agent_label": label,
            "tool_name": tool_name, "arguments": arguments}


def msg_agent_tool_started(label: str, tool_name: str, detail: str = "") -> dict:
    """构建子Agent工具开始消息。"""
    return {"type": WSMsgType.AGENT_TOOL_STARTED, "agent_label": label,
            "tool_name": tool_name, "detail": detail}


def msg_agent_tool_done(label: str, tool_name: str = "", success: bool = True) -> dict:
    """构建子Agent工具完成消息。"""
    return {"type": WSMsgType.AGENT_TOOL_DONE, "agent_label": label,
            "tool_name": tool_name, "success": success}


def msg_tool_status(label: str, status: str) -> dict:
    return {"type": WSMsgType.TOOL_STATUS, "label": label, "status": status}


def msg_tool_summary(successful_tools: list, failed_tools: list) -> dict:
    return {
        "type": WSMsgType.TOOL_SUMMARY,
        "successful_tools": list(successful_tools),
        "failed_tools": [{"name": name, "error": error} for name, error in failed_tools],
    }


def msg_tool_output_chunk(label: str, text: str) -> dict:
    return {"type": WSMsgType.TOOL_OUTPUT_CHUNK, "label": label, "text": text}


def msg_tool_batch_start(label: str, names: list) -> dict:
    return {"type": WSMsgType.TOOL_BATCH_START, "label": label, "names": names}


def msg_model_phase(label: str, phase: str, info: str = "") -> dict:
    return {"type": WSMsgType.MODEL_PHASE, "label": label, "phase": phase, "info": info}


def msg_usage_update(label: str, usage: dict, replace: bool = False) -> dict:
    return {"type": WSMsgType.USAGE_UPDATE, "label": label, "usage": usage, "replace": replace}


def msg_agent_added(label: str, description: str, status: str, source: str = "",
                    dispatch_label: str = "") -> dict:
    d: dict[str, Any] = {"type": WSMsgType.AGENT_ADDED, "label": label,
                         "description": description, "status": status}
    if source:
        d["source"] = source
    if dispatch_label:
        d["dispatch_label"] = dispatch_label
    return d


def msg_agent_status(label: str, status: str) -> dict:
    return {"type": WSMsgType.AGENT_STATUS, "label": label, "status": status}


def msg_command_output(text: str, level: str = "info") -> dict:
    return {"type": WSMsgType.COMMAND_OUTPUT, "text": text, "level": level}


def msg_user_select_needed(select_id: str, title: str, options: list,
                           multi_select: bool, default_options: list,
                           timeout: int) -> dict:
    return {
        "type": WSMsgType.USER_SELECT_NEEDED,
        "select_id": select_id,
        "title": title,
        "options": list(options),
        "multi_select": multi_select,
        "default_options": list(default_options),
        "timeout": timeout,
    }


def msg_parse_info(label: str, tool_name: str, tokens: int, elapsed: float) -> dict:
    return {"type": WSMsgType.PARSE_INFO, "label": label, "tool_name": tool_name,
            "tokens": tokens, "elapsed": elapsed}


def msg_speed_update(label: str, speed: float) -> dict:
    return {"type": WSMsgType.SPEED_UPDATE, "label": label, "speed": speed}


def msg_live_input(label: str, tokens: int) -> dict:
    return {"type": WSMsgType.LIVE_INPUT, "label": label, "tokens": tokens}


def msg_live_output(label: str, tokens: int) -> dict:
    return {"type": WSMsgType.LIVE_OUTPUT, "label": label, "tokens": tokens}


def msg_display_started() -> dict:
    return {"type": WSMsgType.DISPLAY_STARTED}


def msg_display_stopped(final: bool = False) -> dict:
    return {"type": WSMsgType.DISPLAY_STOPPED, "final": final}


def msg_sessions_list(sessions: list, current_id: str = "") -> dict:
    return {"type": WSMsgType.SESSIONS_LIST, "sessions": sessions, "current_id": current_id}


def msg_session_deleted(session_id: str) -> dict:
    return {"type": WSMsgType.SESSION_DELETED, "session_id": session_id}


def msg_session_loaded(session_id: str, model: str, messages: list) -> dict:
    return {"type": WSMsgType.SESSION_LOADED, "session_id": session_id, "model": model, "messages": messages}


def msg_output_frame(lines: list[str], last_lines: int) -> dict:
    """构建输出帧消息 — 将 IOutputTarget.render_frame 的输出推送到前端。

    Args:
        lines: 当前帧的行列表
        last_lines: 上一帧的行数（供前端增量更新参考）

    Returns:
        WebSocket 消息字典
    """
    return {
        "type": WSMsgType.OUTPUT_FRAME,
        "lines": list(lines),
        "last_lines": last_lines,
    }


def msg_round_cost(data: dict) -> dict:
    """构建每轮对话成本消息。

    Args:
        data: compute_round_cost_data() 返回的数据字典

    Returns:
        {
            "type": "round_cost",
            "delta_in": int, "delta_out": int, "delta_calls": int,
            "model": str, "round_cost": float, "total_cost": float,
            "total_input": int, "total_output": int,
            "calls_str": str, "duration_str": str,
            "ctx_pct": float, "compress_hint": str,
        }

    ★ 白名单控制：仅提取预期字段，防止 data 中意外字段泄漏到前端。
    """
    # 预期字段白名单 — 与 docstring 返回格式一致
    _COST_FIELDS = frozenset({
        "delta_in", "delta_out", "delta_calls", "model",
        "round_cost", "total_cost", "total_input", "total_output",
        "calls_str", "duration_str", "ctx_pct", "compress_hint",
    })
    return {
        "type": WSMsgType.ROUND_COST,
        **{k: data[k] for k in _COST_FIELDS if k in data},
    }


__all__ = [
    "WSMsgType",
    "msg_content_chunk", "msg_reasoning_chunk", "msg_phase_done",
    "msg_tool_parsing", "msg_tool_started", "msg_tool_done",
    "msg_tool_status", "msg_tool_summary", "msg_tool_batch_start", "msg_tool_output_chunk",
    "msg_model_phase", "msg_usage_update",
    "msg_agent_added", "msg_agent_status",
    "msg_agent_tool_parsing", "msg_agent_tool_started", "msg_agent_tool_done",
    "msg_command_output",
    "msg_user_select_needed",
    "msg_speed_update", "msg_live_input", "msg_live_output",
    "msg_parse_info",
    "msg_sessions_list", "msg_session_deleted", "msg_session_loaded",
    "msg_display_started", "msg_display_stopped",
    "msg_output_frame",
    "msg_round_cost",
]
