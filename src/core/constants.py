"""共享常量模块

集中存放 core 模块中各子模块共享的常量，避免重复定义。
"""

# ── ANSI 终端颜色常量（纯色值，无依赖） ────────────────
# 从 ui/ansi.py 提升至 core 层，消除 core→ui 的反向依赖。
# ui/colors.py 从此处 re-export 以保持向后兼容。

GRAY: str = "\033[90m"
WHITE: str = "\033[37m"
CYAN: str = "\033[36m"
GREEN: str = "\033[32m"
YELLOW: str = "\033[33m"
RED: str = "\033[31m"
BLUE: str = "\033[34m"
MAGENTA: str = "\033[35m"
BOLD: str = "\033[1m"
DIM: str = "\033[2m"
RESET: str = "\033[0m"
ITALIC: str = "\033[3m"
UNDERLINE: str = "\033[4m"
BRIGHT_CYAN: str = "\033[96m"
BRIGHT_GREEN: str = "\033[92m"
BRIGHT_YELLOW: str = "\033[93m"
BRIGHT_BLUE: str = "\033[94m"
BRIGHT_MAGENTA: str = "\033[95m"
BRIGHT_WHITE: str = "\033[97m"
BRIGHT_RED: str = "\033[91m"
BRIGHT_BLACK: str = GRAY  # alias for GRAY/DARK_GRAY
BG_BLUE: str = "\033[44m"
BG_CYAN: str = "\033[46m"
BG_GREEN: str = "\033[42m"
BG_YELLOW: str = "\033[43m"
ORANGE: str = YELLOW
TEAL: str = CYAN
PINK: str = MAGENTA
LAVENDER: str = BRIGHT_MAGENTA
SOFT_GREEN: str = "\033[92m"
SOFT_BLUE: str = "\033[94m"
SOFT_YELLOW: str = "\033[93m"
DARK_GRAY: str = GRAY

# ── Token 格式化常量 ──

_TOKEN_K_THRESHOLD = 1000


def format_token_k(n: int) -> str:
    """将 token 数格式化为可读形式：≥1000 显示 x.xk，否则原样显示。"""
    if n >= _TOKEN_K_THRESHOLD:
        return f"{n / 1000:.1f}k"
    return str(n)


# ── 文件大小格式化工具 ──────────────────────────────


def human_size(size: int | float) -> str:
    """将字节数转换为人类可读格式（如 1.5K、3.2M、1.8G）。

    从 src/tools/ls.py 提取为共享工具函数。
    """
    if size < 1024:
        return str(size)
    for unit in ("K", "M", "G", "T", "P"):
        size_f = size / 1024
        if size_f < 1024:
            if size_f >= 100:
                return f"{size_f:.0f}{unit}"
            elif size_f >= 10:
                return f"{size_f:.1f}{unit}"
            else:
                return f"{size_f:.1f}{unit}"
        size = size_f
    return f"{size:.1f}P"


# ── 审计日志 ──────────────────────────────────────────

import logging as _logging
_audit_logger = _logging.getLogger("audit")


def audit_log(op: str, detail: str = "") -> None:
    """统一的审计日志记录。"""
    _audit_logger.info(f"{op} | {detail}")


# ── 消息过滤工具 ──────────────────────────────────────

def filter_system(messages: list[dict]) -> list[dict]:
    """过滤出系统消息。"""
    return [m for m in messages if m.get("role") == "system"]


def filter_non_system(messages: list[dict]) -> list[dict]:
    """过滤出非系统消息。"""
    return [m for m in messages if m.get("role") != "system"]


def filter_non_system_indices(messages: list[dict]) -> list[int]:
    """返回非系统消息在消息列表中的下标列表。"""
    return [i for i, m in enumerate(messages) if m.get("role") != "system"]
