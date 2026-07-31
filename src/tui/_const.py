"""统一常量定义 — RenderCommand + FrameworkCommand + ChatCommand + ANSI 紧急路径常量。

Layer 0 — 无内部依赖，被所有 TUI 模块引用。
整合旧 engine/const.py、engine/commands.py、consumer/chat_commands.py 三套枚举到一个文件。
"""

from __future__ import annotations

from enum import IntEnum

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE: int = -1

# ═══════════════════════════════════════════════════════════
# 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线）
# ═══════════════════════════════════════════════════════════
ANSI_EMERGENCY_RED: str = "\033[31m"
ANSI_EMERGENCY_YELLOW: str = "\033[33m"
ANSI_EMERGENCY_RESET: str = "\033[0m"
ANSI_EMERGENCY_CURSOR_BOTTOM: str = "\033[9999;1H"


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（向后兼容，含全部 20 个值）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。合并 FrameworkCommand（框架命令）与 ChatCommand（聊天命令），值完全不变（20 个枚举值）。"""
    REASONING = 0       # (0, text: str)
    CONTENT = 1         # (1, text: str)
    PHASE_DONE = 2      # (2, phase: str)
    TOOL_OUTPUT = 6     # (6, text: str)
    TOOL_SUMMARY = 7    # (7, successful: tuple, failed: tuple)
    USER_MSG = 8        # (8, text: str)
    PARSE_INFO = 9      # (9, tool_names: str, tokens: int, elapsed: float)
    NOTIFICATION = 11   # (11, text: str)
    WRITE_LINE = 12     # (12, text: str)
    DISPLAY_MSGS = 13   # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14 # (14,) — 工具计数+1
    TOOL_FAIL_INC = 15  # (15,) — 工具失败计数+1
    ERROR = 16          # (16, message: str) — 系统错误
    TOOL_COUNT_DEC = 17 # (17,) — 工具计数-1
    SUBAGENT_FRAME = 18 # (18, frame_lines: tuple[str]) — SubAgent 面板帧
    SPLASH = 19         # (19,) — 启动品牌屏
    MAIN_PHASE = 20     # (20, phase: str) — 主Agent模型阶段变更


# ═══════════════════════════════════════════════════════════
# 向后兼容别名 — FrameworkCommand/ChatCommand → RenderCommand
# ═══════════════════════════════════════════════════════════

FrameworkCommand = RenderCommand
ChatCommand = RenderCommand
