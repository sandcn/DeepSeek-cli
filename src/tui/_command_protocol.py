"""渲染命令协议 — 帧内命令模型（Layer 0，零内部依赖）。

``InkSession`` / ``_SessionQueueMixin`` / ``_SessionFrameMixin`` 的队列里流动的
就是本模块的 ``RenderCmd`` 子类，因此**命令协议属于框架而非应用**。历史上
该协议定义在 ``src/tui/_const.py``（应用常量模块），导致框架 ``ink`` 反向
依赖应用层；本模块把它下沉为独立的零依赖协议模块：

  - ``src/tui/ink/command.py``  — 框架侧门面（re-export，供 ink 内部统一导入）；
  - ``src/tui/_const.py``       — 应用侧 re-export（既有导入路径零改动）。

为什么不直接把真源放 ``ink/command.py``：``src/tui/__init__`` → ``_const`` →
``ink.command`` 会触发 ``ink/__init__`` → ``ink.output`` → ``core.style`` →
``_const``，形成**包初始化期循环导入**（实测 ImportError）。把真源放在
``src/tui`` 包内、不触发 ``ink`` 包初始化的独立模块，可同时满足「框架不依赖
应用常量」与「无循环」两个约束。

设计约束：
  - 零内部依赖（仅标准库）——任何层都可以 import 本模块而不引入环；
  - 枚举值**不得变更**：它们是队列优先级与跨线程协议的一部分（值即协议），
    新增命令只允许追加新值，不允许重排；
  - ``RenderCmd`` 为 frozen dataclass，``cid`` 由子类默认值固定——分发方按
    ``cid`` 分派，不依赖类型判断（历史元组协议 → dataclass 迁移的兼容点）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

__all__ = [
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
]


class RenderCommand(IntEnum):
    """渲染命令类型（框架 ↔ 应用的单向数据协议）。

    合并历史上分离的 ``FrameworkCommand``（框架命令）与 ``ChatCommand``
    （聊天命令），值完全不变（23 个枚举值）——值是队列优先级与跨线程协议
    的一部分，**只允许追加**。
    """

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
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC = 15  # (15,) — 工具失败计数+1
    ERROR = 16          # (16, message: str) — 系统错误
    TOOL_COUNT_DEC = 17  # (17,) — 工具计数-1
    SUBAGENT_FRAME = 18  # (18, frame_lines: tuple[str]) — SubAgent 面板帧
    SPLASH = 19         # (19,) — 启动品牌屏
    MAIN_PHASE = 20     # (20, phase: str) — 主 Agent 模型阶段变更
    TOOL_OPEN = 21      # (21, tool_name, tool_id, detail) — 工具 box 打开
    TOOL_CLOSE = 22     # (22, tool_id, success) — 工具 box 关闭
    CLEAR_MSGS = 24     # (24,) — 清空消息区显示（编辑/加载会话重渲染前使用）
    BG_BASH_COUNT = 25  # (25, count: int) — 后台 bash 任务总数（主 + subagent 聚合）
    COMPACTION = 26     # (26, active: int) — 上下文压缩中的 Agent 数量
    TOOL_MARKDOWN = 27  # (27, text: str, tool_id: str) — 工具卡内 markdown 输出


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
    chat_hidden: bool = False


@dataclass(frozen=True)
class ToolMarkdownCmd(RenderCmd):
    """工具卡内 markdown 输出（经 ``AnsiStreamRenderer`` 渲染为卡片正文）。"""

    cid: int = RenderCommand.TOOL_MARKDOWN
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
    frame_lines: tuple | list = ()


@dataclass(frozen=True)
class SplashCmd(RenderCmd):
    cid: int = RenderCommand.SPLASH


@dataclass(frozen=True)
class MainPhaseCmd(RenderCmd):
    cid: int = RenderCommand.MAIN_PHASE
    phase: str = ""


@dataclass(frozen=True)
class BgBashCountCmd(RenderCmd):
    """后台任务数量更新（bash 与 subagent 分开聚合）。"""

    cid: int = RenderCommand.BG_BASH_COUNT
    count: int = 0
    subagent_count: int = 0


@dataclass(frozen=True)
class CompactionCmd(RenderCmd):
    """上下文压缩状态更新（模式行行首显示压缩中的 Agent 数量）。"""

    cid: int = RenderCommand.COMPACTION
    active: int = 0


#: 向后兼容别名（历史 FrameworkCommand / ChatCommand 合并进 RenderCommand）。
FrameworkCommand = RenderCommand
ChatCommand = RenderCommand


#: 内容命令集合（「内容类」命令：触发 markdown 预览刷新 / 增量提交）。
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
    RenderCommand.TOOL_MARKDOWN,
})
