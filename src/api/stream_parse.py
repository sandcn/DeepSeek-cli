"""流式工具调用解析模块 — 从 api/tool_parse.py 拆分而来

包含：工具调用格式转换（convert_tool_calls_map, parse_raw_tool_calls）
和流式解析计时器（ToolParseTracker）。
"""

from __future__ import annotations

import logging
import time
import asyncio

from ._tool_parse_utils import convert_tool_calls_map, parse_raw_tool_calls  # noqa: F401 — 重导出
from .tokens import estimate_tokens
from .stats import set_tool_parse_elapsed
from .interrupt_async import is_interrupted_async
from ..tools.registry import get_tool_display_name

_logger = logging.getLogger(__name__)


# ── 解析计时器 ──

class ToolParseTracker:
    """管理流式工具调用解析的计时与动态显示。

    全异步实现：使用 asyncio.Task 替代 threading.Thread，
    使用 asyncio.Event 替代 threading.Event。
    """

    def __init__(self, tool_calls_map, display=None, label=None, silent=False):
        self._tool_calls_map = tool_calls_map
        self._display = display
        self._label = label
        self._start_time = None
        self._task: asyncio.Task | None = None
        self._interrupted = False

    async def start(self):
        """首次检测到工具调用时调用，启动计时。"""
        self._start_time = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._update_loop_async())

    async def _update_loop_async(self):
        """异步更新循环：每秒刷新5次，仅更新 display（不打印终端）。"""
        try:
            while True:
                if await is_interrupted_async():
                    self._interrupted = True
                    break

                elapsed = time.monotonic() - self._start_time
                snapshot = [{**tc} for tc in self._tool_calls_map.values()]
                total_args = ''.join(tc["arguments"] for tc in snapshot)
                tokens = estimate_tokens(total_args)
                names = [tc["name"] for tc in snapshot if tc["name"]]
                name_str = ','.join(get_tool_display_name(n) for n in names) if names else '工具'
                if self._display is not None and self._label is not None:
                    try:
                        self._display.update_parse_info(self._label, name_str, tokens, elapsed)
                    except Exception:
                        _logger.debug("update_parse_info 失败（非关键）")
                await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            _logger.debug("ToolParseTracker update task cancelled")
            raise

    @property
    def started(self):
        return self._start_time is not None

    @property
    def elapsed(self):
        if self._start_time is None:
            return 0.0
        return time.monotonic() - self._start_time

    @property
    def interrupted(self):
        return self._interrupted

    async def finalize(self):
        """完成工具调用解析，更新全局状态。"""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._start_time is None:
            set_tool_parse_elapsed(0.0)
            return

        elapsed = time.monotonic() - self._start_time
        set_tool_parse_elapsed(elapsed)

        snapshot = dict(self._tool_calls_map)
        total_args = ''.join(tc["arguments"] for tc in snapshot.values())
        tokens = estimate_tokens(total_args)
        names = [tc["name"] for tc in snapshot.values() if tc["name"]]
        name_str = ','.join(get_tool_display_name(n) for n in names) if names else '工具'

        if self._display is not None and self._label is not None:
            try:
                self._display.update_parse_info(self._label, name_str, tokens, elapsed)
            except Exception:
                _logger.debug("update_parse_info(finalize) 失败（非关键）")
            try:
                self._display.parse_info_done(self._label)
            except Exception:
                _logger.debug("parse_info_done 失败（非关键）")
