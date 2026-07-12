"""chat_ui 渲染器包 — 渲染命令分发 + TuiRenderer + Rich桥接。

包含：
- `tui_renderer.py`：TuiRenderer 类 + _RENDER_DISPATCH（原 renderer.py，为避免与包名冲突已重命名）
- `bridge.py`：Rich-TUI 动效桥接

所有原本从 `src.chat_ui.renderer` 导入的符号（TuiRenderer, _RENDER_DISPATCH, _RenderState）
通过本 __init__.py 重新导出，保持向后兼容。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

# ── 桥接函数直接从 bridge 导入（无循环依赖） ─────────
from .bridge import (
    get_breath_color,
    get_sparkle_color,
    make_breath_style,
    make_morph_style,
    assemble_with_breath,
    _to_rich_color,
)

# ── 延迟导入：TuiRenderer / _RENDER_DISPATCH / _RenderState ──
# tui_renderer.py 从 .components 导入，components 从 .bridge 导入，
# 若在 __init__.py 中顶层导入 tui_renderer 会导致循环依赖。
# 使用 __getattr__ 实现按需延迟加载，消除循环导入。

_TUI_RENDERER_MODULE = None  # 延迟加载缓存


def _get_tui_renderer():
    global _TUI_RENDERER_MODULE
    if _TUI_RENDERER_MODULE is None:
        _TUI_RENDERER_MODULE = importlib.import_module("src.chat_ui.tui_renderer")
    return _TUI_RENDERER_MODULE


def __getattr__(name: str) -> Any:
    """延迟加载 tui_renderer 中的符号。"""
    _tui_renderer_symbols = {"TuiRenderer", "_RENDER_DISPATCH", "register_render_command"}
    if name in _tui_renderer_symbols:
        return getattr(_get_tui_renderer(), name)
    if name == "_RenderState":
        from ..render_state import _RenderState as _rs
        return _rs
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# 声明 __all__ 确保 IDE/类型检查器可以识别
__all__ = [
    "TuiRenderer",
    "_RENDER_DISPATCH",
    "_RenderState",
    "register_render_command",
    "get_breath_color",
    "get_sparkle_color",
    "make_breath_style",
    "make_morph_style",
    "assemble_with_breath",
    "_to_rich_color",
]
