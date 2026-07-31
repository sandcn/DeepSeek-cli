"""TuiInputOrchestrator — 用户输入等待编排器。

从 ChatUIConsumer.wait_for_user_input() 提取为独立类，
负责输入等待轮询、prefill 注入和残留输入排空。

单一职责：
  - 阻塞等待用户输入（轮询 Input.get_queued_input()）
  - prefill 文本注入 + 残留输入排空
  - EscapeMonitor 存活检测
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.tui._input import Input

_logger = logging.getLogger(__name__)


class TuiInputOrchestrator:
    """用户输入等待编排器。

    管理用户输入等待的完整生命周期，包括 prefill 注入、
    残留输入排空和超时处理。
    """

    def __init__(self, input_instance: "Input"):
        self._input = input_instance

    def wait_for_user_input(
        self,
        monitor,
        prefill: str = "",
        timeout: float | None = None,
        input_=None,
    ) -> str:
        """阻塞等待用户通过 Input 实例输入文本。

        轮询 ``input_.get_queued_input()``，以 50ms 间隔检查。

        Args:
            monitor: EscapeMonitor 实例，用于 is_alive 存活检测。
            prefill: 预填充文本（可选）。
            timeout: 超时秒数，None 表示无限等待。
            input_: 统一输入管理实例。None 时使用构造时注入的实例。

        Returns:
            用户输入文本；超时时返回空字符串 ``""``。
        """
        if input_ is None:
            input_ = self._input

        if prefill:
            if not monitor.is_alive:
                raise RuntimeError("EscapeMonitor thread died")
            _logger.debug(
                "wait_for_user_input: set prefill, len=%d", len(prefill),
            )
            # 排空残留的排队输入（stale input），修复 editmsg 截断后
            # 无法立即重新编辑的 bug。
            stale = input_.get_queued_input()
            if stale is not None:
                _logger.debug(
                    "wait_for_user_input: drained stale input %r "
                    "before setting prefill", stale,
                )
            input_.set_buffer(prefill)
            input_.echo(prefill)
            _logger.debug("wait_for_user_input: prefill done, entering poll loop")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if not monitor.is_alive:
                _logger.warning("EscapeMonitor 线程已死亡，退出等待")
                raise RuntimeError("EscapeMonitor thread died")
            text = input_.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)


__all__ = ["TuiInputOrchestrator"]
