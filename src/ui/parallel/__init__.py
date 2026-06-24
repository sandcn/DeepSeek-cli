"""并行显示模块"""
from .display import ParallelDisplay
from ._config import DisplayConfig, PARALLEL_REFRESH_HZ, MIN_REFRESH_INTERVAL, SPINNER_FRAMES
from ._tool_icons import (
    TOOL_COLORS, TOOL_ICONS, AGENT_TYPE_ABBREV,
    AGENT_TYPE_COLORS, TOOL_CATEGORY_COLORS, get_tool_color,
)
from ._text_formatter import TextFormatter
