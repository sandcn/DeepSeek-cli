"""DEPRECATED: 请直接使用 src.chat_ui.parallel。此文件仅为向后兼容。

注意：由于 src.ui.renderer.frame_renderer → src.ui.parallel._config → src.ui.parallel.__init__
→ src.chat_ui.parallel 形成循环导入，此处使用惰性加载以避免循环依赖。
"""

import importlib as _importlib

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

_PARALLEL_MODULE = None


def _get_parallel():
    global _PARALLEL_MODULE
    if _PARALLEL_MODULE is None:
        _PARALLEL_MODULE = _importlib.import_module("src.chat_ui.parallel")
    return _PARALLEL_MODULE


def __getattr__(name: str):
    if name in __all__:
        return getattr(_get_parallel(), name)
    raise AttributeError(f"module 'src.ui.parallel' has no attribute {name!r}")
