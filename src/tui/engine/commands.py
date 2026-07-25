"""框架层通用渲染命令枚举。

Layer 0 — 无内部依赖，定义所有 TUI 框架可复用的渲染命令。
与聊天域命令（consumer/chat_commands.py）共同构成完整的命令命名空间。

命令值范围约定：
  - 框架层：0-99（与聊天域共享同一命名空间）
  - 聊天域：0-99（共享同一命名空间，按语义分区）
"""

from __future__ import annotations

from enum import IntEnum


class FrameworkCommand(IntEnum):
    """框架层通用渲染命令 — 与聊天域无关，可被任何 TUI 应用复用。

    每个枚举值对应 TuiRenderer/FrameworkRenderer 中的一个 _do_* 处理方法。
    值与 RenderCommand 保持一致，类型不同以支持按层过滤。
    """

    NOTIFICATION = 11
    """(11, text: str) — 通用通知消息"""

    WRITE_LINE = 12
    """(12, text: str) — 直接写入一行文本到终端"""

    ERROR = 16
    """(16, message: str) — 系统错误（红色 ! 样式）"""

    SUBAGENT_FRAME = 18
    """(18, frame_lines: tuple[str]) — SubAgent 面板帧"""

    SPLASH = 19
    """(19,) — 启动品牌屏"""
