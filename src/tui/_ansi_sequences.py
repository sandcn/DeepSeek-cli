"""ANSI 转义序列原语 — 进程内真源（Layer 0，纯函数，零状态）。

历史：这些序列生成函数原本住在 ``src/tui/_screen.py``（应用层终端工具）。
``InkRenderer`` / ``InkSession`` / ``render()`` 都需要它们，形成「框架 →
应用终端模块」的反向依赖。真源迁到本模块（零内部依赖）后：

  - ``src/tui/ink/ansi.py`` — 框架侧门面（re-export，供 ink 内部导入）；
  - ``src/tui/_screen.py``   — 应用侧 re-export（既有导入路径零改动）。

为什么不直接把真源放 ``ink/ansi.py``：``src/tui/__init__`` → ``_screen`` →
``ink.ansi`` 会触发 ``ink/__init__`` → ``_render_api`` → ``_screen``，形成
**包初始化期循环导入**（实测 ImportError）。真源放在不触发 ``ink`` 包初始化
的位置，同时满足「框架不依赖应用终端模块」与「无循环」。

不在此模块的内容（保留在 ``_screen.py``，由注入提供或仅应用使用）：
  - 终端尺寸查询 / ``TerminalWidthCache`` / SIGWINCH 注册（有状态 + 平台分支）
  - 光标行查询（CPR，需读写真实 tty）
  - 剪贴板（OSC 52 + 平台差异）
  - 窗口标题设置（写 stdout 副作用）
  - 真彩检测（依赖 ``core.color`` 的应用策略）

约定：所有函数都是 **纯函数**（输入 → 字符串），不写 stdout、不读环境。
"""

from __future__ import annotations

__all__ = [
    "ANSI_EMERGENCY_RED",
    "ANSI_EMERGENCY_YELLOW",
    "ANSI_EMERGENCY_RESET",
    "ANSI_EMERGENCY_CURSOR_BOTTOM",
    "set_scroll_region",
    "reset_scroll_region",
    "cursor_save",
    "cursor_restore",
    "cursor_goto",
    "cursor_up",
    "cursor_down",
    "cursor_forward",
    "cursor_hide",
    "cursor_show",
    "clear_line",
    "clear_screen_from_cursor",
    "clear_screen",
    "move_clear",
    "scroll_up",
    "scroll_down",
    "insert_lines",
    "sgr",
    "sgr_reset",
    "fg_256",
    "bg_256",
]

# ═══════════════════════════════════════════════════════════
# 紧急路径序列（绕过渲染管线直写终端；崩溃兜底用）
# ═══════════════════════════════════════════════════════════
ANSI_EMERGENCY_RED: str = "\033[31m"
ANSI_EMERGENCY_YELLOW: str = "\033[33m"
ANSI_EMERGENCY_RESET: str = "\033[0m"
ANSI_EMERGENCY_CURSOR_BOTTOM: str = "\033[9999;1H"


# ═══════════════════════════════════════════════════════════
# 滚动区域 (DECSTBM)
# ═══════════════════════════════════════════════════════════

def set_scroll_region(top: int, bottom: int) -> str:
    """设置滚动区域（1-based 行号）。"""
    return f"\033[{top};{bottom}r"


def reset_scroll_region() -> str:
    """重置滚动区域为全屏。"""
    return "\033[r"


# ═══════════════════════════════════════════════════════════
# 光标控制
# ═══════════════════════════════════════════════════════════

def cursor_save() -> str:
    """保存光标位置 (SCOSC)。"""
    return "\033[s"


def cursor_restore() -> str:
    """恢复光标位置 (SCRC)。"""
    return "\033[u"


def cursor_goto(row: int, col: int) -> str:
    """移动光标到指定位置 (CUP, 1-based)。"""
    return f"\033[{row};{col}H"


def cursor_up(n: int = 1) -> str:
    """光标上移 n 行 (CUU)。"""
    return f"\033[{n}A"


def cursor_down(n: int = 1) -> str:
    """光标下移 n 行 (CUD)。"""
    return f"\033[{n}B"


def cursor_forward(n: int = 1) -> str:
    """光标右移 n 列 (CUF)。"""
    return f"\033[{n}C"


def cursor_hide() -> str:
    """隐藏终端光标（DECTCEM DECSET ?25l）。"""
    return "\033[?25l"


def cursor_show() -> str:
    """显示终端光标（DECTCEM DECRST ?25h）。"""
    return "\033[?25h"


# ═══════════════════════════════════════════════════════════
# 清屏 / 清行
# ═══════════════════════════════════════════════════════════

def clear_line() -> str:
    """清除从光标到行尾的内容 (EL 0)，并回到行首。"""
    return "\r\033[K"


def clear_screen_from_cursor() -> str:
    """清除从光标到屏幕末尾 (ED 0)。"""
    return "\033[0J"


def clear_screen() -> str:
    """清除整个屏幕 (ED 2) 并归位光标。"""
    return "\033[2J\033[H"


def move_clear(row: int) -> str:
    """组合光标定位 + 清行（1-based 行号）。"""
    return f"\033[{row};1H\033[K"


# ═══════════════════════════════════════════════════════════
# 滚动
# ═══════════════════════════════════════════════════════════

def scroll_up(n: int = 1) -> str:
    """向上滚动 n 行 (SU)。"""
    return f"\033[{n}S"


def scroll_down(n: int = 1) -> str:
    """向下滚动 n 行 (SD)。"""
    return f"\033[{n}T"


def insert_lines(n: int = 1) -> str:
    """在当前光标行插入 n 个空行 (IL)，下方内容整体下移、滚动区底部 n 行丢弃。

    增量渲染器在「底部对齐且文档变矮」时用它把整篇可见内容下移：终端原生
    完成「插入空行 + 内容下移 + 底部丢弃」，写入量从 O(屏幕高度) 降到
    O(|delta| + 变化行)。``<=0`` 按 1 处理。

    IL 只影响光标行及其下方内容——文档上方（矮文档时的终端历史）保持不动；
    高文档（文档首行在屏幕外上方）时由调用方在屏幕第 1 行插入。
    """
    n = max(1, int(n))
    return f"\033[{n}L"


# ═══════════════════════════════════════════════════════════
# 颜色 / SGR
# ═══════════════════════════════════════════════════════════

def sgr(*codes: int) -> str:
    """构建 SGR 序列（无参数 = 重置）。"""
    if not codes:
        return "\033[0m"
    return f"\033[{';'.join(str(c) for c in codes)}m"


def sgr_reset() -> str:
    """SGR 重置。"""
    return "\033[0m"


def fg_256(color: int) -> str:
    """设置 256 色前景色。"""
    return f"\033[38;5;{color}m"


def bg_256(color: int) -> str:
    """设置 256 色背景色。"""
    return f"\033[48;5;{color}m"
