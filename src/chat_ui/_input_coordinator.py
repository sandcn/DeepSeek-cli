"""chat_ui 输入协调模块 — ChatUIInputCoordinator 管理用户输入和补全系统。

从 _consumer.py 提取，封装输入等待、补全配置、底部栏设置/拆除。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._consumer import ChatUIConsumer
    from ._protocols import BottomBarProtocol
    from ._completion import _CmplHandler


def _try_focus_navigate(key: str, is_shift: bool = False) -> bool:
    """公共焦点导航逻辑，供 create_focus_key_handler 和 handle_focus_key 复用。

    当 FocusManager 中有已注册的可聚焦组件时执行焦点遍历，
    无焦点组件或焦点管理被禁用时返回 False。

    Args:
        key: 按键标识（如 'tab'）。
        is_shift: Shift 修饰键是否按下（反向遍历焦点）。

    Returns:
        True 表示按键已被焦点系统处理，应阻止默认行为。
    """
    if key != "tab":
        return False

    from .react_ink._focus import FocusManager

    fm = FocusManager()
    if not fm.has_focusables or not fm.enabled:
        return False

    if is_shift:
        fm.focus_previous()
    else:
        fm.focus_next()
    return True


# @reserved: 待 prompt_toolkit KeyBindings 集成
def create_focus_key_handler():
    """创建焦点遍历按键处理器，适用于 prompt_toolkit 等输入框架的 key_bindings。

    返回的 handler 接受 (key_name, is_shift) 两个参数：
    - key_name='tab', is_shift=False → 正向遍历焦点
    - key_name='tab', is_shift=True  → 反向遍历焦点
    仅在 FocusManager 有已注册组件时处理，否则返回 False。

    Returns:
        callable(key_name: str, is_shift: bool) -> bool

    使用示例（prompt_toolkit）：
        kb = KeyBindings()
        focus_handler = create_focus_key_handler()

        @kb.add('tab')
        def _(event):
            if focus_handler('tab', False):
                return  # 焦点系统已处理
            # 否则执行默认补全...

        @kb.add('s-tab')
        def _(event):
            focus_handler('tab', True)
    """

    def handler(key_name: str = 'tab', is_shift: bool = False) -> bool:
        return _try_focus_navigate(key_name, is_shift)

    return handler


class ChatUIInputCoordinator:
    """ChatUIConsumer 输入协调器。

    职责：
    - 用户输入等待（wait_for_user_input）
    - 补全系统配置（setup_completion）
    - 底部栏设置/拆除（setup_bottom_bar / teardown_bottom_bar）
    """

    def __init__(self, consumer: "ChatUIConsumer"):
        self._consumer = consumer

    def wait_for_user_input(self, monitor, prefill: str = "",
                            timeout: float | None = None) -> str:
        """阻塞等待用户通过 monitor 输入文本。

        轮询 monitor.get_queued_input()，以 50ms 间隔检查。

        Args:
            monitor: 输入监视器，需提供 get_queued_input() / set_prefill()
            prefill: 预填充文本（可选）
            timeout: 超时秒数，None 表示无限等待

        Returns:
            用户输入文本；超时时返回空字符串 ""
        """
        if prefill:
            monitor.set_prefill(prefill)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            text = monitor.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    def setup_completion(self, monitor) -> None:
        """为监视器配置补全回调。

        Args:
            monitor: 输入监视器，需提供 set_completion_callback /
                     set_dismiss_completion_callback / set_completion_navigate_callback /
                     set_auto_completion_callback
        """
        cmpl: "_CmplHandler" = self._consumer._cmpl
        monitor.set_completion_callback(cmpl.on_tab)
        monitor.set_dismiss_completion_callback(cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(cmpl.on_navigate)
        monitor.set_auto_completion_callback(cmpl.on_auto)

    def setup_bottom_bar(self, output_lock) -> None:
        """设置底部栏（初始状态）。"""
        with output_lock:
            self._consumer._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        """拆除底部栏。"""
        self._consumer._bottom_bar.teardown()

    # @reserved: 待 prompt_toolkit KeyBindings 集成
    def handle_focus_key(self, key: str, shift_pressed: bool = False) -> bool:
        """处理焦点导航按键（Tab / Shift+Tab）。

        委托给 _try_focus_navigate()，当 FocusManager 中有已注册的可聚焦组件时：
        - key='tab', shift_pressed=False → 正向遍历焦点
        - key='tab', shift_pressed=True  → 反向遍历焦点
        无焦点组件或焦点管理被禁用时返回 False，由调用方执行默认行为。

        Args:
            key: 按键标识（如 'tab'）。
            shift_pressed: Shift 修饰键是否按下。

        Returns:
            True 表示按键已被焦点系统处理，应阻止默认行为。
        """
        return _try_focus_navigate(key, shift_pressed)
