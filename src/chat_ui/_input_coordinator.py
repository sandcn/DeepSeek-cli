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
