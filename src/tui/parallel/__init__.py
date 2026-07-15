"""并行显示模块 — 基础模块已下沉至 core/，本层保留重导出"""
from .display import ParallelDisplay
from ..core.parallel_config import DisplayConfig, PARALLEL_REFRESH_HZ, MIN_REFRESH_INTERVAL, SPINNER_FRAMES
from ..core.tool_icons import (
    TOOL_COLORS, TOOL_ICONS, AGENT_TYPE_ABBREV,
    AGENT_TYPE_COLORS, TOOL_CATEGORY_COLORS, get_tool_color,
)
from ..core.text_formatter import TextFormatter

__all__ = [
    "ParallelDisplay",
    "DisplayConfig",
    "PARALLEL_REFRESH_HZ",
    "MIN_REFRESH_INTERVAL",
    "SPINNER_FRAMES",
    "TOOL_COLORS",
    "TOOL_ICONS",
    "AGENT_TYPE_ABBREV",
    "AGENT_TYPE_COLORS",
    "TOOL_CATEGORY_COLORS",
    "get_tool_color",
    "TextFormatter",
]
