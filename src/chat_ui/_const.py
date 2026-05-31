"""chat_ui 常量模块 — RenderCommand 枚举、Rich Style 常量、推理状态机。

Layer 0 — 无内部依赖，被所有上层模块引用。
"""

from __future__ import annotations

from enum import Enum, IntEnum

from rich.style import Style

# ── 主 Agent 标识 ───────────────────────────────────────
_MAIN_LABEL = "assistant"
_MAIN_SOURCE = "agent"

# ── Rich Style 常量（供 OutputAdapter + Rich 渲染管线使用） ──
_STYLE_DIM = Style(dim=True)
_STYLE_FAIL = Style(color="red")
_STYLE_WARN = Style(color="orange1")
_STYLE_SUCCESS = Style(color="green")
_STYLE_ERROR = Style(color="red", bold=True)
_STYLE_BOLD = Style(bold=True)

_THINKING_HEADER = "\n  ─ 思考 ─\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 25 + "\n"

# ── Reader 线程刷新间隔 ─────────────────────────────────
_READER_INTERVAL = 0.1  # 100ms = 10Hz


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。

    注意：值 3-5 为已废弃命令保留位（TOOL_STARTED/TOOL_DONE/
    PARSE_INFO_DONE），不重用以免产生歧义。
    """
    REASONING     = 0   # (0, text: str)
    CONTENT       = 1   # (1, text: str)
    PHASE_DONE    = 2   # (2, phase: str)
    TOOL_OUTPUT   = 6   # (6, text: str)
    TOOL_SUMMARY  = 7   # (7, successful: tuple, failed: tuple)
    USER_MSG      = 8   # (8, text: str)
    PARSE_INFO    = 9   # (9, tool_names: str, tokens: int, elapsed: float)
    CMD_OUTPUT    = 10  # (10, text: str)
    NOTIFICATION  = 11  # (11, text: str)
    WRITE_LINE    = 12  # (12, text: str)
    DISPLAY_MSGS  = 13  # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # (15,) — 工具失败计数+1
    ERROR          = 16  # (16, message: str) — 系统错误（红色 ! 样式）


# ═══════════════════════════════════════════════════════════
# _ReasoningState — 推理渲染器状态机
# ═══════════════════════════════════════════════════════════

class _ReasoningState(Enum):
    """推理渲染器状态机，替代两个布尔值（thinking_header_printed + reasoning_closed）。

    状态转换：
      INACTIVE → 首个推理块到达 → ACTIVE（创建渲染器+打印标题）
      ACTIVE   → close_reasoning() → CLOSED（写入分隔线+关闭渲染器）
      INACTIVE → close_reasoning() → CLOSED（推理块从未到达即关闭）
      CLOSED   → reopen_reasoning() → INACTIVE（二次推理重新打开）
      CLOSED   → 其他转换不生效（幂等）
    """
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


# ── ChatUIConsumer 辅助 ───────────────────────────────

def _build_render_dispatch() -> dict[int, tuple[str, tuple[int, ...]]]:
    """构建渲染命令分发表（模块级函数，类定义时即初始化）。

    注：直接在类体内写字典字面量亦可，提取为独立函数仅为
    提升可读性——避免 ~20 行的字典字面量打断类属性声明区。
    """
    R = RenderCommand
    return {
        R.REASONING:      ("_do_reasoning",       (1,)),
        R.CONTENT:        ("_do_content",         (1,)),
        R.PHASE_DONE:     ("_do_phase_done",      (1,)),
        R.TOOL_OUTPUT:    ("_do_tool_output",     (1,)),
        R.TOOL_SUMMARY:   ("_do_tool_summary",    (1, 2)),
        R.USER_MSG:       ("_do_user_message",    (1,)),
        R.PARSE_INFO:     ("_do_parse_info",      (1, 2, 3)),
        R.CMD_OUTPUT:     ("_do_cmd_output",      (1,)),
        R.NOTIFICATION:   ("_do_notification",    (1,)),
        R.WRITE_LINE:     ("_do_write_line",      (1,)),
        R.DISPLAY_MSGS:   ("_do_display_messages", (1, 2)),
        R.TOOL_COUNT_INC: ("_do_tool_count_inc",  ()),
        R.TOOL_FAIL_INC:  ("_do_tool_fail_inc",   ()),
        R.ERROR:          ("_do_error",           (1,)),
    }


def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。

    返回枚举名的 `name` 属性（如 0→"REASONING"），
    未知 ID 时回退为字符串格式的整数值（如 "255"）。
    """
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)
