"""Input 文本输入控件 — 单行文本输入，支持光标、遮罩、校验。

设计模式: 观察者 — ``on_change``/``on_submit`` 回调通知值变化。
"""

from __future__ import annotations

import logging
from typing import Callable

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.base import Widget

_logger = logging.getLogger(__name__)


class Input(Widget):
    """单行文本输入控件。

    ## 交互行为

    - 可打印字符 → 追加到 ``value``
    - ``backspace`` → 删除末尾字符
    - ``delete`` → 删除光标后字符
    - ``enter`` → 触发 ``on_submit`` 回调
    - ``tab`` → 触发 ``on_tab`` 钩子（用于自动补全）
    - ``escape`` → 取消输入（触发 ``on_cancel``）
    - ``left`` / ``right`` → 移动光标
    - ``home`` / ``end`` → 光标跳转到开头/末尾
    - ``ctrl+u`` → 删除光标前全部
    - ``ctrl+k`` → 删除光标后全部

    ## 渲染

    渲染返回 ``[placeholder]`` 或 ``value``，光标位置以 ``|`` 指示。
    密码模式下 value 以 ``*`` 遮罩显示。

    ## 回调

    - ``on_change(value: str)``: 值变化时调用
    - ``on_submit(value: str)``: 回车提交时调用
    - ``on_cancel()``: ESC 取消时调用
    - ``on_tab(value: str)``: Tab 自动补全钩子

    Args:
        placeholder: 占位符文本（默认空字符串）。
        value: 初始值（默认空字符串）。
        max_length: 最大字符数（0 表示无限制）。
        password: 是否密码模式（值为 True 时显示 ``*``）。
    """

    def __init__(
        self,
        placeholder: str = "",
        value: str = "",
        max_length: int = 0,
        password: bool = False,
    ) -> None:
        super().__init__()
        self._value: str = value
        self._cursor: int = len(value)
        self.placeholder: str = placeholder
        self.max_length: int = max_length
        self.password: bool = password

        # 回调
        self.on_change: Callable[[str], None] | None = None
        self.on_submit: Callable[[str], None] | None = None
        self.on_cancel: Callable[[], None] | None = None
        self.on_tab: Callable[[str], None] | None = None

    # ── 属性 ────────────────────────────────────────────

    @property
    def value(self) -> str:
        """当前输入值。"""
        return self._value

    @value.setter
    def value(self, v: str) -> None:
        self._value = v
        self._cursor = min(self._cursor, len(v))
        if self._cursor < 0:
            self._cursor = len(v)

    @property
    def cursor(self) -> int:
        """光标位置（字符索引，0-based）。"""
        return self._cursor

    # ── 内部方法 ────────────────────────────────────────

    def _clamp_cursor(self) -> None:
        """将光标 clamp 到有效范围。"""
        if self._cursor < 0:
            self._cursor = 0
        if self._cursor > len(self._value):
            self._cursor = len(self._value)

    def _insert_char(self, ch: str) -> None:
        """在光标位置插入字符。"""
        if self.max_length > 0 and len(self._value) >= self.max_length:
            return
        self._value = self._value[:self._cursor] + ch + self._value[self._cursor:]
        self._cursor += 1

    def _delete_before(self) -> None:
        """删除光标前一个字符。"""
        if self._cursor > 0:
            self._value = self._value[:self._cursor - 1] + self._value[self._cursor:]
            self._cursor -= 1

    def _delete_after(self) -> None:
        """删除光标后一个字符。"""
        if self._cursor < len(self._value):
            self._value = self._value[:self._cursor] + self._value[self._cursor + 1:]

    def _delete_to_start(self) -> None:
        """删除光标前全部字符（Ctrl+U）。"""
        if self._cursor > 0:
            self._value = self._value[self._cursor:]
            self._cursor = 0

    def _delete_to_end(self) -> None:
        """删除光标后全部字符（Ctrl+K）。"""
        if self._cursor < len(self._value):
            self._value = self._value[:self._cursor]

    def _notify_change(self) -> None:
        """触发 on_change 回调。"""
        if self.on_change is not None:
            try:
                self.on_change(self._value)
            except Exception:
                _logger.exception("Input.on_change() 异常")

    def _notify_submit(self) -> None:
        """触发 on_submit 回调。"""
        if self.on_submit is not None:
            try:
                self.on_submit(self._value)
            except Exception:
                _logger.exception("Input.on_submit() 异常")

    # ── 事件处理 ────────────────────────────────────────

    def on_key(self, event: KeyPressEvent) -> bool:
        """处理键盘事件。

        Returns:
            True 表示事件已消费。
        """
        key = event.key

        # ── 导航键 ──
        if key == "left":
            self._cursor = max(0, self._cursor - 1)
            return True
        if key == "right":
            self._cursor = min(len(self._value), self._cursor + 1)
            return True
        if key == "home":
            self._cursor = 0
            return True
        if key == "end":
            self._cursor = len(self._value)
            return True

        # ── 编辑键 ──
        if key == "backspace":
            self._delete_before()
            self._notify_change()
            return True
        if key == "delete":
            self._delete_after()
            self._notify_change()
            return True

        # ── 控制组合键 ──
        if event.ctrl and key == "u":
            self._delete_to_start()
            self._notify_change()
            return True
        if event.ctrl and key == "k":
            self._delete_to_end()
            self._notify_change()
            return True

        # ── 动作键 ──
        if key == "enter":
            self._notify_submit()
            return True
        if key == "escape":
            if self.on_cancel is not None:
                try:
                    self.on_cancel()
                except Exception:
                    _logger.exception("Input.on_cancel() 异常")
            return True
        if key == "tab":
            if self.on_tab is not None:
                try:
                    self.on_tab(self._value)
                except Exception:
                    _logger.exception("Input.on_tab() 异常")
            return True

        # ── 可打印字符 ──
        if len(key) == 1 and not event.ctrl and not event.alt:
            self._insert_char(key)
            self._notify_change()
            return True

        return False

    # ── 渲染 ────────────────────────────────────────────

    def render(self) -> str:
        """渲染输入控件。

        密码模式下以 ``*`` 遮罩显示，无值时显示 placeholder。
        """
        if not self._visible:
            return ""

        display: str
        if self.password and self._value:
            display = "*" * len(self._value)
        elif self._value:
            display = self._value
        else:
            display = self.placeholder

        cursor_pos = self._cursor

        # 在光标位置插入指示符
        if self._focused:
            # 光标位置在 display 范围内
            if cursor_pos >= len(display):
                return display + "|"
            return display[:cursor_pos] + "|" + display[cursor_pos:]
        return display
