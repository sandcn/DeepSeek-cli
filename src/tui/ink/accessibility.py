"""accessibility — React Ink ``aria-*`` 属性支持（屏幕阅读器元数据）。

React Ink ``<Box>``/``<Text>`` 支持 ``aria-label`` / ``aria-hidden`` /
``aria-role`` / ``aria-state`` 属性，用于屏幕阅读器输出。本框架为终端
非全屏流动模型：这些属性不参与布局/绘制（与官方一致——它们是元数据），
本模块提供**统一提取**入口，供组件/工具（``renderToString``、
``useIsScreenReaderEnabled`` 分支）读取。

用法::

    from src.tui.ink.accessibility import get_accessibility
    get_accessibility(props)
    # → {"label": str|None, "hidden": bool, "role": str|None, "state": dict}
"""

from __future__ import annotations

from typing import Any, Mapping

#: 官方 ``aria-role`` 允许值（React Ink v6/v7）。
ARIA_ROLES = frozenset({
    "button", "checkbox", "combobox", "list", "listbox", "listitem", "menu",
    "menuitem", "option", "progressbar", "radio", "radiogroup", "tab",
    "tablist", "table", "textbox", "timer", "toolbar",
})

#: 官方 ``aria-state`` 允许键。
ARIA_STATE_KEYS = frozenset({
    "busy", "checked", "disabled", "expanded", "multiline", "multiselectable",
    "readonly", "required", "selected",
})


def get_accessibility(props: Any) -> dict:
    """从元素 props 提取无障碍元数据（缺失/畸形安全回退）。

    Args:
        props: 元素 props（dict）。

    Returns:
        dict：``{"label": str|None, "hidden": bool, "role": str|None,
        "state": dict}``——role 非官方值时归 None；state 仅保留官方键。
    """
    out = {"label": None, "hidden": False, "role": None, "state": {}}
    if not isinstance(props, Mapping):
        return out
    label = props.get("aria-label")
    if isinstance(label, str) and label:
        out["label"] = label
    out["hidden"] = bool(props.get("aria-hidden", False))
    role = props.get("aria-role")
    if isinstance(role, str) and role in ARIA_ROLES:
        out["role"] = role
    state = props.get("aria-state")
    if isinstance(state, Mapping):
        out["state"] = {k: bool(v) for k, v in state.items() if k in ARIA_STATE_KEYS}
    return out


def screen_reader_text(props: Any, fallback: str = "") -> str:
    """屏幕阅读器模式下的文本：优先 ``aria-label``，否则回退内容文本。"""
    acc = get_accessibility(props)
    if acc["hidden"]:
        return ""
    if acc["label"]:
        return acc["label"]
    return fallback


__all__ = ["get_accessibility", "screen_reader_text", "ARIA_ROLES", "ARIA_STATE_KEYS"]
