"""消息编辑处理 — _handle_get_messages, _handle_edit_messages_action

处理 get_messages 和 edit_messages_action 的 WebSocket 消息。
"""

from __future__ import annotations

import logging

from ...core.constants import filter_non_system, filter_non_system_indices
from ...core.message_edit import truncate_messages
from ...core.sandbox_manager import get_sandbox_manager as _get_sandbox
from . import _MESSAGE_PREVIEW_LENGTH
from .utils import _rebuild_message_indices

_logger = logging.getLogger(__name__)


async def _handle_get_messages(ws_send, session) -> None:
    """处理 get_messages 请求：返回非 system 消息列表（含 data_index 和 real_index）。"""
    messages = session.messages
    non_system = filter_non_system(messages)
    result = []
    for i, m in enumerate(non_system):
        entry = {
            "data_index": i,
            "role": m.get("role", ""),
            "content": (m.get("content") or "")[:_MESSAGE_PREVIEW_LENGTH],  # 消息预览截断
        }
        # tool_calls 消息：包含工具名称
        if m.get("tool_calls"):
            entry["tool_calls"] = [
                {
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                    }
                }
                for tc in m.get("tool_calls", [])
            ]
        result.append(entry)
    await ws_send({"type": "messages_list", "messages": result})


async def _handle_edit_messages_action(data: dict, ws_send, session, msg_idx_state, ws) -> None:
    """处理 edit_messages_action：截断或重写消息。

    data.action: "edit" - 从此重写（截断后预填旧内容到输入框）
                 "truncate" - 从此截断
    data.data_index: 非 system 消息列表中的索引
    """
    action = data.get("action", "")
    data_index = data.get("data_index", -1)

    messages = session.messages
    non_system_indices = filter_non_system_indices(messages)

    if data_index < 0 or data_index >= len(non_system_indices):
        await ws_send({"type": "edit_messages_result", "success": False, "error": "无效的消息索引"})
        return

    real_idx = non_system_indices[data_index]

    if action == "edit" or action == "truncate":
        old_content = messages[real_idx].get("content", "")

        # 截断：删除从 real_idx 开始的所有消息
        truncate_messages(messages, keep_from_start=data_index)

        # 恢复沙盒到截断点
        # ★ 索引体系说明：real_idx 是 session.messages 的实际索引，
        #   sandbox 中 message_index 也是 session.messages 的实际索引，
        #   两者一致。target_index = real_idx - 1 表示恢复到截断前一条消息。
        #   target_index < 0 表示截断了第一条非 system 消息，清空全部沙盒。
        sm = _get_sandbox()
        if sm:
            target_index = real_idx - 1
            if target_index < 0:
                _logger.info("截断至消息 #%d (real_idx=%d)：清空全部沙盒", data_index, real_idx)
                sm.clear()
            else:
                _logger.debug("截断至消息 #%d (real_idx=%d)：沙盒恢复到消息索引 %d",
                              data_index, real_idx, target_index)
                sm.restore_to_message(target_index)

        # 重置消息索引状态，避免旧索引指向错误位置
        msg_idx_state.reset()

        # 截断后重建消息索引，刷新前端界面
        rebuilt = _rebuild_message_indices(messages)
        # ★ 先发送 edit_messages_result（edit 带 prefill，truncate 无 prefill）
        if action == "edit":
            await ws_send({
                "type": "edit_messages_result",
                "success": True,
                "action": "edit",
                "prefill": old_content,
            })
        else:  # truncate
            await ws_send({
                "type": "edit_messages_result",
                "success": True,
                "action": "truncate",
            })
        # ★ Bug 2 修复：改用专用事件 messages_truncated，避免触发前端全量重建
        await ws_send({
            "type": "messages_truncated",
            "messages": rebuilt,
            "model": session.model,
        })
    else:
        await ws_send({"type": "edit_messages_result", "success": False, "error": f"未知操作: {action}"})
