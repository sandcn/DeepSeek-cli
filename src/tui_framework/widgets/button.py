"""Button 按钮控件 — 带标签的可点击按钮，支持多种样式变体。

设计模式: 命令 — 按钮携带可执行的动作回调 ``on_click``。
"""

from __future__ import annotations

import logging
from typing import Callable

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.base import Widget

_logger = logging.getLogger(__name__)

# ── 按钮样式预设 ────────────────────────────────────────

BUTTON_STYLES: dict[str, str] = {
    "primary": "\033[38;5;41m",     # 绿色
    "secondary": "\033[38;5;242m",  # 中灰
    "danger": "\033[38;5;196m",     # 红色
    "warning": "\033[38;5;221m",    # 琥珀黄
    "info": "\033[38;5;45m",        # 青色
    "muted": "\033[38;5;237m",      # 深灰
}


class Button(Widget):
    """按钮控件。

    ## 交互行为

    - ``space`` 或 ``enter`` → 触发 ``on_click`` 回调
    - 禁用状态下不响应任何键盘事件

    ## 渲染

    渲染格式: ``[ Label ]``，不同 ``style`` 变体使用不同颜色。
    禁用状态下渲染为低饱和度颜色（muted）。

    Args:
        label: 按钮标签文本。
        style: 样式变体，"primary" / "secondary" / "danger" /
               "warning" / "info" / "muted"，默认 "secondary"。
        disabled: 是否初始禁用。
    """

    #: 渲染时的边框字符
    BRACKET_LEFT: str = "["
    BRACKET_RIGHT: str = "]"
    #: 边框与标签之间的间距
    PADDING: str = " "

    def __init__(
        self,
        label: str = "",
        style: str = "secondary",
        disabled: bool = False,
    ) -> None:
        super().__init__()
        self.label: str = label
        self.style: str = style
        self._disabled: bool = disabled

        # 回调
        self.on_click: Callable[[], None] | None = None

    # ── 属性 ────────────────────────────────────────────

    @property
    def label(self) -> str:
        """按钮标签文本。"""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        self._label = value

    # ── 事件处理 ────────────────────────────────────────

    def on_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件。

        空格或回车触发 on_click，禁用时返回 False。

        Returns:
            True 表示事件已消费。
        """
        if self._disabled:
            return False

        if event.key in ("enter", "space"):
            if self.on_click is not None:
                try:
                    self.on_click()
                except Exception:
                    _logger.exception("Button.on_click() 异常 [label=%s]", self._label)
            return True

        return False

    # ── 渲染 ────────────────────────────────────────────

    def _resolve_color(self) -> str:
        """解析按钮颜色。

        优先使用 resolve_theme_color() 链式查找主题颜色，
        未命中时回退到 BUTTON_STYLES 预设。
        禁用时强制使用 muted 颜色。
        """
        if self._disabled:
            return BUTTON_STYLES["muted"]
        color = self.resolve_theme_color("button") or BUTTON_STYLES.get(
            self.style, BUTTON_STYLES["secondary"]
        )
        return color

    def render(self) -> str:
        """渲染按钮。

        格式: ``[ Label ]``，根据 style/focused/disabled 应用颜色。
        获得焦点时标签加下划线。
        """
        if not self._visible:
            return ""

        color = self._resolve_color()
        reset = "\033[0m"

        # 构建标签
        label_display = self._label
        if self._focused:
            label_display = f"\033[4m{label_display}{reset}{color}"

        rendered = f"{self.BRACKET_LEFT}{self.PADDING}{label_display}{self.PADDING}{self.BRACKET_RIGHT}"
        return f"{color}{rendered}{reset}"
