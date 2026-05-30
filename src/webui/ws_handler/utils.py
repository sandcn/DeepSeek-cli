"""辅助函数与工具 — _rebuild_message_indices, _WebCmdCtx

包含公共辅助函数和命令上下文类。
"""

from __future__ import annotations

import logging

from ...core.constants import filter_non_system

_logger = logging.getLogger(__name__)


def _rebuild_message_indices(messages: list[dict]) -> list[dict]:
    """为非 system 消息重新分配 msg_index。

    用于 /clear /editmsg 等命令后重建前端消息状态，以及在 WebSocket
    初始化时发送完整消息列表。

    修复：assistant 消息始终设置 content_msg_index 和 reasoning_msg_index，
    避免 content=None（tool_calls 场景）或 reasoning_content="" 时索引缺失，
    导致前端 session_initialized 显示历史消息失败。

    注意：使用浅拷贝 + 选择性深拷贝 tool_calls，避免 deepcopy 递归复制大文本。
    """
    non_system = filter_non_system(messages)
    rebuilt = []
    for i, m in enumerate(non_system):
        # ★ 浅拷贝避免 deepcopy 递归复制大文本（content/reasoning_content）
        m_copy = dict(m)
        if m_copy.get("role") == "user":
            m_copy["msg_index"] = i
        elif m_copy.get("role") == "assistant":
            # 始终设置两个索引，解决前端 msg.content_msg_index || msg.reasoning_msg_index
            # 在 content=None 或 reasoning_content="" 时 idx 变为 undefined 的问题
            m_copy["content_msg_index"] = i
            m_copy["reasoning_msg_index"] = i
            if m_copy.get("tool_calls"):
                # 手动深拷贝 tool_calls 避免共享引用
                m_copy["tool_calls"] = [
                    {**tc, "msg_index": i} for tc in m_copy["tool_calls"]
                ]
        rebuilt.append(m_copy)
    return rebuilt


class _WebCmdCtx:
    """WebSocket 命令上下文。

    封装命令执行所需的环境：消息列表、参数、状态、系统提示构建函数等。
    """

    def __init__(self, msgs, session):
        self.messages = msgs
        self.arg = ""
        self.state = {"model": session.model, "retry": False, "prefill": ""}
        # 使用 Agent 的绑定方法，与 CLI 路径（session.agent.build_system_prompt）保持一致
        self.build_system_prompt = session.agent.build_system_prompt
        # Web 环境下无交互式输入，始终返回空字符串
        self.get_user_input = lambda prompt="": ""
        self.context_manager = getattr(session, '_ctx_mgr', None)
