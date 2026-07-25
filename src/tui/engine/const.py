"""chat_ui 常量模块 — RenderCommand 枚举、解析哨兵、ANSI 转义序列。

Layer 0 — 无内部依赖，被所有上层模块引用。

RenderCommand 分层（v1.3+）：
  - 框架通用命令 → engine/commands.py  FrameworkCommand
  - 聊天域命令   → consumer/chat_commands.py  ChatCommand
  - RenderCommand 保留为向后兼容别名（含全部 20 个枚举值）
"""

from __future__ import annotations

from enum import IntEnum

# ── 命令枚举分层导入 ────────────────────────────────────
from .commands import FrameworkCommand  # noqa: F401 — 重导出供外部使用
# ChatCommand 由 consumer/chat_commands.py 定义（Layer 1），
# 不从 engine/const.py（Layer 0）重导出以避免循环依赖。
# 使用者通过 src.tui.consumer 导入 ChatCommand。

def register_tui_styles() -> None:
    """[已弃用] 样式已在 style.py 模块加载时自动注册。保留为空函数以保持向后兼容。"""
    pass

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 25 + "\n"

# ── 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线） ──
# 用于队列满/render 崩溃等无法通过正常渲染管线输出的场景。
# 定义位于 src.tui.core.style，此处通过导入引用以消除重复定义。
from ..core.style import (
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_YELLOW,
    ANSI_EMERGENCY_RESET,
    ANSI_EMERGENCY_CURSOR_BOTTOM,
)

# 向后兼容别名（旧代码继续使用 _ANSI_* 名称，不破坏已有导入）
_ANSI_RED = ANSI_EMERGENCY_RED
_ANSI_YELLOW = ANSI_EMERGENCY_YELLOW
_ANSI_RESET = ANSI_EMERGENCY_RESET
_ANSI_CURSOR_BOTTOM = ANSI_EMERGENCY_CURSOR_BOTTOM


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。

    分层分类（v1.3+）：
      [框架通用] — 参见 FrameworkCommand（engine/commands.py）
        NOTIFICATION, WRITE_LINE, ERROR, SUBAGENT_FRAME, SPLASH
      [聊天域]   — 参见 ChatCommand（consumer/chat_commands.py）
        REASONING, CONTENT, PHASE_DONE, TOOL_OUTPUT, TOOL_SUMMARY,
        USER_MSG, PARSE_INFO, DISPLAY_MSGS, TOOL_COUNT_INC,
        TOOL_FAIL_INC, TOOL_COUNT_DEC, MAIN_PHASE
    """
    REASONING     = 0   # [聊天域] (0, text: str)
    CONTENT       = 1   # [聊天域] (1, text: str)
    PHASE_DONE    = 2   # [聊天域] (2, phase: str)
    TOOL_OUTPUT   = 6   # [聊天域] (6, text: str)
    TOOL_SUMMARY  = 7   # [聊天域] (7, successful: tuple, failed: tuple)
    USER_MSG      = 8   # [聊天域] (8, text: str)
    PARSE_INFO    = 9   # [聊天域] (9, tool_names: str, tokens: int, elapsed: float)
    NOTIFICATION  = 11  # [框架通用] (11, text: str)
    WRITE_LINE    = 12  # [框架通用] (12, text: str)
    DISPLAY_MSGS  = 13  # [聊天域] (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # [聊天域] (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # [聊天域] (15,) — 工具失败计数+1
    ERROR          = 16  # [框架通用] (16, message: str) — 系统错误（红色 ! 样式）
    TOOL_COUNT_DEC     = 17  # [聊天域] (17,) — 工具计数-1
    SUBAGENT_FRAME     = 18  # [框架通用] (18, frame_lines: tuple[str]) — SubAgent 面板帧
    SPLASH             = 19  # [框架通用] (19,) — 启动品牌屏
    MAIN_PHASE         = 20  # [聊天域] (20, phase: str) — 主Agent模型阶段变更
