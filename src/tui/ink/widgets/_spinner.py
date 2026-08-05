"""Spinner — 旋转加载动画控件（React Ink ink-spinner 对齐）。

模块边界（2026-08-05 架构优化）：从 ``widgets/display.py`` 拆分——Spinner
独立成模块（公共辅助经 ``_display_common`` 共享）。threading 为全局单例
模块——测试 ``patch("src.tui.ink.widgets.display.threading.Timer")`` 修改的
是同一 threading 模块对象，本模块 ``import threading`` 引用同一对象，patch
依然生效（兼容保留）。
"""

from __future__ import annotations

import threading

from ..element import TEXT, Element, h
from ..hooks import use_state, use_effect
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

    实现：``use_state`` 保存帧序号 + ``use_effect`` 注册 ``threading.Timer``
    周期推进帧序号（set_state → schedule → 重渲染）。组件卸载时清理 Timer
    （stop 标志防残余 tick 继续创建新 Timer）。``interval``/``indicator``
    变化不重建 Timer（挂载时捕获；React Ink setInterval deps=[] 同语义）。

    Returns:
        TEXT 元素（当前帧字符）。
    """
    indicator = props.get("indicator")
    type_ = str(props.get("type", "dots"))
    if indicator:
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
    frame_index, set_frame_index = use_state(0)

    def _create():
        stop = {"stop": False}

        def _tick():
            if stop["stop"]:
                return
            set_frame_index(lambda i: (i + 1) % len(frames))
            _schedule_next()

        def _schedule_next():
            t = threading.Timer(interval / 1000.0, _tick)
            t.daemon = True
            t.start()

        _schedule_next()

        def _cleanup():
            stop["stop"] = True

        return _cleanup

    use_effect(_create, ())

    ch = frames[frame_index % len(frames)]
    return h(TEXT, {"children": ch, "style": style, "height": 1})


__all__ = ["Spinner", "SPINNER_FRAMES"]
