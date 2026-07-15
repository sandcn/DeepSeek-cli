"""Menu 菜单控件 — 垂直/水平选项列表，支持键盘导航和选择回调。

设计模式: 策略 — ``horizontal`` 参数切换渲染策略（垂直/水平布局）。
"""

from __future__ import annotations

import logging
from typing import Callable

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.base import Widget

_logger = logging.getLogger(__name__)


class Menu(Widget):
    """菜单控件。

    ## 交互行为

    - ``up`` / ``down`` → 导航选项（支持循环/非循环两种模式）
    - ``enter`` → 触发 ``on_select(action_id)`` 回调
    - ``escape`` → 取消选中（触发 ``on_cancel``，若已注册）

    ## 渲染

    **垂直模式**（默认）:
        每行一个选项，当前选中项高亮。

    **水平模式**（``horizontal=True``）:
        选项单行水平排列，以分隔符间隔。

    ## 数据结构

    每个菜单项为 ``(label, action_id)`` 元组：
    - ``label``: 显示文本
    - ``action_id``: 选中时传递给 ``on_select`` 的动作标识符

    Args:
        items: 菜单项列表，每项为 ``(label, action_id)`` 元组。
        active_index: 初始激活项索引（默认 0）。
        horizontal: 是否水平菜单模式（默认 False，垂直模式）。
        wrap_around: 导航是否循环（默认 True）。
    """

    # 渲染常量
    PREFIX_VERTICAL: str = "  "
    SEQUENCE_HORIZONTAL: str = " │ "

    def __init__(
        self,
        items: list[tuple[str, str]] | None = None,
        active_index: int = 0,
        horizontal: bool = False,
        wrap_around: bool = True,
    ) -> None:
        super().__init__()
        self._items: list[tuple[str, str]] = list(items) if items else []
        self._active_index: int = active_index
        self.horizontal: bool = horizontal
        self.wrap_around: bool = wrap_around

        # 回调
        self.on_select: Callable[[str], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_change: Callable[[int, str, str], None] | None = None

    # ── 属性 ────────────────────────────────────────────

    @property
    def items(self) -> list[tuple[str, str]]:
        """菜单项列表 ``[(label, action_id), ...]``。"""
        return self._items

    @items.setter
    def items(self, value: list[tuple[str, str]]) -> None:
        self._items = list(value)
        self._clamp_index()

    @property
    def active_index(self) -> int:
        """当前激活项索引。"""
        return self._active_index

    @active_index.setter
    def active_index(self, value: int) -> None:
        self._active_index = value
        self._clamp_index()

    @property
    def active_item(self) -> tuple[str, str] | None:
        """当前激活项 ``(label, action_id)``，无选项时返回 None。"""
        if 0 <= self._active_index < len(self._items):
            return self._items[self._active_index]
        return None

    @property
    def count(self) -> int:
        """菜单项数量。"""
        return len(self._items)

    # ── 内部方法 ────────────────────────────────────────

    def _clamp_index(self) -> None:
        """Clamp 激活索引到有效范围。"""
        if not self._items:
            self._active_index = 0
            return
        if self._active_index < 0:
            self._active_index = 0
        if self._active_index >= len(self._items):
            self._active_index = len(self._items) - 1

    def _move_up(self) -> None:
        """激活项上移。"""
        if not self._items:
            return
        if self._active_index > 0:
            self._active_index -= 1
        elif self.wrap_around:
            self._active_index = len(self._items) - 1
        else:
            return
        self._notify_change()

    def _move_down(self) -> None:
        """激活项下移。"""
        if not self._items:
            return
        if self._active_index < len(self._items) - 1:
            self._active_index += 1
        elif self.wrap_around:
            self._active_index = 0
        else:
            return
        self._notify_change()

    def _notify_change(self) -> None:
        """触发 on_change 回调。"""
        if self.on_change is not None and self.active_item is not None:
            try:
                label, action_id = self.active_item
                self.on_change(self._active_index, label, action_id)
            except Exception:
                _logger.exception("Menu.on_change() 异常")

    def _notify_select(self) -> None:
        """触发 on_select 回调。"""
        if self.on_select is not None and self.active_item is not None:
            try:
                self.on_select(self.active_item[1])
            except Exception:
                _logger.exception("Menu.on_select() 异常")

    # ── 事件处理 ────────────────────────────────────────

    def on_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件。"""
        if self._disabled or not self._visible:
            return False

        key = event.key

        if key == "up":
            self._move_up()
            return True
        if key == "down":
            self._move_down()
            return True
        if key == "enter":
            self._notify_select()
            return True
        if key == "escape":
            if self.on_cancel is not None:
                try:
                    self.on_cancel()
                except Exception:
                    _logger.exception("Menu.on_cancel() 异常")
            return True

        return False

    # ── 渲染 ────────────────────────────────────────────

    def _resolve_color_active(self) -> str:
        """解析当前激活项颜色。"""
        return self.resolve_theme_color("highlight", "\033[38;5;45m")

    def _resolve_color_normal(self) -> str:
        """解析非激活项颜色。"""
        return self.resolve_theme_color("muted", "\033[38;5;242m")

    def _resolve_color_indicator(self) -> str:
        """解析指示符颜色。"""
        return self.resolve_theme_color("accent", "\033[38;5;221m")

    def render(self) -> str:
        """渲染菜单。"""
        if not self._visible:
            return ""

        if not self._items:
            return ""

        if self.horizontal:
            return self._render_horizontal()
        return self._render_vertical()

    def _render_vertical(self) -> str:
        """渲染垂直菜单。"""
        reset = "\033[0m"
        active_color = self._resolve_color_active()
        normal_color = self._resolve_color_normal()
        indicator_color = self._resolve_color_indicator()

        lines: list[str] = []
        for i, (label, _action_id) in enumerate(self._items):
            if i == self._active_index:
                prefix = f"{indicator_color}▶{reset}"
                color = active_color
            else:
                prefix = " "
                color = normal_color
            lines.append(f"{self.PREFIX_VERTICAL}{prefix} {color}{label}{reset}")

        return "\n".join(lines)

    def _render_horizontal(self) -> str:
        """渲染水平菜单。"""
        reset = "\033[0m"
        active_color = self._resolve_color_active()
        normal_color = self._resolve_color_normal()

        parts: list[str] = []
        for i, (label, _action_id) in enumerate(self._items):
            if i == self._active_index:
                parts.append(f"{active_color}▶{label}{reset}")
            else:
                parts.append(f"{normal_color}{label}{reset}")

        return self.SEQUENCE_HORIZONTAL.join(parts)
