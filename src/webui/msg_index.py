"""消息索引分配 — WebSocket 消息与 session.messages 下标的映射管理

每个 WebSocket 连接独立拥有 MsgIndexState 实例，
用于将流式消息（reasoning_chunk / content_chunk / tool_* 等）
映射到 session.messages 中的实际下标，供前端排序和渲染使用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from ..core.constants import filter_non_system

_logger = logging.getLogger(__name__)


class MsgIndexState:
    """消息序号状态机，追踪当前 reasoning / content / tool 消息的下标。

    属性:
        reasoning_idx: 当前正在接收的 reasoning 块的 msg_index
        content_idx: 当前正在接收的 content 块的 msg_index
        tool_map: tool_label → msg_index 的映射
        tool_names: tool_label → tool_name 的映射（用于 label 重映射回退）
    """

    def __init__(self) -> None:
        self.reasoning_idx: int = -1
        self.content_idx: int = -1
        self.tool_map: dict[str, int] = {}
        self.tool_names: dict[str, str] = {}

    def reset(self) -> None:
        """重置状态（用户新消息时调用）。"""
        self.reasoning_idx = -1
        self.content_idx = -1
        self.tool_map.clear()
        self.tool_names.clear()

    def __repr__(self) -> str:
        tm = dict(list(self.tool_map.items())[:5])
        tn = dict(list(self.tool_names.items())[:5])
        return (f"MsgIndexState(reasoning_idx={self.reasoning_idx}, "
                f"content_idx={self.content_idx}, "
                f"tool_map={tm}, "
                f"tool_names={tn})")


def non_system_len(messages: list[dict]) -> int:
    """返回 session.messages 中非 system 消息的数量。"""
    return len(filter_non_system(messages))


# ── Handler 函数 ──────────────────────────────────────────


def _handle_user_message(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    # user 消息还未添加到 messages，下标 = 当前非 system 数量
    # 保留 tool_map/tool_names，避免异步任务中未执行的 assign_msg_index 丢失索引
    state.reasoning_idx = -1
    state.content_idx = -1
    msg["msg_index"] = nsl


def _handle_reasoning_chunk(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    if state.reasoning_idx < 0:
        if state.content_idx >= 0:
            # content 已开始：迟到的 reasoning chunk → 并入同一气泡
            state.reasoning_idx = state.content_idx
        else:
            state.reasoning_idx = nsl
    msg["msg_index"] = state.reasoning_idx


def _handle_content_chunk(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    if state.content_idx < 0:
        if state.reasoning_idx >= 0:
            state.content_idx = state.reasoning_idx
        else:
            state.content_idx = nsl
    msg["msg_index"] = state.content_idx


def _handle_phase_done(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    p = msg.get("phase", "")
    if p == "reasoning":
        if state.reasoning_idx < 0:
            state.reasoning_idx = nsl
        msg["msg_index"] = state.reasoning_idx
    elif p == "content":
        if state.content_idx < 0:
            state.content_idx = nsl
        msg["msg_index"] = state.content_idx
    elif p == "segment_end":
        msg["msg_index"] = nsl
        state.reasoning_idx = -1
        state.content_idx = -1
    else:
        msg["msg_index"] = nsl


def _handle_tool_parsing(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    # tool 消息还未添加到 messages，下标 = 当前非 system 数量
    label = msg.get("label", "")
    tool_name = msg.get("tool_name", "")
    state.tool_map[label] = nsl
    state.tool_names[label] = tool_name
    msg["msg_index"] = nsl


def _handle_tool_lifecycle(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    """处理 tool_started / tool_done / tool_output_chunk / tool_status。

    label 格式约定：
    - tool_parsing 阶段 label = str(idx)（如 "0", "1"），
      由 _handle_tool_parsing 分配并记录在 state.tool_map 中。
    - 后续 lifecycle 消息 label = tc["id"]（如 "call_abc123"），
      由前端/API 生成。

    匹配策略（确定性匹配，避免脆弱回退）：
    1. 精确匹配 label → 直接使用 state.tool_map[label]
    2. label 以 "call_" 开头（tc["id"] 格式）→ 按 tool_name + 数字 key 回退
    3. label 为纯数字 → 直接按索引匹配
    4. 均未命中 → 分配新下标
    """
    label = msg.get("label", "")
    if label in state.tool_map:
        msg["msg_index"] = state.tool_map[label]
        return

    tn = msg.get("tool_name", "")
    matched_key: str | None = None

    if label.startswith("call_"):
        # tc["id"] 格式：按 tool_name + 数字 key 回退匹配
        candidates = [
            (k, state.tool_map[k]) for k in list(state.tool_map.keys())
            if k.isdigit() and state.tool_names.get(k, "") == tn
        ]
        if candidates:
            # 并行同名工具：按 msg_index 降序，优先匹配最新分配
            matched_key = max(candidates, key=lambda x: x[1])[0]
    elif label.isdigit():
        # 纯数字格式：直接按索引匹配（tool_parsing 阶段分配的数字 key）
        if label in state.tool_map:
            matched_key = label

    if matched_key is not None:
        msg["msg_index"] = state.tool_map[matched_key]
        # 将 key 重映射为 tc["id"]（后续 lifecycle 消息直接用 id 匹配）
        state.tool_map[label] = state.tool_map.pop(matched_key)
        state.tool_names[label] = state.tool_names.pop(matched_key)
    else:
        # 均未命中：分配新下标
        msg["msg_index"] = nsl
        state.tool_map[label] = nsl
        state.tool_names[label] = tn


def _handle_other(
    msg: dict,
    state: MsgIndexState,
    _messages: list[dict],
    nsl: int,
) -> None:
    # 处理 tool_summary / tool_batch_start / agent_added / agent_status
    msg["msg_index"] = nsl


# ── 消息类型 → handler 映射 ─────────────────────────────────

_MSG_HANDLERS: dict[str, Callable] = {
    "user_message": _handle_user_message,
    "reasoning_chunk": _handle_reasoning_chunk,
    "content_chunk": _handle_content_chunk,
    "phase_done": _handle_phase_done,
    "tool_parsing": _handle_tool_parsing,
    "tool_started": _handle_tool_lifecycle,
    "tool_done": _handle_tool_lifecycle,
    "tool_output_chunk": _handle_tool_lifecycle,
    "tool_status": _handle_tool_lifecycle,
    "tool_summary": _handle_other,
    "tool_batch_start": _handle_other,
    "agent_added": _handle_other,
    "agent_status": _handle_other,
}


async def assign_msg_index(
    msg: dict,
    state: MsgIndexState,
    messages: list[dict],
    ws_send: Callable[[dict], Awaitable[None]],
) -> None:
    """为消息分配 msg_index = 它在 session.messages 中的实际下标。

    处理以下消息类型：
    - user_message / reasoning_chunk / content_chunk / phase_done
    - tool_parsing / tool_started / tool_done / tool_output_chunk / tool_status
    - tool_summary / tool_batch_start / agent_added / agent_status

    Args:
        msg: 要发送的消息 dict（会被就地修改添加 msg_index）
        state: 当前连接的 MsgIndexState
        messages: session.messages
        ws_send: 异步发送函数（消息分配下标后异步发送，不阻塞当前协程）
    """
    mt = msg.get("type", "")
    nsl = non_system_len(messages)
    handler = _MSG_HANDLERS.get(mt)
    if handler:
        try:
            handler(msg, state, messages, nsl)
        except Exception:
            _logger.exception("assign_msg_index: handler 异常, type=%s, label=%s",
                              mt, msg.get("label", ""))
    # Bug 6 (P1) 修复: 异步发送，不等待 ws_send 完成，避免串行队列阻塞
    _send_task = asyncio.create_task(ws_send(msg))
    # ★ Bug 5 修复：追踪 fire-and-forget task 异常
    _send_task.add_done_callback(lambda t: _logger.exception("assign_msg_index: ws_send 异常: %s", t.exception())
                                 if t.done() and not t.cancelled() and t.exception() else None)
