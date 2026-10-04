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
from ..core.stats import set_tool_parse_elapsed
from .interrupt_async import is_interrupted_async
from ..core.tool_display import get_tool_display_name

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

#: 解析进度推送异常告警限频（秒）——单拍异常不终止推送循环（见
#: ``ToolParseTracker._update_loop_async``），同频率窗口内仅记 1 条 warning
#: （其余记 debug），防持续异常刷屏。
_PUSH_ERR_LOG_INTERVAL = 5.0


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
        #: 上次推送异常告警时间戳（5s 限频，见 ``_log_push_error``）
        self._last_err_log: float = 0.0

    async def start(self):
        """首次检测到工具调用时调用，启动计时。"""
        self._start_time = time.monotonic()
        self._task = asyncio.get_running_loop().create_task(self._update_loop_async())

    async def _update_loop_async(self):
        """异步更新循环：每秒刷新 10 次（10Hz），仅更新 display（不打印终端）。

        间隔取 ``self.REFRESH_INTERVAL``（10Hz）——与 TUI 渲染循环 10Hz 对齐
        （修复前 0.2s＝5Hz：spinner 每帧推进而 token/耗时两拍一更，进度行
        信息「不随 10Hz 刷新」）。

        ★ 2026-09-20（解析进度行卡住修复）：**单拍异常不再终止推送循环**——
        修复前循环体除 CancelledError 外无捕获，任一单拍异常（条目缺 ``name``
        键的 KeyError、非字符串工具名导致的 join TypeError 等）会静默结束本
        asyncio 任务（仅留下 "Task exception was never retrieved"），此后解析
        进度行**永远停在上一次推送值**（工具参数仍在接收 → 用户看到「数字卡住、
        数据有传过来」）。异常记 warning（5s 限频）后继续下一拍。
        """
        try:
            while True:
                if await is_interrupted_async():
                    self._interrupted = True
                    break
                try:
                    await self._push_once()
                except Exception:
                    self._log_push_error()
                # ★ 10Hz（0.1s）——与渲染循环同帧率；每拍刷新（不累积多拍后
                #   一次推送，否则进度行信息又退化为低频跳变）。
                await asyncio.sleep(self.REFRESH_INTERVAL)
        except asyncio.CancelledError:
            _logger.debug("ToolParseTracker update task cancelled")
            raise

    async def _push_once(self) -> None:
        """推送一拍解析进度（工具名 + token 估算 + 耗时）。

        与 ``finalize`` 共享同一计算口径（args_parts 全量 join → estimate_tokens
        → 显示名映射）。
        """
        elapsed = time.monotonic() - self._start_time
        name_str, tokens = self._snapshot_progress()
        if self._display is not None and self._label is not None:
            try:
                self._display.update_parse_info(self._label, name_str, tokens, elapsed)
            except Exception:
                _logger.debug("update_parse_info 失败（非关键）")

    def _snapshot_progress(self) -> tuple[str, int]:
        """当前工具调用累积状态的显示名串与 token 估算（防御式）。

        条目结构异常（缺 ``name`` 键 / 非字符串工具名 / 参数片段非字符串）
        一律**就地归一化**而非抛出——若每拍都抛（如非字符串工具名），推送被
        整体跳过，进度行数字仍会停住（与终止循环同样的用户可见症状）；外层
        捕获仅作为未知异常的兜底。

        Returns:
            (name_str, tokens)。
        """
        snapshot = [{**tc} for tc in self._tool_calls_map.values()]
        names: list[str] = []
        arg_parts: list[str] = []
        for tc in snapshot:
            name = tc.get("name")
            if name:
                names.append(name if isinstance(name, str) else str(name))
            parts = tc.get("_args_parts")
            if parts:
                arg_parts.extend(p if isinstance(p, str) else str(p) for p in parts)
                continue
            legacy = tc.get("arguments", "")
            arg_parts.append(legacy if isinstance(legacy, str) else str(legacy))
        tokens = estimate_tokens("".join(arg_parts))
        name_str = ','.join(get_tool_display_name(n) for n in names) if names else '工具'
        return name_str, tokens

    def _log_push_error(self) -> None:
        """记录推送异常（同会话 5s 限频，防高频刷屏）。"""
        now = time.monotonic()
        if now - self._last_err_log >= _PUSH_ERR_LOG_INTERVAL:
            self._last_err_log = now
            _logger.warning("解析进度推送异常（已跳过本拍，继续下一拍）", exc_info=True)
        else:
            _logger.debug("解析进度推送异常（限频抑制）", exc_info=True)

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

        # ★ 2026-09-20：与 ``_push_once`` 共用同一防御口径——条目结构异常
        #   （缺 name 键/非字符串工具名等）不得中断流收尾清理（修复前异常
        #   会穿透到 ``_cleanup_display``，进度行清除与渲染器收尾被跳过）。
        try:
            name_str, tokens = self._snapshot_progress()
        except Exception:
            _logger.warning("解析进度快照失败（finalize 降级为默认值）", exc_info=True)
            name_str, tokens = '工具', 0

        if self._display is not None and self._label is not None:
            try:
                self._display.update_parse_info(self._label, name_str, tokens, elapsed)
            except Exception:
                _logger.debug("update_parse_info(finalize) 失败（非关键）")
            try:
                self._display.parse_info_done(self._label)
            except Exception:
                _logger.debug("parse_info_done 失败（非关键）")
