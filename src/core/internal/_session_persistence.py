"""会话持久化函数 — 从 ChatSession 提取的持久化逻辑。

包含：保存/加载会话、断点管理（checkpoint save/load/resume）、会话列表。
这些函数接受 ChatSession 实例作为第一个参数，通过实例访问内部属性。
"""

from __future__ import annotations

import asyncio
import logging

_logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────
_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"
_MIN_CHECKPOINT_MESSAGES = 2        # resume_from_checkpoint 检查点消息数最小值
_TASK_DESC_TRUNCATE = 200           # 检查点恢复时任务描述截断长度


# ═══════════════════════════════════════════════════════════════
# 会话保存/加载
# ═══════════════════════════════════════════════════════════════

def save_session(session) -> str | None:
    """保存当前会话到 .chat/msg_list/。

    状态转换: COMPLETED/INTERRUPTED/IDLE → IDLE

    Args:
        session: ChatSession 实例

    Returns:
        session_id，无可保存内容时返回 None
    """
    non_system = [m for m in session._agent.messages if m.get(_ROLE_KEY) != _SYSTEM_ROLE]
    if not non_system:
        # ★ 修复：空消息时也执行状态转换，防止状态机残留在 COMPLETED 或 INTERRUPTED
        safe_save_state(session)
        return session._session_id

    sid = session._persistence_port.save_session(
        messages=non_system,
        model=session._model,
        session_id=session._session_id,
    )
    session._session_id = sid

    # 状态转换：COMPLETED/INTERRUPTED → IDLE
    safe_save_state(session)

    session._emit("saved", session_id=sid)
    return sid


def load_session_data(session, session_id: str) -> dict | None:
    """加载历史会话。

    会替换当前非 system 消息，保留当前 system prompt。
    根据最后一条消息的角色设置状态机的 retry 能力。

    Args:
        session: ChatSession 实例
        session_id: 会话 ID（可带或不带 .json 后缀）

    Returns:
        会话数据字典，不存在时返回 None
    """
    data = session._persistence_port.load_session(session_id)
    if data is None:
        return None

    loaded_msgs = data.get("messages", [])
    if not loaded_msgs:
        return None

    # 保留 system 消息，替换其余消息
    system_msgs = [m for m in session._agent.messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
    session._agent.messages[:] = system_msgs
    for msg in loaded_msgs:
        session._agent.messages.append(msg)

    session._model = data.get("model", session._model)
    session._agent.model = session._model
    session._session_id = session_id

    # 最后一条是 user 消息 → 标记 retry_pending
    if session._agent.messages and session._agent.messages[-1].get(_ROLE_KEY) == "user":
        session._retry_pending = True
    else:
        session._retry_pending = False

    session._metrics.gauge("session.messages", len(session._agent.messages))

    session._emit("loaded", data=data)
    return data


def list_sessions_fn(session) -> list[dict]:
    """列出所有保存的会话。"""
    return session._persistence_port.list_sessions()


def get_session_ids_fn(session) -> list[str]:
    """列出所有保存的会话 ID。"""
    return [s["id"] for s in session._persistence_port.list_sessions()]


# ═══════════════════════════════════════════════════════════════
# 断点管理
# ═══════════════════════════════════════════════════════════════

def save_checkpoint_session(session) -> None:
    """保存断点（任务中断时调用）。"""
    session._checkpoint_port.save(session._agent.messages, session._model)
    session._emit("checkpoint_saved")


def clear_checkpoint_session(session) -> None:
    """清除断点（任务成功完成时调用）。"""
    session._checkpoint_port.clear()
    session._emit("checkpoint_cleared")


def load_checkpoint_data(session) -> dict | None:
    """加载断点数据。"""
    return session._checkpoint_port.load()


def has_checkpoint_session(session) -> bool:
    """检查是否存在有效断点。"""
    return session._checkpoint_port.exists()


def resume_from_checkpoint_session(session) -> bool:
    """从断点恢复任务。

    Args:
        session: ChatSession 实例

    Returns:
        是否成功恢复
    """
    info = session._checkpoint_port.get_info()
    if not info:
        return False

    data = session._checkpoint_port.load()
    if not data:
        return False

    checkpoint_msgs = data.get("messages", [])
    if len(checkpoint_msgs) < _MIN_CHECKPOINT_MESSAGES:
        session._checkpoint_port.clear()
        session._emit("checkpoint_cleared")
        return False

    # 保留 system 消息，替换为非 system 消息
    system_msgs = [m for m in session._agent.messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
    session._agent.messages[:] = system_msgs
    for msg in checkpoint_msgs:
        if msg.get(_ROLE_KEY) == _SYSTEM_ROLE:
            continue
        session._agent.messages.append(dict(msg))

    # 注入恢复指令
    resume_prompt = (
        f"[检查点恢复] 你的任务在之前被中断了。\n"
        f"中断时的任务描述: {info['task_description'][:_TASK_DESC_TRUNCATE]}\n"
        f"请先回顾以上对话历史，了解当前进度和已完成的工作，\n"
        f"然后继续完成剩余任务。不要重复已完成的步骤。"
    )
    session._agent.messages.append({_ROLE_KEY: _SYSTEM_ROLE, "content": resume_prompt})

    checkpoint_model = data.get("model", session._model)
    if checkpoint_model:
        session._model = checkpoint_model
        session._agent.model = checkpoint_model

    session._checkpoint_port.clear()
    session._emit("checkpoint_cleared")

    # 触发状态机：允许 retry
    session._metrics.gauge("session.messages", len(session._agent.messages))

    session._emit("checkpoint_restored", info=info)
    return True


# ═══════════════════════════════════════════════════════════════
# 状态机辅助
# ═══════════════════════════════════════════════════════════════

def safe_save_state(session) -> None:
    """安全执行状态机 save 转换（忽略无效转换）。"""
    from ..state_machine import InvalidTransitionError

    if session._state_machine.can("save"):
        try:
            session._state_machine.save()
        except InvalidTransitionError:
            _logger.debug("状态机 save 转换无效（当前状态: %s）", session._state_machine.name)
