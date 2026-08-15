"""_SessionQueueMixin — InkSession 命令队列管理子域（架构改进方向 A，2026-08-16）。

拆分背景：InkSession（原 ~1540 行）为「上帝类」——命令队列/线程生命周期/
渲染循环/崩溃恢复/hooks 等职责混杂。方向 A 按**可独立测试的职责边界**
拆分：命令入队/背压/排空安全收敛为本 mixin，渲染帧执行为
``_session_frame_mixin._SessionFrameMixin``，session 保留渲染循环调度/
生命周期/崩溃恢复/注入/访问器。

本 mixin 承载：
  - ``push_cmd`` / ``push_cmd_critical`` — 命令入队（优先级/阻塞/腾位/背压/紧急直写）；
  - ``_put_no_drop`` — 内容命令背压等待（有上限，防渲染线程卡死时调用方永久阻塞）；
  - ``_drain_queue_safe`` — 清空队列（可保留内容命令，供 suspend/崩溃恢复/超时兜底）。

依赖约定（由 InkSession.__init__ 初始化，运行时经 ``self`` 访问）：
  - ``_cmd_queue``/``_cmd_seq``/``_consecutive_full``/``_cmd_queue_dropped``
  - ``_cmd_event``（threading.Event，入队唤醒渲染循环）
  - ``_render_running``（渲染线程存活标志，背压/丢弃判定）
  - ``_config``（cmd_queue_maxsize / consecutive_full_threshold）
  - ``_write_emergency``（紧急直写 stderr 兜底）

★ 常量归属：``_KEEP_CONTENT_CMDS`` / ``_PUT_NO_DROP_TIMEOUT`` 随方法迁至
本模块（唯一使用方）——session 模块 re-export 保持旧导入路径兼容
（``src.tui.ink.session._PUT_NO_DROP_TIMEOUT`` 仍可访问，测试 patch 目标
已同步更新至本模块）。
"""

from __future__ import annotations

import heapq
import itertools
import logging
import queue
import time

from src.tui._const import (
    RenderCmd,
    RenderCommand,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
# ★ 命令优先级策略（方向B 拆分）：优先级常量/映射自 ._cmd_priority 导入
from src.tui.ink._cmd_priority import (
    _CMD_PRIORITY_CRITICAL,
    _CMD_PRIORITY_LOW,
    _CRITICAL_CMDS,
    _STREAM_CMDS,
    _get_cmd_id,
    _get_cmd_priority,
    _cmd_name,
)

_logger = logging.getLogger(__name__)

#: 暂停/恢复保留命令集合（2026-08-15 短内容丢失修复）：
#: suspend（交互工具独占终端）/ 崩溃恢复 / flush 超时兜底经 ``_drain_queue_safe``
#: 清空队列时，**用户可见核心内容命令**（思考/回答/工具卡/阶段/错误等）不丢弃、
#: 保留在队列中，resume 后渲染线程处理——修复前这些命令被无条件丢弃，模型
#: 在交互工具挂起 / 渲染暂停期间输出的短思考/短回答**永久丢失**（模型状态也
#: 未应用），视觉上「很短的回答跟思考没显示」（偶发，取决于命令入队与
#: suspend 清理的时序）。
#: 可丢弃命令（WRITE_LINE/DISPLAY_MSGS/SUBAGENT_FRAME/CLEAR_MSGS/SPLASH/
#: BG_BASH_COUNT）为外部输出/历史回放/面板刷新/清屏等——临时挂起后重放或
#: 由调用方重新触发，丢弃可接受（避免暂停期间积压陈旧命令污染恢复帧）。
_KEEP_CONTENT_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.TOOL_SUMMARY,
    RenderCommand.TOOL_OPEN,
    RenderCommand.TOOL_CLOSE,
    RenderCommand.TOOL_COUNT_INC,
    RenderCommand.TOOL_COUNT_DEC,
    RenderCommand.TOOL_FAIL_INC,
    RenderCommand.USER_MSG,
    RenderCommand.ERROR,
    RenderCommand.NOTIFICATION,
    RenderCommand.MAIN_PHASE,
    RenderCommand.PARSE_INFO,
    RenderCommand.SUBAGENT_MARKDOWN,
})

#: P2-2（review 方向）：``_put_no_drop`` 内容命令背压最大等待时长（秒）。
#: 渲染线程存活时队列满 → 背压等待（不静默丢弃内容）；超过本阈值（渲染线程
#: 卡死/消费停滞）回退为丢弃并记 warning——防调用方（流式/事件循环线程）
#: 永久阻塞。
_PUT_NO_DROP_TIMEOUT = 30.0


class _SessionQueueMixin:
    """InkSession 命令队列管理子域（mixin）。

    所有方法经 ``self`` 访问 InkSession 实例字段（见模块 docstring 依赖
    约定）——本 mixin 无独立状态，方法可直接被测试以实例属性替换
    （monkeypatch 语义保持，session._push_cmd 等替换即生效）。
    """

    # ── 类型标注（InkSession.__init__ 初始化；运行时求值为实例字段） ──
    _cmd_queue: "queue.PriorityQueue"
    _cmd_seq: "itertools.count"
    _cmd_event: "threading.Event"
    _consecutive_full: int
    _cmd_queue_dropped: int
    _render_running: bool
    _config: object

    # ── 命令入队 ─────────────────────────────────────

    def push_cmd(self, cmd: RenderCmd) -> None:
        """入队渲染命令（阻塞语义与 TuiEngine.push_cmd 一致）。"""
        priority = _get_cmd_priority(cmd)
        blocking = _get_cmd_id(cmd) in _CRITICAL_CMDS
        try:
            if blocking:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd),
                    block=True, timeout=0.1,
                )
            else:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd), block=False,
                )
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            # ★ 方向4（队列满 LOW 优先丢弃）：新命令优先级高于 LOW 且队列中
            #   存在 LOW 命令（WRITE_LINE/DISPLAY_MSGS）时腾位——持 mutex 锁内
            #   遍历队列移除至多一个 LOW 项（记录 dropped + warning）后重试 put；
            #   新命令本身为 LOW 或队列无 LOW 项时保持现状丢弃（保护
            #   STREAM/CRITICAL 不丢）。
            evicted = False
            if priority < _CMD_PRIORITY_LOW:
                with self._cmd_queue.mutex:
                    for i, item in enumerate(self._cmd_queue.queue):
                        if item[0] >= _CMD_PRIORITY_LOW:
                            removed = self._cmd_queue.queue.pop(i)
                            # ★ BUG-31（review 方向）：``PriorityQueue`` 底层是
                            #   heapq 数组，任意下标 ``pop`` 后堆序被破坏——后续
                            #   ``heappush``/``heappop`` 在损坏堆上操作可能返回非
                            #   最小项 → 命令优先级/同批顺序错乱（如 PHASE_DONE
                            #   先于 CONTENT 出队致内容通道提前关闭、TOOL_CLOSE
                            #   先于 TOOL_OUTPUT 致输出落到无名新 box）。pop 后
                            #   ``heapq.heapify`` 恢复堆序（O(n)，仅队列满腾位
                            #   罕见路径触发，成本可接受）。
                            heapq.heapify(self._cmd_queue.queue)
                            self._cmd_queue_dropped += 1
                            _logger.warning(
                                "渲染命令队列已满，腾位移除 LOW 命令: %s",
                                _cmd_name(_get_cmd_id(removed[2])),
                            )
                            evicted = True
                            break
            if evicted:
                # ★ 修复（长任务思考/回答丢失）：pop 绕过 get() 直接移除 heapq
                #   元素，须补 task_done() 减 unfinished_tasks——否则
                #   queue.join()（flush 等待排空）因 unfinished_tasks 虚高而
                #   永远等待 → flush 恒超时 → _drain_queue_safe 丢弃未消费的
                #   reasoning/content 命令（视觉「只显示工具调用」）。
                #   task_done() 须在 mutex 外调用：其内部经 all_tasks_done
                #   （Condition，与 mutex 同源普通 Lock 不可重入）再次获取
                #   mutex，持 mutex 调用会自死锁。
                try:
                    self._cmd_queue.task_done()
                except ValueError:
                    pass
                try:
                    self._cmd_queue.put(
                        (priority, next(self._cmd_seq), cmd), block=False,
                    )
                    self._consecutive_full = 0
                    self._cmd_event.set()
                    return
                except queue.Full:
                    pass  # 并发竞争仍满 → 保持丢弃（不无限循环）
            # ★ 内容命令不静默丢弃（长任务思考/回答丢失修复，2026-08-13）：
            #   REASONING/CONTENT/TOOL_OUTPUT 是用户可见核心内容——队列满且无
            #   LOW 可腾位时，修复前非 blocking 内容命令被**立即静默丢弃** →
            #   长任务（大量工具输出/流式文本积压致队列满）中思考/回答偶发不
            #   显示，而 TOOL_OPEN/TOOL_CLOSE/PHASE_DONE 等 CRITICAL 命令走
            #   阻塞路径不丢，视觉上「只显示工具调用卡片」。渲染线程存活时
            #   背压等待（模型流式让渲染消费跟上，内容不丢）；渲染线程已终止
            #   （UI 不可用）回退原丢弃语义（不无限卡死调用方/事件循环）。
            if _get_cmd_id(cmd) in _STREAM_CMDS and self._render_running:
                if self._put_no_drop(priority, cmd):
                    return
            # 方向2（CRITICAL 不静默丢弃）：blocking（CRITICAL）命令腾位失败后
            # 改走 push_cmd_critical 紧急直写语义（_write_emergency 兜底，绝不
            # 静默丢弃）——PhaseDone/ToolClose 等通道关闭命令丢失会导致通道
            # 永不关闭。非 CRITICAL 保持既有丢弃语义（计数/日志不变）。
            if blocking:
                self.push_cmd_critical(cmd)
                return
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            cmd_id = _get_cmd_id(cmd)
            _logger.warning(
                "渲染命令队列已满（%s 条），丢弃命令: %s (优先级=%d)",
                self._cmd_queue.qsize(), _cmd_name(cmd_id), priority,
            )
            if self._consecutive_full >= self._config.consecutive_full_threshold:
                _logger.error("渲染输出管线持续拥堵（%d 次连续满队列）", self._consecutive_full)
                if self._consecutive_full % self._config.consecutive_full_threshold == 0:
                    self._write_emergency(
                        f"{ANSI_EMERGENCY_RED}[ChatUI] 渲染队列已满，已丢弃 "
                        f"{self._cmd_queue_dropped} 条命令{ANSI_EMERGENCY_RESET}\n",
                        stream="stderr",
                    )

    def push_cmd_critical(self, cmd: RenderCmd) -> None:
        """入队关键命令 — 阻塞等待以确保绝不丢失。

        队列满（queue.Full）时**不抛异常**：经紧急路径直写 stderr 兜底
        （BUG-T2），保证"关键命令绝不丢失"语义（非静默丢弃）。
        """
        priority = _CMD_PRIORITY_CRITICAL
        cmd_id = _get_cmd_id(cmd)
        try:
            self._cmd_queue.put((priority, next(self._cmd_seq), cmd), block=True, timeout=1.0)
            self._consecutive_full = 0
            self._cmd_event.set()
        except queue.Full:
            self._consecutive_full += 1
            self._cmd_queue_dropped += 1
            _logger.warning(
                "关键命令队列已满，紧急直写: %s (优先级=%d)",
                _cmd_name(cmd_id), priority,
            )
            self._write_emergency(
                f"{ANSI_EMERGENCY_RED}[ChatUI] 关键命令队列已满，紧急直写: "
                f"{_cmd_name(cmd_id)}{ANSI_EMERGENCY_RESET}\n",
                stream="stderr",
            )

    def _put_no_drop(self, priority: int, cmd: RenderCmd) -> bool:
        """内容命令背压等待：渲染线程存活时持续等待入队（不静默丢弃内容）。

        长任务渲染拥塞修复（2026-08-13）：REASONING/CONTENT/TOOL_OUTPUT 是
        用户可见核心内容——队列满时若静默丢弃，长任务中思考/回答偶发不显示
        （工具调用卡因 CRITICAL 阻塞语义不丢，视觉上「只显示工具调用」）。
        渲染线程存活时阻塞等待（背压：模型流式等待渲染消费跟上，内容不丢）；
        渲染线程已终止（UI 不可用）返回 False → 调用方回退丢弃告警路径，
        避免无限卡死调用方（流式/事件循环线程）。

        ★ P2-2（review 方向）：背压**有上限**——``while`` 循环内
        ``put(timeout=0.5)`` 无限重试，渲染线程卡死（消费停滞但线程仍存活）
        时调用方永久阻塞。修复：增加最大等待时长（``_PUT_NO_DROP_TIMEOUT``，
        30s），超时后回退为丢弃并记 warning（返回 False，调用方回退既有
        丢弃告警路径，语义兼容）。

        Returns:
            True — 已入队成功；False — 渲染线程已终止或背压超时，未入队。
        """
        deadline = time.monotonic() + _PUT_NO_DROP_TIMEOUT
        while self._render_running:
            try:
                self._cmd_queue.put(
                    (priority, next(self._cmd_seq), cmd),
                    block=True, timeout=0.5,
                )
                self._consecutive_full = 0
                self._cmd_event.set()
                return True
            except queue.Full:
                # 渲染线程存活但队列仍满（消费中）→ 继续等待（背压，不丢内容）
                if time.monotonic() >= deadline:
                    _logger.warning(
                        "内容命令背压等待超时（>%.0fs），回退丢弃: %s (优先级=%d)",
                        _PUT_NO_DROP_TIMEOUT, _cmd_name(_get_cmd_id(cmd)), priority,
                    )
                    return False
                continue
        return False

    # ── 队列安全排空 ─────────────────────────────────

    def _drain_queue_safe(self, keep_content: bool = False) -> int:
        """清空渲染命令队列（丢弃计数）。

        Args:
            keep_content: True 时**保留用户可见核心内容命令**
                （``_KEEP_CONTENT_CMDS``：思考/回答/工具卡/阶段/错误等）——
                仅丢弃非内容命令（WRITE_LINE/DISPLAY_MSGS/SUBAGENT_FRAME 等）。
                供 suspend（交互工具独占终端）/ 崩溃恢复 / flush 超时兜底使用：
                模型在渲染暂停期间输出的短思考/短回答命令**不丢失**，resume 后
                渲染线程处理并显示（修复前无条件丢弃 → 短内容永久丢失）。

        Returns:
            丢弃的命令条数。
        """
        dropped = 0
        if keep_content:
            # ★ 保留内容命令：**不 get/put 重放**（会破坏 unfinished_tasks——
            # get_nowait+task_done 后 put 回去，resume 后 _drain_queue 的
            # task_done 抛 ValueError）——直接在 mutex 保护下遍历队列，仅
            # 移除要丢弃的命令（保留命令原地不动，seq/堆序不变）。
            with self._cmd_queue.mutex:
                keep_items: list = []
                drop_items: list = []
                for item in self._cmd_queue.queue:
                    if _get_cmd_id(item[2]) in _KEEP_CONTENT_CMDS:
                        keep_items.append(item)
                    else:
                        drop_items.append(item)
                self._cmd_queue.queue[:] = keep_items
                # ★ BUG-31 同族：heapq 数组任意修改后须 heapify 恢复堆序
                #   （否则后续 heappush/heappop 在损坏堆上操作可能返回非最小项）。
                heapq.heapify(self._cmd_queue.queue)
                dropped = len(drop_items)
            # 丢弃的命令补 task_done（unfinished_tasks 一致性：put 增加、task_done
            # 减少；丢弃的命令不再被消费 → 补一次 task_done）。与 push_cmd 腾位
            # 语义一致：task_done 在 mutex 外调用（all_tasks_done Condition 与
            # mutex 同源普通 Lock 不可重入，持 mutex 调用会自死锁）。
            for _ in drop_items:
                try:
                    self._cmd_queue.task_done()
                except ValueError:
                    pass
            if dropped > 0:
                _logger.info(
                    "渲染队列清理：丢弃 %d 条非内容命令，保留 %d 条内容命令",
                    dropped, len(keep_items),
                )
            return dropped
        while not self._cmd_queue.empty():
            try:
                _, _, cmd = self._cmd_queue.get_nowait()
                self._cmd_queue.task_done()
                dropped += 1
            except queue.Empty:
                break
        if self._cmd_queue_dropped > 0:
            _logger.info("render 线程终止，共丢弃 %d 条命令", self._cmd_queue_dropped)
        return dropped


#: 为保持旧导入路径兼容（``src.tui.ink.session._KEEP_CONTENT_CMDS`` /
#: ``..._PUT_NO_DROP_TIMEOUT``），session 模块 re-export 本模块常量。
__all__ = [
    "_SessionQueueMixin",
    "_KEEP_CONTENT_CMDS",
    "_PUT_NO_DROP_TIMEOUT",
]
