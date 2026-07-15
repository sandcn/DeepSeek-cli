"""Checkbox 复选框控件 — 独立勾选状态，ON/OFF 布尔切换。

设计模式: 状态 — ``checked`` 作为布尔状态，控制渲染符号和语义。
"""

from __future__ import annotations

import logging
from typing import Callable

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.base import Widget

_logger = logging.getLogger(__name__)


class Checkbox(Widget):
    """复选框控件。

    ## 交互行为

    - ``space`` / ``enter`` → 切换 ``checked`` 状态
    - 禁用状态下不响应键盘事件

    ## 渲染

    - 未选中: ``[ ] label``
    - 选中:   ``[✓] label``
    - 焦点+未选中: ``[ ] label``（高亮框）
    - 焦点+选中:   ``[✓] label``（高亮框）

    Args:
        label: 复选框标签文本。
        checked: 初始勾选状态（默认 False）。
    """

    # 渲染符号
    CHECKED: str = "✓"
    UNCHECKED: str = " "
    BRACKET_LEFT: str = "["
    BRACKET_RIGHT: str = "]"

    def __init__(
        self,
        label: str = "",
        checked: bool = False,
    ) -> None:
        super().__init__()
        self.label: str = label
        self._checked: bool = checked

        # 回调
        self.on_change: Callable[[bool], None] | None = None

    # ── 属性 ────────────────────────────────────────────

    @property
    def checked(self) -> bool:
        """是否勾选。"""
        return self._checked

    @checked.setter
    def checked(self, value: bool) -> None:
        self._checked = bool(value)

    @property
    def label(self) -> str:
        """复选框标签文本。"""
        return self._label

    @label.setter
    def label(self, value: str) -> None:
        self._label = value

    def toggle(self) -> None:
        """切换勾选状态并触发 on_change。"""
        self._checked = not self._checked
        self._notify_change()

    def check(self) -> None:
        """勾选（无变化时不通知）。"""
        if not self._checked:
            self._checked = True
            self._notify_change()

    def uncheck(self) -> None:
        """取消勾选（无变化时不通知）。"""
        if self._checked:
            self._checked = False
            self._notify_change()

    def _notify_change(self) -> None:
        """触发 on_change 回调。"""
        if self.on_change is not None:
            try:
                self.on_change(self._checked)
            except Exception:
                _logger.exception("Checkbox.on_change() 异常")

    # ── 事件处理 ────────────────────────────────────────

    def on_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件。

        空格/回车切换勾选状态，禁用时返回 False。

        Returns:
            True 表示事件已消费。
        """
        if self._disabled:
            return False

        if event.key in ("space", "enter"):
            self.toggle()
            return True

        return False

    # ── 渲染 ────────────────────────────────────────────

    def _resolve_color(self) -> str:
        """解析渲染颜色。

        选中时使用绿色，未选中时使用 muted 色。
        禁用时使用最暗色。
        """
        if self._disabled:
            return "\033[38;5;237m"
        if self._checked:
            return self.resolve_theme_color("success", "\033[38;5;41m")
        if self._focused:
            return self.resolve_theme_color("border_active", "\033[38;5;45m")
        return self.resolve_theme_color("muted", "\033[38;5;237m")

    def render(self) -> str:
        """渲染复选框。

        格式: ``[✓] label`` 或 ``[ ] label``。
        """
        if not self._visible:
            return ""

        reset = "\033[0m"
        indicator = self.CHECKED if self._checked else self.UNCHECKED
        color = self._resolve_color()

        bracket = f"{self.BRACKET_LEFT}{indicator}{self.BRACKET_RIGHT}"
        return f"{color}{bracket} {self._label}{reset}"
