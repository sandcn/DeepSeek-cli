"""spinner — InlineSpinner 行内时间基 spinner 控件。

React Ink 生态 ``<Spinner>`` 的行内变体：渲染为**单字符** spinner 帧
（``⠋⠙⠹…``），供状态行/解析行等**行内**场景使用（React Ink Spinner 为
独立组件占一行，行内场景用 InlineSpinner）。

时间基动画：帧号 = ``int(time.monotonic() * tick_hz) % len(frames)``——纯时间
推进（非帧计数），空闲不触发重绘由宿主渲染短路（``session._needs_animation``
语义；本控件仅按时间返回当前帧字符，不请求渲染）。
"""

from __future__ import annotations

import time

from ..element import TEXT, Element, h

__all__ = ["InlineSpinner", "SPINNER_FRAMES"]

#: 默认 spinner 帧序列（braille：⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏，10 帧 10Hz 推进 1s 循环）
SPINNER_FRAMES = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"


def _spinner_frame_index(tick_hz: float, frames) -> int:
    """时间基 spinner 帧索引（``int(now * hz) % len(frames)``）。"""
    n = len(frames)
    if n <= 0:
        return 0
    hz = tick_hz if tick_hz and tick_hz > 0 else 10.0
    return int(time.monotonic() * max(hz, 1e-6)) % n


def InlineSpinner(props: dict) -> Element:
    """行内时间基 spinner 字符控件。

    Props:
        tickHz: 每秒帧切换次数（默认 10.0）。
        frames: 帧序列（str/list/tuple；默认 braille 10 帧）。
        style: 字符样式（Style 对象或 None）。

    Returns:
        TEXT 元素（当前帧字符，单字符宽度）。
    """
    tick_hz = props.get("tickHz", 0.0)
    try:
        tick_hz = float(tick_hz)
    except (TypeError, ValueError, OverflowError):
        tick_hz = 0.0
    frames = props.get("frames", SPINNER_FRAMES)
    try:
        n = len(frames)
    except TypeError:
        n = 0
    if n <= 0:
        frames = SPINNER_FRAMES
        n = len(frames)
    idx = _spinner_frame_index(tick_hz, frames)
    try:
        ch = frames[idx]
    except (KeyError, IndexError, TypeError):
        # ★ P2-10（review）：frames 为 dict 时 ``frames[idx]`` 抛 KeyError（idx
        #   不在键中）——与 IndexError/TypeError 一致捕获，回退空格。
        ch = " "
    return h(TEXT, {"children": ch, "style": props.get("style")})
