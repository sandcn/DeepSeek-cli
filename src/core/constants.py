"""共享常量模块 — 统一颜色外观层（Facade）

集中存放 core 模块中各子模块共享的常量，避免重复定义。
所有 UI 模块通过本模块（或通过 ui/colors.py 外观层）访问颜色常量，
消除颜色值的分散定义。

256 色常量命名规范：
  - 基础色：``COLORNAME_256``（如 ``CYAN_256``、``GREEN_256``），色号对应 xterm-256color 标准
  - 别名：旧 8-bit 名称作为别名保留（如 ``ORANGE_256 = YELLOW_256``），注意 ``DIM_256`` 仅为灰色值别名，不等同于 SGR dim 样式
  - 语义名：``<功能描述>_256``（如 ``RUNNING_256``、``DONE_256``、``PARSING_256``），用于 UI 组件状态标识
  - 背景色：前缀 ``BG_``（如 ``BG_BLUE_256``）
  详见 https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit
"""

__all__: list[str] = [
    # ── 8-bit ANSI 颜色常量 ──
    "GRAY", "WHITE", "CYAN", "GREEN", "YELLOW", "RED", "BLUE", "MAGENTA",
    "BOLD", "DIM", "RESET", "ITALIC", "UNDERLINE",
    "BRIGHT_CYAN", "BRIGHT_GREEN", "BRIGHT_YELLOW", "BRIGHT_BLUE",
    "BRIGHT_MAGENTA", "BRIGHT_RED", "BRIGHT_WHITE", "BRIGHT_BLACK",
    "BG_BLUE", "BG_CYAN", "BG_GREEN", "BG_YELLOW",
    "ORANGE", "TEAL", "PINK", "LAVENDER",
    "SOFT_GREEN", "SOFT_BLUE", "SOFT_YELLOW", "DARK_GRAY",
    # ── 256 色扩展常量 ──
    "GRAY_256", "WHITE_256", "CYAN_256", "GREEN_256", "YELLOW_256",
    "RED_256", "BLUE_256", "MAGENTA_256",
    "BRIGHT_CYAN_256", "BRIGHT_GREEN_256", "BRIGHT_YELLOW_256",
    "BRIGHT_BLUE_256", "BRIGHT_MAGENTA_256", "BRIGHT_RED_256",
    "BRIGHT_WHITE_256", "BRIGHT_BLACK_256",
    "BG_BLUE_256", "BG_CYAN_256", "BG_GREEN_256", "BG_YELLOW_256",
    "ORANGE_256", "TEAL_256", "PINK_256", "LAVENDER_256",
    "SOFT_GREEN_256", "SOFT_BLUE_256", "SOFT_YELLOW_256", "DARK_GRAY_256",
    "DIM_256",
    # ── 语义命名 256 色常量 ──
    "RUNNING_256", "DONE_256", "FAIL_256", "ANSWERING_256",
    "PARSING_256", "BATCH_256", "DIMMER_256", "DIMMEST_256",
    "SUMMARY_DIM_256", "BRANCH_256", "SPINNER_COLOR_256",
]

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

# ── 256 色扩展常量 ────────────────────────────────
# 使用 xterm-256color 标准色号，格式 \033[38;5;Nm（前景）或 \033[48;5;Nm（背景）
# 所有原始 8-bit 常量保持不变，新增 _256 后缀变体

GRAY_256: str = "\033[38;5;242m"        # 242 — 中灰
WHITE_256: str = "\033[38;5;15m"        # 15 — 亮白
CYAN_256: str = "\033[38;5;45m"         # 45 — 青色
GREEN_256: str = "\033[38;5;41m"        # 41 — 绿色
YELLOW_256: str = "\033[38;5;221m"      # 221 — 琥珀黄
RED_256: str = "\033[38;5;196m"         # 196 — 亮红
BLUE_256: str = "\033[38;5;33m"         # 33 — 蓝色
MAGENTA_256: str = "\033[38;5;171m"     # 171 — 紫红
BRIGHT_CYAN_256: str = "\033[38;5;81m"  # 81 — 亮青
BRIGHT_GREEN_256: str = "\033[38;5;47m" # 47 — 亮绿
BRIGHT_YELLOW_256: str = "\033[38;5;227m"  # 227 — 亮黄
BRIGHT_BLUE_256: str = "\033[38;5;75m"  # 75 — 亮蓝
BRIGHT_MAGENTA_256: str = "\033[38;5;177m"  # 177 — 亮紫红
BRIGHT_WHITE_256: str = "\033[38;5;255m"  # 255 — 亮白
BRIGHT_RED_256: str = "\033[38;5;197m"  # 197 — 亮红
BRIGHT_BLACK_256: str = "\033[38;5;239m"  # 239 — 深灰
BG_BLUE_256: str = "\033[48;5;24m"      # 24 — 蓝背景
BG_CYAN_256: str = "\033[48;5;43m"      # 43 — 青背景
BG_GREEN_256: str = "\033[48;5;28m"     # 28 — 绿背景
BG_YELLOW_256: str = "\033[48;5;214m"   # 214 — 琥珀背景
ORANGE_256: str = YELLOW_256             # 别名 = YELLOW_256(221)
TEAL_256: str = CYAN_256                 # 别名 = CYAN_256(45)
PINK_256: str = MAGENTA_256              # 别名 = MAGENTA_256(171)
LAVENDER_256: str = BRIGHT_MAGENTA_256   # 别名 = BRIGHT_MAGENTA_256(177)
SOFT_GREEN_256: str = "\033[38;5;114m"   # 114 — 柔和绿
SOFT_BLUE_256: str = "\033[38;5;110m"    # 110 — 柔和蓝灰
SOFT_YELLOW_256: str = "\033[38;5;222m"  # 222 — 柔和浅黄
DARK_GRAY_256: str = "\033[38;5;237m"    # 237 — 深灰
DIM_256: str = GRAY_256                   # alias — 用灰色(242)替代 dim 样式

# ── 语义命名 256 色常量（用于 UI 组件状态标识）────────────────
# 由 frame_renderer.py 等模块引用，替代硬编码，消除分散定义
RUNNING_256      = "\033[38;5;214m"   # 琥珀色 — 运行中
DONE_256         = "\033[38;5;40m"    # 亮绿 — 完成
FAIL_256         = RED_256            # 红 — 失败
ANSWERING_256    = BRIGHT_BLUE_256    # 浅蓝 — 回答中
PARSING_256      = "\033[38;5;178m"   # 金色 — 解析工具调用
BATCH_256        = "\033[38;5;140m"   # 淡紫 — 批量工具调用
DIMMER_256       = "\033[38;5;240m"   # 暗灰 — 辅助信息
DIMMEST_256      = "\033[38;5;238m"   # 更深灰 — 分隔线/边框
SUMMARY_DIM_256  = "\033[38;5;245m"   # 中灰 — 摘要行次要信息
BRANCH_256       = BRIGHT_BLACK_256   # 灰 — 树状连接线
SPINNER_COLOR_256 = YELLOW_256        # 金色 — spinner 动画

# ── Token 格式化常量 ──

_TOKEN_K_THRESHOLD = 1000


def format_token_k(n: int) -> str:
    """将 token 数格式化为可读形式：≥1000 显示 x.xk，否则原样显示。"""
    if n >= _TOKEN_K_THRESHOLD:
        return f"{n / 1000:.1f}k"
    return str(n)


# ── 文件大小格式化工具 ──────────────────────────────


def human_size(size: int) -> str:
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
