"""渲染命令协议门面 — 真源在 ``src/tui/_command_protocol.py``。

框架内部统一 ``from .command import RenderCommand, RenderCmd, ...``；应用侧经
``src/tui/_const.py`` re-export 同一批符号。真源放在 ``src/tui`` 包内而非本包，
是为了避免「``_const`` → ``ink.command`` → ``ink.__init__`` → ``ink.output`` →
``core.style`` → ``_const``」的包初始化期循环导入（见真源模块 docstring）。
"""

from __future__ import annotations

from .._command_protocol import (  # noqa: F401  （显式重导出，见 __all__）
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
    SubagentMarkdownCmd,
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
    "SubagentMarkdownCmd",
    "BgBashCountCmd",
    "CompactionCmd",
    "CONTENT_COMMANDS",
]
