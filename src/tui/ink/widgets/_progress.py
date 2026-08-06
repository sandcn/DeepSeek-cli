"""ProgressBar — 进度条控件（React Ink ink-progress-bar 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——进度条
独立成模块（公共辅助经 ``_display_common`` 共享）。
"""

from __future__ import annotations

import math

from ..element import TEXT, Element, h
from src.tui._width import wcswidth_simple
from ._display_common import _resolve_style


def ProgressBar(props: dict) -> Element:
    """React Ink ``<ProgressBar>`` 等价物：进度条控件。

    Props:
        percent: 进度（0-1 或 0-100，自动识别归一化）。
        width: 进度条区域宽度（默认 40）。
        left/right: 左右标记文本（如 ``"["`` / ``"]"``；默认空）。
        char: 进度填充字符（默认 ``"█"``；支持宽字符，按显示宽度换算）。
        color: 前景色（颜色名/int）。
        style: 完整样式（``color`` 覆盖 style.fg）。

    Returns:
        TEXT 元素（``left + 进度条 + right``）。
    """
    try:
        percent = float(props.get("percent", 0))
    except (TypeError, ValueError):
        percent = 0.0
    # ★ P3（review）：NaN/Inf percent 防御——修复前 float('nan') 通过
    #   ``min(1.0, nan)`` 返回 1.0 → NaN 渲染为 100% 满格。非有限值回退 0%。
    if not math.isfinite(percent):
        percent = 0.0
    # 归一化：0-1 原样；(1, 100] 视为百分比；> 100 视为 100%
    if percent > 1.0:
        if percent <= 100.0:
            percent /= 100.0
        else:
            percent = 1.0
    percent = max(0.0, min(1.0, percent))
    try:
        width = max(1, int(props.get("width", 40)))
    except (TypeError, ValueError, OverflowError):
        width = 40
    left = str(props.get("left", ""))
    right = str(props.get("right", ""))
    char = str(props.get("char", "█")) or "█"
    style = _resolve_style(props)

    char_w = wcswidth_simple(char)
    if char_w <= 0:
        # ★ P3（review）：零宽字符（如 ``\u200b``）实际宽度 0——修复前
        #   ``max(1, wcswidth_simple(char))`` 按 1 计导致总宽不足（进度条
        #   渲染不满 width）。零宽 char 回退默认 "█"（与 _display_common
        #   ``_repeat_to_width`` 的零宽回退空格填充不同：进度条语义须保留
        #   填充字符视觉）。
        char = "█"
        char_w = 1
    filled_w = int(round(width * percent))
    filled_chars = filled_w // char_w
    bar = char * filled_chars + " " * (width - filled_chars * char_w)
    return h(TEXT, {"children": left + bar + right, "style": style})


__all__ = ["ProgressBar"]
