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
  （已移除 4 个死组件：StatusLine / InputLine / CompletionPopup / SelectionMenu）

兼容 re-export：BottomBarProtocol（定义在 _protocols.py）
"""

from __future__ import annotations

# 辅助函数
from ._base import _estimate_content_lines

# 基类
from ._base import TuiComponent

# 消息流组件
from ._user_msg import UserMsgBlock
from ._thinking import ThinkingBlock
from ._answer import AnswerBlock
from ._tool_output import ToolOutputBlock
from ._tool_summary import ToolSummaryBlock
from ._error import ErrorBlock
from ._notification import NotificationBlock
from ._write_line import WriteLineBlock

# 兼容 re-export（定义已移至 _protocols.py）
from ..protocols import BottomBarProtocol

__all__ = [
    "TuiComponent",
    "_estimate_content_lines",
    "UserMsgBlock",
    "ThinkingBlock",
    "AnswerBlock",
    "ToolOutputBlock",
    "ToolSummaryBlock",
    "ErrorBlock",
    "NotificationBlock",
    "WriteLineBlock",
    "BottomBarProtocol",
]
