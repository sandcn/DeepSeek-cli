"""共享常量模块 — 统一颜色外观层（Facade）

集中存放 core 模块中各子模块共享的常量，避免重复定义。
所有 UI 模块通过本模块（或通过 ui/colors.py 外观层）访问颜色常量，
消除颜色值的分散定义。

颜色常量：本模块只保留 CLI（非 TUI）使用的 8 色 ANSI 常量。TUI 的颜色
体系（真彩 24-bit、xterm-256 调色板索引）在 ``src.tui.core.color`` /
``src.tui.core.style``——2026-10-25 用户需求「TUI 颜色全部真彩色」后，
原 ``*_256`` 系列常量（256 色输出）已删除。
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
    # ── 内部模型调用标签 ──
    "STREAM_LABEL_SUMMARIZE", "STREAM_LABEL_TITLE", "STREAM_LABEL_LOOP_PROMPT",
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

# ── 内部模型调用标签 ──────────────────────────────────

#: 上下文压缩摘要调用的流式标签（单一真源，api 与 core 共同引用）。
#:
#: ``api.model_async`` 以该 label 发起**流式**摘要请求（``silent``）；core 侧
#: 据此把该调用排除出主 Agent 上下文使用率统计——摘要输出写回检查点而非主
#: 消息列表，输入也不是主对话（见 ``core.context_manager`` 的
#: ``update_streaming_usage`` / ``update_real_prompt_usage``）。
STREAM_LABEL_SUMMARIZE = "summarize"

#: 会话标题生成调用的流式标签（单一真源，api 与 core 共同引用）。
#:
#: 标题生成是**辅助请求**：输出写回会话标题而非主消息列表，输入也不是主对话
#: ——core 侧据此把该调用排除出 ``main · N%`` 上下文使用率统计，TUI 也不渲染
#: 其流式内容（调用方另传 ``silent=True``）。
STREAM_LABEL_TITLE = "session-title"

#: ``/loop`` 续跑提词生成调用的流式标签（单一真源，api 与 core 共同引用）。
#:
#: 与标题生成同为**辅助请求**：输出写回循环控制状态而非主消息列表，输入也不是
#: 主对话——core 侧据此把该调用排除出 ``main · N%`` 上下文使用率统计，TUI 也不
#: 渲染其流式内容（调用方另传 ``silent=True``）。
STREAM_LABEL_LOOP_PROMPT = "loop-prompt"

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
