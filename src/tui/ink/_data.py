"""表现层数据注入 — 框架读取「可被插件覆盖的数据表」的唯一入口。

背景：边框字符、代码块兜底边框、Divider 默认宽度、Badge 对比色度量等
**可 Patch/Overlay 覆盖**的数据表，真源在应用层 ``src.presentation_data``
（插件清单里的 ``DataTable`` 条目）。框架直接 ``from src.presentation_data
import ...`` 会形成 ink → 应用的反向依赖。

本模块的做法：
  - 框架内置**最小兜底值**（single/double/round/bold/classic/dashed 边框等），
    保证独立使用 ink 时渲染正确；
  - 生产装配经 ``set_data_provider(presentation_data)`` 注入应用真源，
    覆盖兜底值（注入后行为与历史完全一致）；
  - 注入接口按 duck typing 匹配 ``presentation_data`` 的同名函数
    （``border_chars()`` / ``border_object_default()`` / ``ui_default(key, default)`` /
    ``badge_metrics()``），异常/缺席一律回退兜底——框架不因数据表坏掉而崩。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from ._hook_context import current_context

__all__ = [
    "set_data_provider",
    "get_data_provider",
    "DataTableMapping",
    "ui_default",
    "badge_metrics",
    "spinner_presets",
    "inline_spinner_frames",
    "BORDER_CHARS_FALLBACK",
    "BORDER_OBJECT_DEFAULT_FALLBACK",
    "UI_DEFAULTS_FALLBACK",
    "SPINNER_PRESETS_FALLBACK",
    "INLINE_SPINNER_FRAMES_FALLBACK",
]

#: 无注入时的数据提供者（None = 只用内置兜底）。
_provider: Any | None = None


def set_data_provider(provider: Any | None) -> None:
    """注入表现层数据提供者（传 ``None`` 恢复内置兜底）。"""
    global _provider
    _provider = provider


def get_data_provider() -> Any | None:
    """当前数据提供者（会话级优先，回退模块级默认）。

    ★ 2026-10-24（多会话）：``InkSession`` 把 provider 写入自己的
    ``HookContext.data_provider``——渲染线程激活该上下文时读取会话级
    数据表，避免多会话/多渲染根互相覆盖。未挂载会话（独立 ``render()``、
    非渲染线程调用）回退 ``set_data_provider`` 的模块级默认，行为不变。
    """
    ctx = current_context()
    scoped = getattr(ctx, "data_provider", None)
    if scoped is not None:
        return scoped
    return _provider


def _call(name: str) -> Any:
    provider = get_data_provider()
    if provider is None:
        return None
    fn = getattr(provider, name, None)
    if fn is None:
        return None
    try:
        return fn()
    except Exception:  # pragma: no cover - 数据表异常一律回退兜底
        return None


class DataTableMapping(Mapping):
    """只读映射视图：提供者数据优先，缺席/非法时回退内置兜底表。"""

    def __init__(self, name: str, fallback: Mapping) -> None:
        self._name = name
        self._fallback = fallback

    def _data(self) -> Mapping:
        data = _call(self._name)
        if isinstance(data, Mapping) and data:
            return data
        return self._fallback

    def __getitem__(self, key):
        try:
            return self._data()[key]
        except KeyError:
            return self._fallback[key]

    def __iter__(self):
        return iter(self._fallback)

    def __len__(self) -> int:
        return len(self._fallback)


#: 边框字符兜底表（与 ``presentation_data.BORDER_CHARS_MAP`` 同值的最小集；
#: 生产注入后以应用真源为准）。
BORDER_CHARS_FALLBACK: dict = {
    "single": ("┌", "┐", "└", "┘", "─", "│"),
    "double": ("╔", "╗", "╚", "╝", "═", "║"),
    "round": ("╭", "╮", "╰", "╯", "─", "│"),
    "bold": ("┏", "┓", "┗", "┛", "━", "┃"),
    "classic": ("+", "+", "+", "+", "-", "|"),
    "dashed": ("┌", "┐", "└", "┘", "┄", "┆"),
    "singleDouble": ("╓", "╖", "╙", "╜", "═", "│"),
    "doubleSingle": ("╒", "╕", "╘", "╛", "─", "║"),
}

#: 自定义 borderStyle 对象缺省值（React Ink v6 键集）。
BORDER_OBJECT_DEFAULT_FALLBACK: dict = {
    "topLeft": "┌", "top": "─", "topRight": "┐",
    "left": "│",
    "bottomLeft": "└", "bottom": "─", "bottomRight": "┘",
    "right": "│",
}

#: UI 默认参数兜底（与 ``presentation_data.UI_DEFAULTS_DATA`` 同值的最小集）。
UI_DEFAULTS_FALLBACK: dict = {
    "prompt": "> ",
    "truncate_width": 48,
    "divider_width": 40,
    "codeblock_border": ["┌", "┐", "└", "┘", "─", "│"],
}


def ui_default(key: str, fallback: Any) -> Any:
    """读取 UI 默认参数（提供者优先，缺席/异常回退 ``fallback``）。"""
    provider = get_data_provider()
    if provider is not None:
        fn: Callable | None = getattr(provider, "ui_default", None)
        if fn is not None:
            try:
                return fn(key, fallback)
            except Exception:  # pragma: no cover
                return fallback
    return UI_DEFAULTS_FALLBACK.get(key, fallback)


def badge_metrics() -> dict:
    """读取 Badge 对比色度量表（缺席时返回空 dict，由调用方兜底）。"""
    data = _call("badge_metrics")
    return data if isinstance(data, dict) else {}


#: Spinner 预设帧兜底表（与 ``presentation_data.SPINNER_PRESET_FRAMES`` 同值；
#: 生产注入后以应用真源为准，测试 ``test_layering_contract`` 校验同值）。
SPINNER_PRESETS_FALLBACK: dict = {
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

#: 行内 Spinner 兜底帧序列（braille，10 帧）。
INLINE_SPINNER_FRAMES_FALLBACK: str = (
    "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
)


def spinner_presets() -> dict:
    """读取 Spinner 预设帧表（提供者优先，缺席回退内置表）。"""
    data = _call("spinner_presets")
    return data if isinstance(data, dict) and data else SPINNER_PRESETS_FALLBACK


def inline_spinner_frames() -> str:
    """读取行内 Spinner 帧序列（提供者优先，缺席回退内置串）。"""
    provider = get_data_provider()
    if provider is not None:
        fn: Callable | None = getattr(provider, "inline_spinner_frames", None)
        if fn is not None:
            try:
                value = fn()
                if isinstance(value, str) and value:
                    return value
            except Exception:  # pragma: no cover
                pass
    return INLINE_SPINNER_FRAMES_FALLBACK
