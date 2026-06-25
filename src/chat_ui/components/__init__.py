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
