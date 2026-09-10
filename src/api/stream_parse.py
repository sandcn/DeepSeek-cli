"""流式工具调用解析模块 — 从 api/tool_parse.py 拆分而来

包含：工具调用格式转换（convert_tool_calls_map, convert_tool_calls_map_with_status,
parse_raw_tool_calls, parse_raw_tool_calls_with_status）
和流式解析计时器（ToolParseTracker）。
"""

from __future__ import annotations

import logging
import time
import asyncio

from ._tool_parse_utils import convert_tool_calls_map, convert_tool_calls_map_with_status, parse_raw_tool_calls, parse_raw_tool_calls_with_status, full_args_str  # noqa: F401 — 重导出
from .tokens import estimate_tokens
from .stats import set_tool_parse_elapsed
from .interrupt_async import is_interrupted_async
from ..tools.registry import get_tool_display_name

_logger = logging.getLogger(__name__)


#: 解析进度刷新间隔（秒）——10Hz（每秒 10 拍）。
#: ★ 2026-09-10 修复（用户需求：``⠙ Write 384t 1.01s`` 不会每 10Hz 刷新信息）：
#:   修复前为 0.2s（5Hz）——解析进度行（工具名 + token 数 + 耗时）每 0.2s 才
#:   推送一次 ``update_parse_info``，而 TUI 渲染循环为 **10Hz**、进度行 spinner
#:   帧序列（``_fx.spinner_char``）也按 10Hz 逐帧推进：spinner 每帧都在动，
#:   但 token/耗时两拍才动一次（0.2s 跳变，如 ``1.01s → 1.21s``），视觉上
#:   「信息不刷新」。现与渲染循环同频（10Hz）——每拍推送一次，渲染循环每帧
#:   消费一次，进度行信息随 spinner 平滑刷新。
_PARSE_INFO_REFRESH_INTERVAL = 0.1


# ── 解析计时器 ──

class ToolParseTracker:
    """管理流式工具调用解析的计时与动态显示。

    全异步实现：使用 asyncio.Task 替代 threading.Thread，
    使用 asyncio.Event 替代 threading.Event。

    刷新频率：``REFRESH_INTERVAL``（10Hz）——与 TUI 渲染循环／解析进度行
    spinner 帧率对齐（见 ``_PARSE_INFO_REFRESH_INTERVAL`` 注释）。
    """

    #: 解析进度刷新间隔（秒）——10Hz。类属性便于测试注入（惰性读取）。
    REFRESH_INTERVAL = _PARSE_INFO_REFRESH_INTERVAL

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
        """异步更新循环：每秒刷新 10 次（10Hz），仅更新 display（不打印终端）。

        间隔取 ``self.REFRESH_INTERVAL``（10Hz）——与 TUI 渲染循环 10Hz 对齐
        （修复前 0.2s＝5Hz：spinner 每帧推进而 token/耗时两拍一更，进度行
        信息「不随 10Hz 刷新」）。
        """
        try:
            while True:
                if await is_interrupted_async():
                    self._interrupted = True
                    break

                elapsed = time.monotonic() - self._start_time
                snapshot = [{**tc} for tc in self._tool_calls_map.values()]
                total_args = ''.join(full_args_str(tc) for tc in snapshot)
                tokens = estimate_tokens(total_args)
                names = [tc["name"] for tc in snapshot if tc["name"]]
                name_str = ','.join(get_tool_display_name(n) for n in names) if names else '工具'
                if self._display is not None and self._label is not None:
                    try:
                        self._display.update_parse_info(self._label, name_str, tokens, elapsed)
                    except Exception:
                        _logger.debug("update_parse_info 失败（非关键）")
                # ★ 10Hz（0.1s）——与渲染循环同帧率；每拍刷新（不累积多拍后
                #   一次推送，否则进度行信息又退化为低频跳变）。
                await asyncio.sleep(self.REFRESH_INTERVAL)
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
        total_args = ''.join(full_args_str(tc) for tc in snapshot.values())
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
