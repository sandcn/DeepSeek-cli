"""UI Components — TuiComponent base class, message blocks, box, animation."""
from .animation import AnimationClock, use_animation  # noqa: F401
from .base import (  # noqa: F401
    TuiComponent,
    InputBarComponent,
    UserMsgBlock,
    ThinkingBlock,
    AnswerBlock,
    ToolOutputBlock,
    ToolSummaryBlock,
    ErrorBlock,
    NotificationBlock,
    WriteLineBlock,
    _estimate_content_lines,
)
from .box import Box  # noqa: F401
from .message_blocks import (  # noqa: F401
    ThinkingBlockBox,
    AnswerBlockBox,
    UserMsgBlockBox,
    ToolOutputBlockBox,
    ErrorBlockBox,
    NotificationBlockBox,
    TextContent,
    create_message_box,
)
from .static import Static  # noqa: F401
from .transform import Transform  # noqa: F401
from .text import Text  # noqa: F401
from .newline import Newline  # noqa: F401
from .spacer import Spacer  # noqa: F401
from .unordered_list import UnorderedList  # noqa: F401
from .ordered_list import OrderedList  # noqa: F401
from .link import Link  # noqa: F401
from .scrollbar import Scrollbar  # noqa: F401
from .table import Table  # noqa: F401

__all__ = [
    # Animation
    "AnimationClock",
    "use_animation",
    # Base
    "TuiComponent",
    "InputBarComponent",
    "UserMsgBlock",
    "ThinkingBlock",
    "AnswerBlock",
    "ToolOutputBlock",
    "ToolSummaryBlock",
    "ErrorBlock",
    "NotificationBlock",
    "WriteLineBlock",
    "_estimate_content_lines",
    # Box
    "Box",
    # Message Blocks
    "ThinkingBlockBox",
    "AnswerBlockBox",
    "UserMsgBlockBox",
    "ToolOutputBlockBox",
    "ErrorBlockBox",
    "NotificationBlockBox",
    "TextContent",
    "create_message_box",
    # Static & Transform
    "Static",
    "Transform",
    # New components
    "Text",
    "Newline",
    "Spacer",
    "UnorderedList",
    "OrderedList",
    "Link",
    "Scrollbar",
    "Table",
]
