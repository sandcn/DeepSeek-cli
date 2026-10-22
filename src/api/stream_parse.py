"""流式工具调用解析模块 — 从 api/tool_parse.py 拆分而来

包含：工具调用格式转换（convert_tool_calls_map, convert_tool_calls_map_with_status,
parse_raw_tool_calls, parse_raw_tool_calls_with_status）
和流式解析计时器（ToolParseTracker）。
"""

from __future__ import annotations

import logging
import time

from ._tool_parse_utils import convert_tool_calls_map, convert_tool_calls_map_with_status, parse_raw_tool_calls, parse_raw_tool_calls_with_status, full_args_str  # noqa: F401 — 重导出
from .tokens import estimate_tokens
from ..core.stats import set_tool_parse_elapsed
from ..core.interrupt_state import is_interrupted
from ..core.frame_tick import get_frame_tick_registry
from ..core.tool_display import get_tool_display_name

_logger = logging.getLogger(__name__)


#: 解析进度刷新间隔（秒）——60Hz（每秒 60 拍；兼容常量）。
#: ★ 2026-09-10 修复（用户需求：``⠙ Write 384t 1.01s`` 不会每帧刷新信息）：
#:   修复前为 0.2s（5Hz）——解析进度行（工具名 + token 数 + 耗时）每 0.2s 才
#:   推送一次 ``update_parse_info``，而 TUI 渲染循环为 **60Hz**、进度行 spinner
#:   帧序列（``_fx.spinner_char``）也按 60Hz 逐帧推进：spinner 每帧都在动，
#:   但 token/耗时几拍才动一次（0.2s 跳变，如 ``1.01s → 1.21s``），视觉上
#:   「信息不刷新」。现与渲染循环同频（60Hz）——每拍推送一次，渲染循环每帧
#:   消费一次，进度行信息随 spinner 平滑刷新。
#: ★ 2026-10-20（用户需求：render 线程跟 input 处理改成 60hz，不能降减少和
#:   降级）：随渲染循环 30Hz → 60Hz 同步提升，保持「每帧推送一次」不降采样。
#: ★ 2026-10-23（用户需求：单次调用完成所有工作，像游戏引擎的 game_loop）：
#:   推送不再由本模块自建 asyncio 定时任务驱动——``ToolParseTracker.tick``
#:   注册到 ``core.frame_tick``，由 TUI render 线程每帧调用一次；本常量仅
#:   保留为兼容导出（不再参与调度）。
_PARSE_INFO_REFRESH_INTERVAL = 1.0 / 60

#: 解析进度推送异常告警限频（秒）——单拍异常不终止后续刷新（见
#: ``ToolParseTracker.tick``），同频率窗口内仅记 1 条 warning（其余记 debug），
#: 防持续异常刷屏。
_PUSH_ERR_LOG_INTERVAL = 5.0

#: 兼容重导出面：这些符号的实现位于 ``api._tool_parse_utils``，本模块保留
#: 旧导入路径（``from .stream_parse import convert_tool_calls_map`` 等）。
__all__ = [
    "ToolParseTracker",
    "_PARSE_INFO_REFRESH_INTERVAL",
    "_PUSH_ERR_LOG_INTERVAL",
    "convert_tool_calls_map",
    "convert_tool_calls_map_with_status",
    "parse_raw_tool_calls",
    "parse_raw_tool_calls_with_status",
    "full_args_str",
]


# ── 解析计时器 ──

class ToolParseTracker:
    """管理流式工具调用解析的计时与动态显示。

    ★ 2026-10-23（用户需求：render 线程只有 60Hz 且不能改变；单次调用完成
    所有工作，像游戏引擎的 game_loop）：**不再自建 asyncio 60Hz 定时任务**。
    ``start()`` 把 ``tick`` 注册到 ``core.frame_tick`` 全局注册表，由 TUI
    render 线程每帧（``FRAME_TICKS`` 阶段）调用一次——解析进度与渲染循环
    共用同一节拍，进程内只有一套 60Hz 驱动。

    刷新频率：与渲染循环同帧率（60Hz；``REFRESH_INTERVAL`` 保留为兼容常量）。
    """

    #: 解析进度刷新间隔（秒）——60Hz（兼容常量；实际节拍由 render 帧驱动）。
    REFRESH_INTERVAL = _PARSE_INFO_REFRESH_INTERVAL

    def __init__(self, tool_calls_map, display=None, label=None, silent=False):
        self._tool_calls_map = tool_calls_map
        self._display = display
        self._label = label
        self._start_time = None
        #: 兼容字段：帧驱动后不再创建 asyncio 任务（保留属性供既有清理代码读取）
        self._task = None
        self._interrupted = False
        #: 是否已注册到帧驱动注册表（幂等注销）
        self._registered = False
        #: 上次推送异常告警时间戳（5s 限频，见 ``_log_push_error``）
        self._last_err_log: float = 0.0

    async def start(self):
        """首次检测到工具调用时调用，启动计时并注册帧驱动。

        保留 ``async`` 签名以兼容既有 ``await ctx.tracker.start()`` 调用方
        （本方法不含任何 await）。
        """
        self._start_time = time.monotonic()
        if not self._registered:
            get_frame_tick_registry().register(self.tick)
            self._registered = True
        # 立即推送首拍（原实现首拍在第一个 1/60s 定时器到期后）。
        self.tick()

    def tick(self) -> None:
        """帧驱动单拍：检测中断并推送一次解析进度（render 线程调用）。

        ★ 2026-09-20（解析进度行卡住修复）：单拍异常只记 warning（5s 限频）
        并继续下一拍——不再让异常终止后续刷新。
        """
        if self._start_time is None:
            return
        if is_interrupted():
            self._interrupted = True
            self._unregister()
            return
        try:
            self._push_once()
        except Exception:
            self._log_push_error()

    def _unregister(self) -> None:
        """从帧驱动注册表注销（幂等）。"""
        if self._registered:
            get_frame_tick_registry().unregister(self.tick)
            self._registered = False

    def _push_once(self) -> None:
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
        """完成工具调用解析，更新全局状态。

        保留 ``async`` 签名以兼容既有 ``await ctx.tracker.finalize()``。
        ★ 2026-10-23（game_loop）：帧驱动后不再有内部 asyncio 任务需要取消，
        这里只从注册表注销，并推送最终值 + 完成事件（帧内/帧外均由调用方
        线程同步执行，单次收尾）。
        """
        self._unregister()
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
