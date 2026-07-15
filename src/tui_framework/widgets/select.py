"""Select 下拉选择控件 — 支持选项导航、展开/收起和选中确认。

设计模式: 状态 — ``expanded`` 状态控制渲染模式（收起/展开）。

动效：展开时触发 ``slide_in`` 滑入动画。
"""

from __future__ import annotations

import logging
from typing import Callable

from tui_framework.animation.declarative import effect
from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.animated import AnimatedWidget

_logger = logging.getLogger(__name__)


@effect("expand", type="slide_in", duration=6, easing="smooth")
class Select(AnimatedWidget):
    """下拉选择控件。

    ## 交互行为

    **收起状态**:
    - ``enter`` / ``space`` → 展开选项列表
    - ``up`` / ``down`` → 切换选中项（在收起状态也可导航，但不展开显示）

    **展开状态**:
    - ``up`` → 上一项
    - ``down`` → 下一项
    - ``enter`` → 确认选中并收起
    - ``space`` → 确认选中并收起
    - ``escape`` → 取消并收起（恢复原选中项）

    ## 渲染

    - 收起时: ``[ 当前选项 ▼ ]``
    - 展开时: 垂直选项列表，高亮当前选中项，以 ``●`` 指示选中

    Args:
        options: 选项文本列表。
        selected_index: 初始选中项索引（默认 0）。
    """

    # 渲染字符
    ARROW_DOWN: str = "▼"
    ARROW_UP: str = "▲"
    INDICATOR_SELECTED: str = "●"
    INDICATOR_UNSELECTED: str = " "
    PREFIX_EXPANDED: str = "  "

    def __init__(
        self,
        options: list[str] | None = None,
        selected_index: int = 0,
    ) -> None:
        super().__init__()
        self._options: list[str] = list(options) if options else []
        self._expanded: bool = False
        # Clamp selected_index
        if self._options:
            self._selected_index: int = max(0, min(selected_index, len(self._options) - 1))
        else:
            self._selected_index: int = 0
        self._saved_index: int = self._selected_index  # ESC 恢复用

        # 回调
        self.on_change: Callable[[int, str], None] | None = None

    # ── 属性 ────────────────────────────────────────────

    @property
    def options(self) -> list[str]:
        """选项列表。"""
        return self._options

    @options.setter
    def options(self, value: list[str]) -> None:
        self._options = list(value)
        self._selected_index = min(self._selected_index, len(self._options) - 1)
        if self._selected_index < 0 and self._options:
            self._selected_index = 0

    @property
    def selected_index(self) -> int:
        """当前选中项索引。"""
        return self._selected_index

    @selected_index.setter
    def selected_index(self, value: int) -> None:
        if self._options:
            self._selected_index = max(0, min(value, len(self._options) - 1))
        else:
            self._selected_index = 0

    @property
    def selected_option(self) -> str:
        """当前选中项文本。"""
        if 0 <= self._selected_index < len(self._options):
            return self._options[self._selected_index]
        return ""

    @property
    def expanded(self) -> bool:
        """是否处于展开状态。"""
        return self._expanded

    # ── 内部方法 ────────────────────────────────────────

    def _clamp_index(self) -> None:
        """Clamp 选中索引到有效范围。"""
        if not self._options:
            self._selected_index = 0
            return
        if self._selected_index < 0:
            self._selected_index = 0
        if self._selected_index >= len(self._options):
            self._selected_index = len(self._options) - 1

    def _move_up(self) -> None:
        """选中项上移。"""
        if self._options and self._selected_index > 0:
            self._selected_index -= 1
            self._notify_change()

    def _move_down(self) -> None:
        """选中项下移。"""
        if self._options and self._selected_index < len(self._options) - 1:
            self._selected_index += 1
            self._notify_change()

    def _expand(self) -> None:
        """展开选项列表（触发滑入动效）。"""
        if self._options:
            self._expanded = True
            self._saved_index = self._selected_index
            self.trigger_effect("expand")

    def _collapse(self, confirm: bool) -> None:
        """收起选项列表。

        Args:
            confirm: True=确认选中，False=取消恢复原选中项。
        """
        if not confirm:
            self._selected_index = self._saved_index
        self._expanded = False

    def _notify_change(self) -> None:
        """触发 on_change 回调。"""
        if self.on_change is not None:
            try:
                self.on_change(self._selected_index, self.selected_option)
            except Exception:
                _logger.exception("Select.on_change() 异常")

    # ── 事件处理 ────────────────────────────────────────

    def on_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件。"""
        if self._disabled:
            return False

        key = event.key

        if self._expanded:
            # 展开状态
            if key == "up":
                self._move_up()
                return True
            if key == "down":
                self._move_down()
                return True
            if key in ("enter", "space"):
                self._collapse(confirm=True)
                self._notify_change()
                return True
            if key == "escape":
                self._collapse(confirm=False)
                return True
        else:
            # 收起状态
            if key in ("enter", "space"):
                self._expand()
                return True
            if key == "up":
                self._move_up()
                return True
            if key == "down":
                self._move_down()
                return True

        return False

    # ── 渲染 ────────────────────────────────────────────

    def _resolve_border_color(self) -> str:
        """解析边框颜色。"""
        if self._focused:
            return self.resolve_theme_color("border_active", "\033[38;5;45m")
        return self.resolve_theme_color("border", "\033[38;5;239m")

    def render(self) -> str:
        """渲染选择控件。"""
        if not self._visible:
            return ""

        reset = "\033[0m"
        border_color = self._resolve_border_color()

        if not self._options:
            return f"{border_color}[ 无选项 ]{reset}"

        if self._expanded:
            content = self._render_expanded(border_color, reset)
            return self._apply_effects(content)

        return self._render_collapsed(border_color, reset)

    def _render_collapsed(self, border_color: str, reset: str) -> str:
        """渲染收起状态。"""
        option = self.selected_option
        arrow = self.ARROW_DOWN
        return f"{border_color}[ {option} {arrow} ]{reset}"

    def _render_expanded(self, border_color: str, reset: str) -> str:
        """渲染展开状态。"""
        highlight = self.resolve_theme_color("highlight", "\033[38;5;45m")
        lines: list[str] = []

        # 顶部边框
        lines.append(f"{border_color}┌─ {self.ARROW_UP} 展开 ─┐{reset}")

        # 选项列表
        for i, opt in enumerate(self._options):
            if i == self._selected_index:
                indicator = self.INDICATOR_SELECTED
                color = highlight
            else:
                indicator = self.INDICATOR_UNSELECTED
                color = ""
            lines.append(f"{self.PREFIX_EXPANDED}{color}{indicator} {opt}{reset}")

        # 底部边框
        lines.append(f"{border_color}└{'─' * 10}┘{reset}")

        return "\n".join(lines)
