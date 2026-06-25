"""UI 模块 — 展示层适配器和组件

包含终端适配器、并行显示、事件总线、格式化和渲染器。TUI 实现已统一到 React Ink（见 src/chat_ui/）。
"""

from __future__ import annotations

# 惰性导出：避免 cost_display → config → ui._lock → ui.__init__ 循环导入
__all__ = [
    "extract_key_params",
    "show_round_cost",
]


def __getattr__(name: str):
    if name == "extract_key_params":
        from .formatters.param_formatter import extract_key_params
        return extract_key_params
    if name == "show_round_cost":
        from .components.cost_display import show_round_cost
        return show_round_cost
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
