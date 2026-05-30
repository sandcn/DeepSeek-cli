"""常量、枚举与 Rich Style 定义。

纯数据模块，零运行时依赖（仅导入 rich/wcwidth）。
"""

from __future__ import annotations

from enum import Enum, IntEnum

from rich.style import Style

# ── 主 Agent 标识 ───────────────────────────────────────
_MAIN_LABEL = "assistant"
_MAIN_SOURCE = "agent"

# ── Rich Style 常量（供 OutputAdapter + Rich 渲染管线使用） ──
_STYLE_DIM = Style(dim=True)
_STYLE_DIM_GREY = Style(dim=True, color="grey58")
_STYLE_FAIL = Style(color="red")
_STYLE_WARN = Style(color="orange1")
_STYLE_SUCCESS = Style(color="green")
_STYLE_PARSE = Style(color="gold1")
_STYLE_PARSE_DIM = Style(color="grey74")
_STYLE_USER = Style(color="deep_sky_blue1")
_STYLE_BOLD = Style(bold=True)
_STYLE_ERROR = Style(color="red", bold=True)

_THINKING_HEADER = "\n  ── ◆ 思考 ◆ ──\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 40 + "\n"

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
    REASONING      = 0   # (0, text: str)
    CONTENT        = 1   # (1, text: str)
    PHASE_DONE     = 2   # (2, phase: str)
    TOOL_OUTPUT    = 6   # (6, text: str)
    TOOL_SUMMARY   = 7   # (7, successful: tuple, failed: tuple)
    USER_MSG       = 8   # (8, text: str)
    PARSE_INFO     = 9   # (9, tool_names: str, tokens: int, elapsed: float)
    CMD_OUTPUT     = 10  # (10, text: str)
    NOTIFICATION   = 11  # (11, text: str)
    WRITE_LINE     = 12  # (12, text: str)
    DISPLAY_MSGS   = 13  # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # (15,) — 工具失败计数+1
    ERROR          = 16  # (16, message: str) — 系统错误（红色 ◆ 样式）


# ═══════════════════════════════════════════════════════════
# _ReasoningState — 推理渲染器状态机
# ═══════════════════════════════════════════════════════════

class _ReasoningState(Enum):
    """推理渲染器状态机，替代两个布尔值。

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


# ── 辅助函数 ───────────────────────────────────────────

def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。"""
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)
