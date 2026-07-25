"""chat_ui 工具函数模块 — _cmd_name / _emergency_write 通用工具。

Layer 0 — 仅依赖 _const（RenderCommand 枚举），无其他内部依赖。
"""

from __future__ import annotations

import sys

from .const import RenderCommand


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

    此函数有意使用 sys.__stdout__ / sys.__stderr__ 而非 OutputAdapter，
    这是设计上的刻意选择（NOT a bug）：
      - 这是紧急回退路径，绕过所有渲染管线（OutputAdapter / Rich / render_lock）
      - 用于 render 线程崩溃、队列满等无法通过正常路径输出终端的场景
      - 若经由 OutputAdapter 写入，在 render 线程已崩溃时可能死锁或丢失消息

    仅在以下场景使用：
      - render 线程崩溃通知（_handle_render_crash → _emergency_write）
      - 队列满降级通知（finally 排空丢弃计数）
      不适用于正常渲染路径。

    不持有 output_lock，不经过 Rich/OutputAdapter 处理。

    Args:
        text: 要写入的文本。
        stream: 输出流，'stdout' 或 'stderr'。
    """
    f = sys.__stdout__ if stream == "stdout" else sys.__stderr__
    f.write(text)
    f.flush()
