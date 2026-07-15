"""chat_ui 常量模块 — RenderCommand 枚举、Rich Style 常量、推理状态机。

Layer 0 — 无内部依赖，被所有上层模块引用。
"""

from __future__ import annotations

from enum import IntEnum

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

# ── 渐变色增强 Style（ChatUI 第三阶段美化，步骤 7） ──
_STYLE_ERROR_GRADIENT = Style(color="bright_red", bold=True)        # 亮红增强
_STYLE_USER_GRADIENT = Style(color="cyan", bold=True)               # 青色渐变
_STYLE_NOTIFICATION_GRADIENT = Style(color="bright_green", bold=True)  # 亮绿增强

# ── 桥接：注册 tui.core.Style 等效样式到 StyleSheet（供新组件使用） ──
# 使用 __all__ 约定 + 模块加载时自动注册，确保新旧两套样式系统共存。
# 旧代码继续使用 rich.style.Style 常量，新代码使用 StyleSheet.get() 获取 tui.core.Style。
def _register_tui_styles() -> None:
    """将常用样式注册到 tui.core.StyleSheet。（延迟导入避免模块加载循环）"""
    from ..core.style import StyleSheet as _SS, Style as _TS
    _SS.register_many({
        "dim": _TS(dim=True),
        "bold": _TS(bold=True),
        "italic": _TS(italic=True),
        "underline": _TS(underline=True),
        "bold_italic": _TS(bold=True, italic=True),
        "dim_italic": _TS(dim=True, italic=True),
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
        "neon": _TS(fg=51),
        # TreeView 组件语义色
        "tree_branch": _TS(fg=239),
        "tree_leaf": _TS(fg=45),
    })

_register_tui_styles()

_THINKING_HEADER = "\n  ─ 思考 ─\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 25 + "\n"

# ── 统一错误消息截断长度 ─────────────────────────────
_MAX_ERROR_LENGTH = 200
_MAX_OUTPUT_LEN = 10000  # 工具输出最大长度（字符），与 _MAX_ERROR_LENGTH 对齐

# ── render 线程刷新间隔 ─────────────────────────────────
_RENDER_INTERVAL = 0.1  # 100ms = 10Hz

# ── 单次 drain_queue 最大批处理命令数 ───────────────────
_MAX_BATCH_SIZE = 50  # 钳位值，防止单帧处理过多命令导致 UI 冻结

# ── 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线） ──
# 用于队列满/render 崩溃等无法通过正常渲染管线输出的场景。
# 提取为常量而非散落硬编码，确保可维护性。
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"
_ANSI_CURSOR_BOTTOM = "\033[9999;1H"

# ── inline 模式 ANSI 序列 ────────────────────────────
# 用于底部栏 inline 渲染（非全屏逐行覆盖模式），
# 替代 DECSTBM/SCOSC/DECRC/SU/SD 等全屏序列。
_ANSI_UP = "\033[A"            # 光标上移 1 行
_ANSI_CLEAR_LINE = "\r\033[K"  # 移至行首 + 清除当前行


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。
    """
    REASONING     = 0   # (0, text: str)
    CONTENT       = 1   # (1, text: str)
    PHASE_DONE    = 2   # (2, phase: str)
    TOOL_OUTPUT   = 6   # (6, text: str)
    TOOL_SUMMARY  = 7   # (7, successful: tuple, failed: tuple)
    USER_MSG      = 8   # (8, text: str)
    PARSE_INFO    = 9   # (9, tool_names: str, tokens: int, elapsed: float)
    NOTIFICATION  = 11  # (11, text: str)
    WRITE_LINE    = 12  # (12, text: str)
    DISPLAY_MSGS  = 13  # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # (15,) — 工具失败计数+1
    ERROR          = 16  # (16, message: str) — 系统错误（红色 ! 样式）
    TOOL_COUNT_DEC     = 17  # (17,) — 工具计数-1
    SUBAGENT_FRAME     = 18  # (18, frame_lines: tuple[str]) — SubAgent 面板帧
    SPLASH             = 19  # (19,) — 启动品牌屏
    MAIN_PHASE         = 20  # (20, phase: str) — 主Agent模型阶段变更


# ── drain 锁超时 ─────────────────────────────────────
_DRAIN_LOCK_TIMEOUT = 0.1  # drain_queue 获取输出锁的超时（秒），与 _RENDER_INTERVAL (0.1) 对齐，避免一方修改引入竞态
