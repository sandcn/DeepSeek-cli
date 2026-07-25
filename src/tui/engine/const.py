"""chat_ui 常量模块 — RenderCommand/FrameworkCommand 枚举、工具函数、ANSI 转义序列。

Layer 0 — 无内部依赖，被所有上层模块引用。

RenderCommand 分层（v1.3+）：
  - 框架通用命令 → FrameworkCommand（本模块内定义）
  - 聊天域命令   → consumer/chat_commands.py  ChatCommand
  - RenderCommand 保留为向后兼容别名（含全部 20 个枚举值）

工具函数（v2.0 合并自 engine/utils.py）：
  - _truncate_msg / _cmd_name / _emergency_write
"""

from __future__ import annotations

import sys
import threading
from enum import IntEnum

# ── 命令枚举分层导入 ────────────────────────────────────
# ── 框架通用命令枚举 ────────────────────────────────────
class FrameworkCommand(IntEnum):
    """框架层通用渲染命令 — 与聊天域无关，可被任何 TUI 应用复用。"""
    NOTIFICATION = 11
    WRITE_LINE = 12
    ERROR = 16
    SUBAGENT_FRAME = 18
    SPLASH = 19
# ChatCommand 由 consumer/chat_commands.py 定义（Layer 1），
# 不从 engine/const.py（Layer 0）重导出以避免循环依赖。
# 使用者通过 src.tui.consumer 导入 ChatCommand。

# ── 桥接：注册 tui.core.Style 等效样式到 StyleSheet（供新组件使用） ──
# 使用 __all__ 约定 + 惰性注册（由 TuiEngine.start() 或 factory 显式调用），
# 消除模块加载时自动注册的副作用，确保初始化时机可控。
# 旧代码继续使用 rich.style.Style 常量，新代码使用 StyleSheet.get() 获取 tui.core.Style。

_register_tui_styles_lock = threading.Lock()
_register_tui_styles_done = False

def register_tui_styles() -> None:
    """将常用样式注册到 tui.core.StyleSheet。（延迟导入避免模块加载循环）

    幂等设计：多次调用只注册一次。线程安全。
    """
    global _register_tui_styles_done
    if _register_tui_styles_done:
        return
    with _register_tui_styles_lock:
        if _register_tui_styles_done:
            return
        from ..core.style import StyleSheet as _SS, Style as _TS
        _SS.register_many({
            # style.py 已预注册的样式不再重复注册（dim/bold/italic/underline/bold_dim/dim_italic/tree_branch/tree_leaf）
            "bold_italic": _TS(bold=True, italic=True),
            # 语义色（从 THEME 读取色号，兜底硬编码）
            "error": _TS(fg=196, bold=True),
            "success": _TS(fg=47),
            "warn": _TS(fg=220),
            "info": _TS(fg=45),
            "muted": _TS(fg=244),
            "border_breath": _TS(fg=23),
            # 差异渲染语义色
            "diff_add": _TS(fg=41),
            "diff_del": _TS(fg=196),
            "diff_ctx": _TS(fg=244),
            # 消息角色图标色
            "user_icon": _TS(fg=81),
            "asst_icon": _TS(fg=47),
            "tool_icon": _TS(fg=220),
            # 工具输出文本色
            "tool_txt": _TS(fg=242),
            # 装饰色
            "separator": _TS(fg=239),
            "highlight": _TS(fg=45),
            "accent": _TS(fg=221),
            "deco": _TS(fg=242),
            # 渲染效果语义色
            "neon": _TS(fg=51, bold=True),
        })
        _register_tui_styles_done = True

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
      [框架通用] — 参见 FrameworkCommand（上方定义）
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


# ═══════════════════════════════════════════════════════════
# 工具函数（v2.0 合并自 engine/utils.py）
# ═══════════════════════════════════════════════════════════

def _truncate_msg(msg: str, max_len: int) -> str:
    """截断超长消息，追加"..."标记（尾部安全）。

    若 `msg` 长度超过 `max_len`，取前 `max_len` 字符并追加 "..."。
    若未超过，原样返回。
    """
    if len(msg) > max_len:
        return msg[:max_len] + "..."
    return msg


def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。

    返回枚举名的 `name` 属性（如 0→"REASONING"），
    未知 ID 时回退为字符串格式的整数值（如 "255"）。
    """
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


def _emergency_write(text: str, stream: str = "stdout") -> None:
    """紧急输出 — 绕过 OutputAdapter 直写终端。

    此函数有意使用 sys.__stdout__ / sys.__stderr__ 而非 OutputAdapter，
    这是设计上的刻意选择（NOT a bug）：
      - 这是紧急回退路径，绕过所有渲染管线（OutputAdapter / Rich / render_lock）
      - 用于 render 线程崩溃、队列满等无法通过正常路径输出终端的场景
      - 若经由 OutputAdapter 写入，在 render 线程已崩溃时可能死锁或丢失消息

    仅在以下场景使用：
      - render 线程崩溃通知（_handle_render_crash → _emergency_write）
      - 队列满降级通知（finally 排空丢弃计数）
      不适用于正常渲染路径。

    不持有 output_lock，不经过 Rich/OutputAdapter 处理。

    Args:
        text: 要写入的文本。
        stream: 输出流，'stdout' 或 'stderr'。
    """
    f = sys.__stdout__ if stream == "stdout" else sys.__stderr__
    f.write(text)
    f.flush()
