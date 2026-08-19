"""统一常量定义 — RenderCommand + FrameworkCommand + ChatCommand + ANSI 紧急路径常量。

Layer 0 — 无内部依赖，被所有 TUI 模块引用。
整合旧 engine/const.py、engine/commands.py、consumer/chat_commands.py 三套枚举到一个文件。

保留确认（2026-07-31 方向F）：``FrameworkCommand``/``ChatCommand`` 为
``RenderCommand`` 别名，被 ``src/tui/__init__.py`` 与 ``src/tui/consumer/__init__.py``
re-export（公共 API 约束）；``RenderCommand`` 22 个枚举值均有对应 ``RenderCmd``
dataclass 且全部被分发使用，**无未引用枚举**。别名保留不删。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE: int = -1

# ═══════════════════════════════════════════════════════════
# 紧急路径 ANSI 转义序列（直写终端，绕过 Rich 管线）
# ═══════════════════════════════════════════════════════════
ANSI_EMERGENCY_RED: str = "\033[31m"
ANSI_EMERGENCY_YELLOW: str = "\033[33m"
ANSI_EMERGENCY_RESET: str = "\033[0m"
ANSI_EMERGENCY_CURSOR_BOTTOM: str = "\033[9999;1H"


# ═══════════════════════════════════════════════════════════
# ANSI 颜色常量（256 色体系）— 唯一真源（方向F 步骤12 收敛）
# ═══════════════════════════════════════════════════════════
# 收敛 _screen.py（底栏 _COLOR_*）与 _subagent_panel.py（面板 _C_*）两套颜色
# 到本 Layer 0 模块单一真源；_screen.py 保留 re-export（bottom_bar 导入路径不变），
# _subagent_panel.py 改为模块级从本模块导入。值与原定义完全一致，零行为变化。
#
# 方向3 步骤15（样式/颜色单一真源）：新增 ``_SEMANTIC_COLOR`` 槽位表——
# 语义色名 → 256 色号的唯一真源。``_COLOR_*``/``_C_*`` 中与
# ``app/_theme.py`` Palette(dark) 共有的语义色由槽位派生（防漂移），
# 其余无槽位字面量保留原值；``app/_theme.py`` 暗色 Palette 各槽与
# ``core/style.py`` StyleSheet 的 "error" 亦引用本槽位表（零视觉回归）。

#: 语义色槽位表（语义名 → 256 色号）— 样式/颜色单一真源。
#: 覆盖 Palette(dark) 与 _COLOR_* 共有的语义色；值与原硬编码完全一致。
_SEMANTIC_COLOR: dict[str, int] = {
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
}
# ★ 标准 React Ink 组件化（2026-08-05）：原 _COLOR_*（ANSI 前景序列）与 _C_*
# （ANSI 面板色）常量已删除——生产渲染统一用 core/style.py Style（fg 色号），
# 色号从 _SEMANTIC_COLOR 槽位表解析（零视觉回归）。ANSI_EMERGENCY_*（紧急
# 路径）保留。


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（向后兼容，含全部 22 个值）
# ═══════════════════════════════════════════════════════════

class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。合并 FrameworkCommand（框架命令）与 ChatCommand（聊天命令），值完全不变（22 个枚举值）。"""
    REASONING = 0       # (0, text: str)
    CONTENT = 1         # (1, text: str)
    PHASE_DONE = 2      # (2, phase: str)
    TOOL_OUTPUT = 6     # (6, text: str)
    TOOL_SUMMARY = 7    # (7, successful: tuple, failed: tuple)
    USER_MSG = 8        # (8, text: str)
    PARSE_INFO = 9      # (9, tool_names: str, tokens: int, elapsed: float)
    NOTIFICATION = 11   # (11, text: str)
    WRITE_LINE = 12     # (12, text: str)
    DISPLAY_MSGS = 13   # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14 # (14,) — 工具计数+1
    TOOL_FAIL_INC = 15  # (15,) — 工具失败计数+1
    ERROR = 16          # (16, message: str) — 系统错误
    TOOL_COUNT_DEC = 17 # (17,) — 工具计数-1
    SUBAGENT_FRAME = 18 # (18, frame_lines: tuple[str]) — SubAgent 面板帧
    SPLASH = 19         # (19,) — 启动品牌屏
    MAIN_PHASE = 20     # (20, phase: str) — 主Agent模型阶段变更
    TOOL_OPEN = 21      # (21, tool_name, tool_id, detail) — 工具 box 打开
    TOOL_CLOSE = 22     # (22, tool_id, success) — 工具 box 关闭
    SUBAGENT_MARKDOWN = 23  # (23, text: str) — subagent 提词/返回 markdown 消息区块
    CLEAR_MSGS = 24      # (24,) — 清空消息区显示（编辑/加载会话重渲染前使用）
    BG_BASH_COUNT = 25   # (25, count: int) — 后台 bash 任务总数（主 agent + subagent 聚合）


# ═══════════════════════════════════════════════════════════
# RenderCmd — 渲染命令数据类（取代元组传递）
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True)
class RenderCmd:
    """渲染命令基类。所有子类必须设 cid 默认值。"""
    cid: int = 0

@dataclass(frozen=True)
class ReasoningCmd(RenderCmd):
    cid: int = RenderCommand.REASONING
    text: str = ""

@dataclass(frozen=True)
class ContentCmd(RenderCmd):
    cid: int = RenderCommand.CONTENT
    text: str = ""

@dataclass(frozen=True)
class PhaseDoneCmd(RenderCmd):
    cid: int = RenderCommand.PHASE_DONE
    phase: str = ""

@dataclass(frozen=True)
class ToolOutputCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_OUTPUT
    text: str = ""
    tool_id: str = ""

@dataclass(frozen=True)
class ToolSummaryCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_SUMMARY
    successful: tuple = ()
    failed: tuple = ()

@dataclass(frozen=True)
class ToolOpenCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_OPEN
    tool_name: str = ""
    tool_id: str = ""
    detail: str = ""

@dataclass(frozen=True)
class ToolCloseCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_CLOSE
    tool_id: str = ""
    success: bool = True

@dataclass(frozen=True)
class UserMsgCmd(RenderCmd):
    cid: int = RenderCommand.USER_MSG
    text: str = ""

@dataclass(frozen=True)
class ParseInfoCmd(RenderCmd):
    cid: int = RenderCommand.PARSE_INFO
    tool_names: str = ""
    tokens: int = 0
    elapsed: float = 0.0

@dataclass(frozen=True)
class NotificationCmd(RenderCmd):
    cid: int = RenderCommand.NOTIFICATION
    text: str = ""

@dataclass(frozen=True)
class WriteLineCmd(RenderCmd):
    cid: int = RenderCommand.WRITE_LINE
    text: str = ""

@dataclass(frozen=True)
class DisplayMsgsCmd(RenderCmd):
    cid: int = RenderCommand.DISPLAY_MSGS
    messages: list = field(default_factory=list)
    speed: int = 0

@dataclass(frozen=True)
class ClearMsgsCmd(RenderCmd):
    cid: int = RenderCommand.CLEAR_MSGS

@dataclass(frozen=True)
class ToolCountIncCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_COUNT_INC

@dataclass(frozen=True)
class ToolFailIncCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_FAIL_INC

@dataclass(frozen=True)
class ErrorCmd(RenderCmd):
    cid: int = RenderCommand.ERROR
    message: str = ""

@dataclass(frozen=True)
class ToolCountDecCmd(RenderCmd):
    cid: int = RenderCommand.TOOL_COUNT_DEC

@dataclass(frozen=True)
class SubagentFrameCmd(RenderCmd):
    cid: int = RenderCommand.SUBAGENT_FRAME
    # tuple | list：调用侧 _subagent_panel._push_frame 实际传 List[Line]
    # （渲染行列表）——标注放宽以匹配真实调用（修复前标注 tuple 与传参不符）。
    frame_lines: tuple | list = ()

@dataclass(frozen=True)
class SplashCmd(RenderCmd):
    cid: int = RenderCommand.SPLASH

@dataclass(frozen=True)
class MainPhaseCmd(RenderCmd):
    cid: int = RenderCommand.MAIN_PHASE
    phase: str = ""

@dataclass(frozen=True)
class SubagentMarkdownCmd(RenderCmd):
    cid: int = RenderCommand.SUBAGENT_MARKDOWN
    text: str = ""

@dataclass(frozen=True)
class BgBashCountCmd(RenderCmd):
    """后台任务数量更新（bash 与 subagent 分开聚合）。

    Attributes:
        count: 当前运行中的后台 bash 任务总数（主 agent + 全部 subagent）。
        subagent_count: 当前运行中的后台 subagent 任务总数（主 agent 派发）。
    """
    cid: int = RenderCommand.BG_BASH_COUNT
    count: int = 0
    subagent_count: int = 0


# ═══════════════════════════════════════════════════════════
# 向后兼容别名 — FrameworkCommand/ChatCommand → RenderCommand
# ═══════════════════════════════════════════════════════════

FrameworkCommand = RenderCommand
ChatCommand = RenderCommand


# ═══════════════════════════════════════════════════════════
# CONTENT_COMMANDS — 内容命令集合（唯一真源，步骤 4.1）
# ═══════════════════════════════════════════════════════════
# 收敛 _renderer/_renderer.py 与 _renderer/_engine.py 的重复定义。
# 私有名 _CONTENT_COMMANDS 在各自模块保留别名以兼容 re-export。

CONTENT_COMMANDS: frozenset[RenderCommand] = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.PARSE_INFO,
    RenderCommand.USER_MSG,
    RenderCommand.ERROR,
    RenderCommand.WRITE_LINE,
    RenderCommand.NOTIFICATION,
    RenderCommand.DISPLAY_MSGS,
    RenderCommand.SPLASH,
})


def is_agent_source(source: str | None) -> bool:
    """判断事件 source 是否为 agent 来源（唯一真源，供过滤策略收敛引用）。

    与历史 `EventDispatcher._default_filter_fn` / 装配注入 lambda 语义一致：
    ``source == "agent"`` 或 ``source.startswith("agent-")``；None 视为非 agent。
    """
    if source is None:
        return False
    return source == "agent" or source.startswith("agent-")


def truncate_error_message(text: str | None, max_length: int) -> str:
    """截断错误消息至 max_length 字符（含省略号，唯一真源，步骤 4.3）。

    收敛 _renderer/_dispatcher.py `_on_model_phase` 与 consumer/__init__.py
    `ChatUIErrorHandler.emit` 的重复截断逻辑。
    语义：超长时保留前 max_length-3 字符并追加 "..."，使结果长度恰为
    max_length（max_length<=3 时退化为直接截断，不加省略号）；未超长时原样返回。
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
