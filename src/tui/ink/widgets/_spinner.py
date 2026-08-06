"""Spinner — 旋转加载动画控件（React Ink ink-spinner 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——Spinner
独立成模块（公共辅助经 ``_display_common`` 共享）。

★ P1（review 2026-08-06）：**时间基动画重构**——修复前 ``Spinner`` 用
``threading.Timer`` 后台线程周期调用 ``set_frame_index`` 推进帧号：Timer
线程与 render 线程的 ``_next_state_hook``（``hook.queue = None``）并发读写
同一 StateHook.queue，存在状态更新丢失与调度竞争，违反框架单线程渲染模型。
现改为**纯渲染期计算帧号**（``int(time.monotonic() * hz) % len(frames)``，
与 ``widgets/spinner.py`` 的 ``InlineSpinner`` 同语义）——无后台线程、无跨
线程状态访问、无状态 hook；空闲不触发重绘（与 InlineSpinner 一致，由宿主
渲染短路语义承担）。
"""

from __future__ import annotations

import time

from ..element import TEXT, Element, h
from ._display_common import _resolve_style

#: 内置动画帧字符集（Braille/几何/emoji，键名对齐 ink-spinner 常用预设）
SPINNER_FRAMES: dict[str, str] = {
    "dots": "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏",
    "dots2": "⣾⣽⣻⢿⡿⣟⣯⣷",
    "dots3": "⠋⠙⠚⠞⠖⠦⠴⠲⠳⠓",
    "dots4": "⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆",
    "dots5": "⠋⠙⠚⠒⠂⠂⠒⠲⠴⠦⠖⠒⠐⠐⠒⠓⠋",
    "dots6": "⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠴⠲⠒⠂⠂⠒⠚⠙⠉⠁",
    "dots7": "⠈⠉⠋⠓⠒⠐⠐⠒⠖⠦⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈",
    "dots8": "⠁⠁⠉⠙⠚⠒⠂⠂⠒⠲⠴⠤⠄⠄⠤⠠⠠⠤⠦⠖⠒⠐⠐⠒⠓⠋⠉⠈⠈",
    "dots9": "⢹⢺⢼⣸⣇⡧⡗⡏",
    "dots10": "⢄⢂⢁⡁⡈⡐⡠",
    "dots11": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "line": "─╼╾╴╶",
    "line2": "⠂⠒⠐⠈⠁⠉⠐⠒⠂",
    "pipe": "┤┘┴└├┌┬┐",
    "simpleDots": "⠂⠄⠆⠇⠋⠙⠸⠰⠠⠰⠸⠙⠋⠇⠆⠄",
    "simpleDotsScrolling": "⠈⠐⠠⢀⡀⢄⡂⡆⡇⡏⡟⡿⢿⠻⠽⠾⢾⣀⣠⣄⣆⣇⣏⣟⣿",
    "bar": "▁▃▄▅▆▇█▇▆▅▄▃",
    "vertical": "▁▂▃▄▅▆▇█▇▆▅▄▃▂",
    "grow": "▁▂▃▄▅▆▇█",
    "growHorizontal": "▏▎▍▌▋▊▉█",
    "arrow": "←↖↑↗→↘↓↙",
    "moon": "🌑🌒🌓🌔🌕🌖🌗🌘",
    "dotsClassic": "⠁⠂⠄⡀⢀⠠⠐⠈",
    "shark": "▐▌▐▌",
}


def Spinner(props: dict) -> Element:
    """React Ink ``<Spinner>`` 等价物：旋转加载动画控件。

    Props:
        type: 内置动画预设名（见 ``SPINNER_FRAMES``；默认 ``"dots"``）。
        indicator: 自定义帧序列（字符串/列表——每个字符/元素一帧），
            提供时优先于 ``type``。
        interval: 帧切换间隔毫秒（默认 80）。
        color: 前景色（颜色名/int）。
        style: 完整样式（``color`` 覆盖 style.fg）。

    实现（时间基）：渲染期按 ``time.monotonic()`` 与 ``interval`` 计算当前
    帧号（``int(now * (1000.0 / interval)) % len(frames)``）——纯时间推进
    （非帧计数，与 ``InlineSpinner`` 同语义）。修复 P1：无后台线程、无跨
    线程访问 hook 状态竞态；空闲不触发重绘（宿主渲染短路语义承担）。

    Returns:
        TEXT 元素（当前帧字符）。
    """
    indicator = props.get("indicator")
    type_ = str(props.get("type", "dots"))
    if indicator:
        # ★ P1（review）：indicator 为 list/tuple 时按帧序列元素逐帧取——修复前
        #   ``list(str(indicator))`` 对 ``["⠋","⠙"]`` 生成 ``['[', "'", ...]``
        #   逐字符 repr 垃圾帧。
        if isinstance(indicator, (list, tuple)):
            frames = list(indicator)
        else:
            frames = list(str(indicator))
    else:
        frames = list(SPINNER_FRAMES.get(type_, SPINNER_FRAMES["dots"]))
    if not frames:
        frames = [" "]
    try:
        interval = max(10, int(props.get("interval", 80)))
    except (TypeError, ValueError, OverflowError):
        interval = 80
    style = _resolve_style(props)
    # ★ P1（review）：时间基帧号——``int(now * hz) % n``（interval 毫秒 →
    #   每秒帧数 hz = 1000/interval）。纯渲染期计算，无 threading.Timer。
    hz = 1000.0 / interval
    frame_index = int(time.monotonic() * hz) % len(frames)
    # ★ P3（review）：frame_index 已在上一行取模——修复前重复
    #   ``% len(frames)``（无行为差异，冗余计算）。
    ch = frames[frame_index]
    return h(TEXT, {"children": ch, "style": style, "height": 1})


__all__ = ["Spinner", "SPINNER_FRAMES"]
