"""ProgressBar — 进度条控件（React Ink ink-progress-bar 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——进度条
独立成模块（公共辅助经 ``_display_common`` 共享）。
"""

from __future__ import annotations

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

    char_w = max(1, wcswidth_simple(char))
    filled_w = int(round(width * percent))
    filled_chars = filled_w // char_w
    bar = char * filled_chars + " " * (width - filled_chars * char_w)
    return h(TEXT, {"children": left + bar + right, "style": style})


__all__ = ["ProgressBar"]
