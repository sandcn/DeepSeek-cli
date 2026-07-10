"""消息管理函数 — 从 ChatSession 提取的消息操作逻辑。

包含：添加消息、消息过滤属性。
这些函数接受 ChatSession 实例作为第一个参数，通过实例访问内部属性。
"""

from __future__ import annotations

# ── 常量 ─────────────────────────────────────────────────
_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"


# ═══════════════════════════════════════════════════════════════
# 消息添加
# ═══════════════════════════════════════════════════════════════

def add_message(session, content: str) -> None:
    """追加用户消息。

    Args:
        session: ChatSession 实例
        content: 消息内容
    """
    session._agent.add_user_message(content)


# ═══════════════════════════════════════════════════════════════
# 消息过滤
# ═══════════════════════════════════════════════════════════════

def non_system_messages(session) -> list[dict]:
    """获取所有非 system 消息。

    Args:
        session: ChatSession 实例

    Returns:
        非 system 消息列表
    """
    return [m for m in session._agent.messages if m.get(_ROLE_KEY) != _SYSTEM_ROLE]


def system_messages(session) -> list[dict]:
    """获取所有 system 消息。

    Args:
        session: ChatSession 实例

    Returns:
        system 消息列表
    """
    return [m for m in session._agent.messages if m.get(_ROLE_KEY) == _SYSTEM_ROLE]
