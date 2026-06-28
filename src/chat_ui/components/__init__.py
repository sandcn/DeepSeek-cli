"""UI Components — TuiComponent base class, message blocks, box, animation.

所有组件导出入口。组件实现在 .py 文件中，此处聚合导出。
新增组件需同时在此文件和 react_ink/__init__.py 中注册。
"""
from .animation import (  # noqa: F401
    AnimationClock, use_animation,
    use_spinner, use_progress, use_typewriter, SPINNER_FRAMES,
    use_adaptive_animation, use_count_up, use_rainbow,
)
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
from .badge import Badge  # noqa: F401
from .box import Box  # noqa: F401
from .fixed_box import FixedSizeBox  # noqa: F401
from .list_view import ListView  # noqa: F401
from .breadcrumbs import Breadcrumbs  # noqa: F401
from .code import Code  # noqa: F401
from .collapsible import Collapsible  # noqa: F401
from .columns import Columns  # noqa: F401
from .divider import Divider  # noqa: F401
from .key_value import KeyValue  # noqa: F401
from .link import Link  # noqa: F401
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
from .newline import Newline  # noqa: F401
from .ordered_list import OrderedList  # noqa: F401
from .panel import Panel  # noqa: F401
from .progress import Progress  # noqa: F401
from .scrollbar import Scrollbar  # noqa: F401
from .spacer import Spacer  # noqa: F401
from .spinner import Spinner  # noqa: F401
from .static import Static  # noqa: F401
from .streaming_markdown import StreamingMarkdown  # noqa: F401
from .table import Table  # noqa: F401
from .text import Text  # noqa: F401
from .transform import Transform  # noqa: F401
from .tree import Tree, TreeNode  # noqa: F401
from .subagent_tree import subagent_slots_to_tree  # noqa: F401
from .unordered_list import UnorderedList  # noqa: F401

__all__ = [
    # Animation
    "AnimationClock",
    "use_animation",
    "use_spinner",
    "use_progress",
    "use_typewriter",
    "SPINNER_FRAMES",
    "use_adaptive_animation",
    "use_count_up",
    "use_rainbow",
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
    # Badge
    "Badge",
    # Box
    "Box",
    # Breadcrumbs
    "Breadcrumbs",
    # Code
    "Code",
    # Collapsible
    "Collapsible",
    # Columns
    "Columns",
    # Divider
    "Divider",
    # FixedSizeBox
    "FixedSizeBox",
    # KeyValue
    # KeyValue
    "KeyValue",
    # Link
    "Link",
    # ListView
    "ListView",
    # Message Blocks
    "ThinkingBlockBox",
    "AnswerBlockBox",
    "UserMsgBlockBox",
    "ToolOutputBlockBox",
    "ErrorBlockBox",
    "NotificationBlockBox",
    "TextContent",
    "create_message_box",
    # Newline
    "Newline",
    # OrderedList
    "OrderedList",
    # Panel
    "Panel",
    # Progress
    "Progress",
    # Scrollbar
    "Scrollbar",
    # Spacer
    "Spacer",
    # Spinner
    "Spinner",
    # Static & Transform
    "Static",
    "Transform",
    # StreamingMarkdown
    "StreamingMarkdown",
    # Table
    "Table",
    # Text
    "Text",
    # Tree
    "Tree",
    "TreeNode",
    "subagent_slots_to_tree",
    # UnorderedList
    "UnorderedList",
]
