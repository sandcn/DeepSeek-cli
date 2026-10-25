"""统一常量定义 — ANSI 紧急路径常量 + 语义色槽位 + 渲染命令协议 re-export。

分层（2026-10 架构演进）：
  - **渲染命令协议**（``RenderCommand`` / ``RenderCmd`` 族 / ``CONTENT_COMMANDS``）
    已下沉到框架层 ``src/tui/ink/command.py``——那份协议是 ``InkSession`` 队列
    的载荷类型，属于框架而非应用常量。本模块保留 re-export，既有导入路径
    （``from src.tui._const import RenderCommand``）零改动。
  - 本模块自身只保留**表现层常量**：紧急路径 ANSI 序列、语义色槽位表，
    以及两个纯函数工具（``is_agent_source`` / ``truncate_error_message``）。

Layer 0 —— 除框架内零依赖的 ``ink.command`` 外无内部依赖。
"""

from __future__ import annotations

from collections.abc import Mapping

from ._command_protocol import (  # noqa: F401  （显式重导出，见 __all__）
    BgBashCountCmd,
    ChatCommand,
    ClearMsgsCmd,
    CompactionCmd,
    CONTENT_COMMANDS,
    ContentCmd,
    DisplayMsgsCmd,
    ErrorCmd,
    FrameworkCommand,
    MainPhaseCmd,
    NotificationCmd,
    ParseInfoCmd,
    PhaseDoneCmd,
    ReasoningCmd,
    RenderCmd,
    RenderCommand,
    SplashCmd,
    SubagentFrameCmd,
    ToolCloseCmd,
    ToolCountDecCmd,
    ToolCountIncCmd,
    ToolFailIncCmd,
    ToolMarkdownCmd,
    ToolOpenCmd,
    ToolOutputCmd,
    ToolSummaryCmd,
    UserMsgCmd,
    WriteLineCmd,
)

__all__ = [
    # ── 命令协议（re-export，真源在 ink/command.py）──
    "RenderCommand",
    "FrameworkCommand",
    "ChatCommand",
    "RenderCmd",
    "ReasoningCmd",
    "ContentCmd",
    "PhaseDoneCmd",
    "ToolOutputCmd",
    "ToolMarkdownCmd",
    "ToolSummaryCmd",
    "ToolOpenCmd",
    "ToolCloseCmd",
    "UserMsgCmd",
    "ParseInfoCmd",
    "NotificationCmd",
    "WriteLineCmd",
    "DisplayMsgsCmd",
    "ClearMsgsCmd",
    "ToolCountIncCmd",
    "ToolFailIncCmd",
    "ErrorCmd",
    "ToolCountDecCmd",
    "SubagentFrameCmd",
    "SplashCmd",
    "MainPhaseCmd",
    "BgBashCountCmd",
    "CompactionCmd",
    "CONTENT_COMMANDS",
    # ── 本模块常量/工具 ──
    "_CLEAR_PARSE_LINE",
    "ANSI_EMERGENCY_RED",
    "ANSI_EMERGENCY_YELLOW",
    "ANSI_EMERGENCY_RESET",
    "ANSI_EMERGENCY_CURSOR_BOTTOM",
    "_SEMANTIC_COLOR",
    "is_agent_source",
    "truncate_error_message",
]

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE: int = -1

# ═══════════════════════════════════════════════════════════
# 紧急路径 ANSI 转义序列（直写终端，绕过渲染管线）
# ═══════════════════════════════════════════════════════════
# 2026-10-25（用户需求：TUI 颜色全部真彩色）：原 31/33 基础色改为 24-bit
# 真彩（xterm 9/11 亮红/亮黄），保持紧急提示醒目。
ANSI_EMERGENCY_RED: str = "\033[38;2;255;0;0m"
ANSI_EMERGENCY_YELLOW: str = "\033[38;2;255;255;0m"
ANSI_EMERGENCY_RESET: str = "\033[0m"
ANSI_EMERGENCY_CURSOR_BOTTOM: str = "\033[9999;1H"

#: 语义色槽位表（语义名 → 256 色号）— 样式/颜色的单一真源。
#: 覆盖 ``app/_theme.py`` 的 Palette(dark) 与历史 ``_COLOR_*`` 共有的语义色。
#: 「一切皆插件」：数据上移为表现层数据注册表（``presentation_data`` →
#: ``semantic_color`` 表），本表为实时委托（可按 Patch/Overlay 覆盖/禁用；
#: 禁用/缺席时回退下方内置默认值）。
_SEMANTIC_COLOR_FALLBACK: dict = {
    "accent": 45,
    "deep_cyan": 32,
    "dim": 242,
    "sep": 237,
    "time": 110,
    "token": 68,
    "speed": 214,
    "tool_ok": 41,
    "tool_fail": 196,
    "select_bg": 236,
    "select_fg": 15,
    "border": 23,
    "placeholder": 238,
    "running": 214,
    "done": 40,
    "fail": 196,
    "answering": 75,
    "parsing": 178,
    "batch": 140,
    "dimmer": 240,
    "dimmest": 238,
    "summary_dim": 245,
    "branch": 239,
}


class _SemanticColor(Mapping):
    """语义色只读映射 — 实时委托 ``presentation_data.semantic_color`` 表。"""

    def _data(self) -> dict:
        from ..presentation_data import semantic_colors

        return semantic_colors() or _SEMANTIC_COLOR_FALLBACK

    def __getitem__(self, key):
        try:
            return self._data()[key]
        except KeyError:
            return _SEMANTIC_COLOR_FALLBACK[key]

    def __iter__(self):
        return iter(_SEMANTIC_COLOR_FALLBACK)

    def __len__(self):
        return len(_SEMANTIC_COLOR_FALLBACK)


_SEMANTIC_COLOR: Mapping = _SemanticColor()


def is_agent_source(source: str | None) -> bool:
    """判断事件 source 是否为 agent 来源（唯一真源，供过滤策略收敛引用）。

    语义：``source == "agent"`` 或 ``source.startswith("agent-")``；None 视为
    非 agent。
    """
    if source is None:
        return False
    return source == "agent" or source.startswith("agent-")


def truncate_error_message(text: str | None, max_length: int) -> str:
    """截断错误消息至 max_length 字符（含省略号，唯一真源）。

    超长时保留前 ``max_length-3`` 字符并追加 ``"..."``，使结果长度恰为
    ``max_length``（``max_length<=3`` 时退化为直接截断，不加省略号）；
    未超长时原样返回。
    """
    if text is None:
        return ""
    if max_length <= 0:
        return ""
    if len(text) <= max_length:
        return text
    if max_length <= 3:
        return text[:max_length]
    return text[:max_length - 3] + "..."
