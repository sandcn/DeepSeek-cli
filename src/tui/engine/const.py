"""chat_ui 常量模块 — RenderCommand 枚举、Rich Style 常量、推理状态机。

Layer 0 — 无内部依赖，被所有上层模块引用。

RenderCommand 分层（v1.3+）：
  - 框架通用命令 → engine/commands.py  FrameworkCommand
  - 聊天域命令   → consumer/chat_commands.py  ChatCommand
  - RenderCommand 保留为向后兼容别名（含全部 20 个枚举值）
"""

from __future__ import annotations

from enum import IntEnum

from rich.style import Style

# ── 命令枚举分层导入 ────────────────────────────────────
from .commands import FrameworkCommand  # noqa: F401 — 重导出供外部使用
# ChatCommand 由 consumer/chat_commands.py 定义（Layer 1），
# 不从 engine/const.py（Layer 0）重导出以避免循环依赖。
# 使用者通过 src.tui.consumer 导入 ChatCommand。

# ── 主 Agent 标识 ───────────────────────────────────────
# @deprecated — 使用 ChatConfig.main_label 替代，v1.3+ 移除
# 详情参见 src/tui/consumer/chat_config.py
_MAIN_LABEL = "assistant"
# @deprecated — 使用 ChatConfig.main_source 替代，v1.3+ 移除
# 详情参见 src/tui/consumer/chat_config.py
_MAIN_SOURCE = "agent"

# ── Rich Style 常量（供 OutputAdapter + Rich 渲染管线使用） ──
# @deprecated — 使用 StyleSheet.get("dim") 替代，v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_DIM = Style(dim=True)
# @deprecated — 使用 StyleSheet.get("error") 近似替代（tui.core.Style fg=196），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_FAIL = Style(color="red")
# @deprecated — 使用 StyleSheet.get("warn") 替代（tui.core.Style fg=220），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_WARN = Style(color="orange1")
# @deprecated — 使用 StyleSheet.get("success") 替代（tui.core.Style fg=47），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_SUCCESS = Style(color="green")
# @deprecated — 使用 StyleSheet.get("error") 替代（tui.core.Style fg=196, bold=True），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_ERROR = Style(color="red", bold=True)
# @deprecated — 使用 StyleSheet.get("bold") 替代，v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_BOLD = Style(bold=True)

# ── 渐变色增强 Style（ChatUI 第三阶段美化，步骤 7） ──
# @deprecated — 使用 tui.core.Style(fg=196, bold=True) 替代，v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_ERROR_GRADIENT = Style(color="bright_red", bold=True)        # 亮红增强
# @deprecated — 使用 StyleSheet.get("user_icon") 替代（tui.core.Style fg=81），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_USER_GRADIENT = Style(color="cyan", bold=True)               # 青色渐变
# @deprecated — 使用 StyleSheet.get("neon") 替代（tui.core.Style fg=51），v1.3+ 移除
# 详情参见 src/tui/core/style.py StyleSheet 注册表
_STYLE_NOTIFICATION_GRADIENT = Style(color="bright_green", bold=True)  # 亮绿增强

# ── 桥接：注册 tui.core.Style 等效样式到 StyleSheet（供新组件使用） ──
# 使用 __all__ 约定 + 模块加载时自动注册，确保新旧两套样式系统共存。
# 旧代码继续使用 rich.style.Style 常量，新代码使用 StyleSheet.get() 获取 tui.core.Style。
def _register_tui_styles() -> None:
    """将常用样式注册到 tui.core.StyleSheet。（延迟导入避免模块加载循环）"""
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

_register_tui_styles()

# @deprecated — 使用 ChatConfig.thinking_header 替代，v1.3+ 移除
# 详情参见 src/tui/consumer/chat_config.py
_THINKING_HEADER = "\n  ─ 思考 ─\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 25 + "\n"

# ── 统一错误消息截断长度 ─────────────────────────────
# @deprecated — 使用 TuiConfig.max_error_length 替代，v1.3+ 移除
# 详情参见 src/tui/config.py
_MAX_ERROR_LENGTH = 200
# @deprecated — 使用 ChatConfig.max_output_len 替代，v1.3+ 移除
# 详情参见 src/tui/consumer/chat_config.py
_MAX_OUTPUT_LEN = 10000  # 工具输出最大长度（字符），与 _MAX_ERROR_LENGTH 对齐

# ── render 线程刷新间隔 ─────────────────────────────────
# @deprecated — 使用 TuiConfig.render_interval 替代，v1.3+ 移除
# 详情参见 src/tui/config.py
_RENDER_INTERVAL = 0.1  # 100ms = 10Hz

# ── 单次 drain_queue 最大批处理命令数 ───────────────────
# @deprecated — 使用 TuiConfig.max_batch_size 替代，v1.3+ 移除
# 详情参见 src/tui/config.py
_MAX_BATCH_SIZE = 50  # 钳位值，防止单帧处理过多命令导致 UI 冻结

# ── 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线） ──
# 用于队列满/render 崩溃等无法通过正常渲染管线输出的场景。
# 提取为常量而非散落硬编码，确保可维护性。
_ANSI_RED = "\033[31m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RESET = "\033[0m"
_ANSI_CURSOR_BOTTOM = "\033[9999;1H"


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。

    分层分类（v1.3+）：
      [框架通用] — 参见 FrameworkCommand（engine/commands.py）
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


# ── drain 锁超时 ─────────────────────────────────────
# @deprecated — 使用 TuiConfig.drain_lock_timeout 替代，v1.3+ 移除
# 详情参见 src/tui/config.py
_DRAIN_LOCK_TIMEOUT = 0.1  # drain_queue 获取输出锁的超时（秒），与 _RENDER_INTERVAL (0.1) 对齐，避免一方修改引入竞态
