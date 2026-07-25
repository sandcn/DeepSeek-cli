"""components 包 — 组件层，从 _components.py 拆包。

将原 _components.py 按单一职责拆分为以下子模块：

  _base.py            — TuiComponent 基类 + _estimate_content_lines
  _user_msg.py        — UserMsgBlock
  _thinking.py        — ThinkingBlock
  _answer.py          — AnswerBlock
  _tool_output.py     — ToolOutputBlock
  _tool_summary.py    — ToolSummaryBlock
  _error.py           — ErrorBlock
  _notification.py    — NotificationBlock
  _write_line.py      — WriteLineBlock
  （已移除的死组件：StatusLine / InputLine / CompletionPopup / SelectionMenu / TreeView / parse_markup / render_markup，2026-07-24~25 清理）

兼容 re-export：BottomBarProtocol（定义在 _protocols.py）

模块采用懒加载模式（LazyLoader），首次属性访问时才执行实际 import，
降低应用启动时的模块加载开销。
"""

from __future__ import annotations

from .._lazy import LazyLoader

# ═══════════════════════════════════════════════════════════
# 懒加载模块代理
# ═══════════════════════════════════════════════════════════

_base_mod = LazyLoader("src.tui.components._base")
_user_msg_mod = LazyLoader("src.tui.components._user_msg")
_thinking_mod = LazyLoader("src.tui.components._thinking")
_answer_mod = LazyLoader("src.tui.components._answer")
_tool_output_mod = LazyLoader("src.tui.components._tool_output")
_tool_summary_mod = LazyLoader("src.tui.components._tool_summary")
_error_mod = LazyLoader("src.tui.components._error")
_notification_mod = LazyLoader("src.tui.components._notification")
_write_line_mod = LazyLoader("src.tui.components._write_line")
_cost_mod = LazyLoader("src.tui.components._cost")
_splash_mod = LazyLoader("src.tui.components._splash")
_box_mod = LazyLoader("src.tui.components._box")
_panel_mod = LazyLoader("src.tui.components._panel")
_separator_mod = LazyLoader("src.tui.components._separator")
_spinner_mod = LazyLoader("src.tui.components._spinner")
_progress_mod = LazyLoader("src.tui.components._progress")
_table_mod = LazyLoader("src.tui.components._table")
_protocols_mod = LazyLoader("src.tui.consumer.protocols")


# ═══════════════════════════════════════════════════════════
# 符号到懒加载模块的映射（供 __getattr__ 使用）
# ═══════════════════════════════════════════════════════════

_SYMBOL_MAP: dict[str, LazyLoader] = {
    # ── 基类 ─────────────────────────────────────
    "Widget": _base_mod,
    "_estimate_content_lines": _base_mod,
    "TuiComponent": _base_mod,

    # ── 聊天域组件（业务相关）────────────────────
    "UserMsgBlock": _user_msg_mod,
    "ThinkingBlock": _thinking_mod,
    "AnswerBlock": _answer_mod,
    "ToolOutputBlock": _tool_output_mod,
    "ToolSummaryBlock": _tool_summary_mod,
    "ErrorBlock": _error_mod,
    "NotificationBlock": _notification_mod,
    "WriteLineBlock": _write_line_mod,

    # ── 通用框架组件（可独立复用）─────────────────
    "BoxStyle": _box_mod,
    "Box": _box_mod,
    "Panel": _panel_mod,
    "Separator": _separator_mod,
    "Spinner": _spinner_mod,
    "ProgressBar": _progress_mod,
    "Table": _table_mod,
    "SplashScreen": _splash_mod,

    # ── 协议 ─────────────────────────────────────
    "BottomBarProtocol": _protocols_mod,
}


def __getattr__(name: str):
    """模块级 __getattr__ — 从对应懒加载模块延迟解析符号。

    当 ``from src.tui.components import XXX`` 执行时，如果 XXX 不是模块的
    直接属性，Python 会调用此函数，从 _SYMBOL_MAP 中查找对应的
    LazyLoader 并执行延迟导入。

    Raises:
        AttributeError: 符号不在 __all__ 中时抛出。
    """
    loader = _SYMBOL_MAP.get(name)
    if loader is not None:
        return getattr(loader, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """支持 dir() 列出所有导出符号。"""
    return sorted(__all__)


# ═══════════════════════════════════════════════════════════
# __all__ — 公开 API 清单
# ═══════════════════════════════════════════════════════════

__all__ = [
    # ── 基类 ─────────────────────────────────────
    "Widget",
    "TuiComponent",
    "_estimate_content_lines",

    # ── 聊天域组件（业务相关）────────────────────
    "UserMsgBlock",
    "ThinkingBlock",
    "AnswerBlock",
    "ToolOutputBlock",
    "ToolSummaryBlock",
    "ErrorBlock",
    "NotificationBlock",
    "WriteLineBlock",

    # ── 通用框架组件（可独立复用）─────────────────
    "BoxStyle", "Box",
    "Panel",
    "Separator",
    "Spinner",
    "ProgressBar",
    "Table",
    "SplashScreen",

    # ── 协议 ─────────────────────────────────────
    "BottomBarProtocol",
]
