"""chat_ui 工具函数模块 — _truncate_msg / _cmd_name 通用工具。

Layer 0 — 仅依赖 _const（RenderCommand 枚举），无其他内部依赖。
"""

from __future__ import annotations

import sys

from .const import RenderCommand


def _truncate_msg(msg: str, max_len: int) -> str:
    """截断超长消息，追加"..."标记（尾部安全）。

    若 `msg` 长度超过 `max_len`，取前 `max_len` 字符并追加 "..."。
    若未超过，原样返回。
    """
    if len(msg) > max_len:
        return msg[:max_len] + "..."
    return msg


def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。

    返回枚举名的 `name` 属性（如 0→"REASONING"），
    未知 ID 时回退为字符串格式的整数值（如 "255"）。
    """
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


def _emergency_write(text: str, stream: str = "stdout") -> None:
    """紧急输出 — 绕过 OutputAdapter 直写终端。

    仅在渲染管线不可用时使用（崩溃/队列满等紧急场景）。
    不持有 output_lock，不经过 Rich/OutputAdapter 处理。
    适用于：render 线程崩溃通知、队列满降级通知、parse_info 实时覆盖行。

    Args:
        text: 要写入的文本。
        stream: 输出流，'stdout' 或 'stderr'。
    """
    f = sys.__stdout__ if stream == "stdout" else sys.__stderr__
    f.write(text)
    f.flush()
