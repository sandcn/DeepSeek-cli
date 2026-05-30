"""消息编辑公共模块 — 截断/重写消息的通用工具函数

CLI 和 WebUI 共用的消息列表操作函数。
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


def truncate_messages(messages: list[dict], keep_from_start: int) -> list[dict]:
    """截断消息列表，保留前 keep_from_start 条非 system 消息。

    保留 system 消息（通常是前几条），以及 keep_from_start 条非 system 消息。
    删除之后的非 system 消息。

    与 `get_sandbox_manager().restore_to_message()` 配合使用。

    Args:
        messages: 消息列表（可变，会原地修改）
        keep_from_start: 要保留的非 system 消息数量

    Returns:
        被删除的消息列表
    """
    # 找 system 消息的边界
    system_end = 0
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            system_end = i + 1
        else:
            break

    # 找第 keep_from_start 条非 system 消息的真实索引
    non_system_count = 0
    truncate_idx = len(messages)
    for i in range(system_end, len(messages)):
        if messages[i].get("role") != "system":
            non_system_count += 1
            if non_system_count > keep_from_start:
                truncate_idx = i
                break

    # 截断
    deleted = messages[truncate_idx:]
    del messages[truncate_idx:]
    return deleted


def clear_all_messages(messages: list[dict], build_system_prompt) -> None:
    """清空所有非 system 消息，重建 system prompt。

    与 `get_sandbox_manager().clear()` 配合使用。

    Args:
        messages: 消息列表（可变，会原地修改）
        build_system_prompt: 生成 system prompt 的可调用对象
    """
    messages.clear()
    for part in build_system_prompt():
        messages.append({"role": "system", "content": part})
