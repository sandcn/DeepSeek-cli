"""_SessionQueueMixin — InkSession 命令队列管理子域（P1-2 重写）。

背景：修复前本 mixin 直接操作 ``queue.PriorityQueue`` 的 ``mutex`` 与底层
``queue`` 堆数组做合并/覆盖/腾位/排空，并手工维护 ``unfinished_tasks``——
与 stdlib 私有契约强耦合（BUG-31：任意下标 pop 破坏堆序；task_done 遗漏
导致 ``join`` 永久等待）。

现在全部语义收敛到 ``CommandBuffer``（自建有序缓冲，公开契约）：

  - 入队：``put``（满队列返回 False，阻塞路径带超时）；
  - 同类型最新值覆盖：``replace_same_kind``；
  - 流式命令尾部合并：``append_stream``；
  - 满队列腾位：``evict_low``（只移除低优先级）；
  - 排空保留内容命令：``drain``；
  - ``get_nowait`` / ``task_done`` / ``join`` 与 queue.Queue 语义一致。

本 mixin 不再 import ``heapq`` / 不再触碰 stdlib 私有字段。
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import TYPE_CHECKING, Any

from src.tui._const import (
    RenderCmd,
    RenderCommand,
    ANSI_EMERGENCY_RED,
    ANSI_EMERGENCY_RESET,
)
from ._cmd_priority import (
    _CMD_PRIORITY_CRITICAL,
    _CMD_PRIORITY_LOW,
    _COALESCE_CMDS,
    _CRITICAL_CMDS,
    _STREAM_CMDS,
    _get_cmd_id,
    _get_cmd_priority,
    _cmd_name,
)

_logger = logging.getLogger(__name__)

#: 可追加合并的流式内容命令（同类型、且为队列最新条目时合并文本）
_APPEND_STREAM_CMDS = frozenset({
    RenderCommand.CONTENT,
    RenderCommand.REASONING,
    RenderCommand.TOOL_MARKDOWN,
})

#: 流式命令合并后的文本上限（字符）
_STREAM_MERGE_MAX_CHARS = 65536

#: 暂停/恢复保留命令集合（用户可见核心内容不丢弃）
_KEEP_CONTENT_CMDS = frozenset({
    RenderCommand.REASONING,
    RenderCommand.CONTENT,
    RenderCommand.PHASE_DONE,
    RenderCommand.TOOL_OUTPUT,
    RenderCommand.TOOL_MARKDOWN,
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

#: 内容命令背压最大等待时长（秒）
_PUT_NO_DROP_TIMEOUT = 30.0


class _SessionQueueMixin:
    """InkSession 命令队列管理子域（CommandBuffer 公开契约）。"""

    _cmd_queue: Any
    _cmd_event: Any
    _consecutive_full: int
    _cmd_queue_dropped: int
    _render_running: bool
    _config: Any

    if TYPE_CHECKING:  # pragma: no cover
        def _write_emergency(self, *args: Any, **kwargs: Any) -> Any: ...

    def _wake_render(self) -> None:
        """唤醒渲染循环（P0-1：Event + 按需调度器双通道兼容）。"""
        self._cmd_event.set()
        scheduler = getattr(self, "_scheduler", None)
        if scheduler is not None:
            scheduler.request("push_cmd")

    # ── 合并辅助 ─────────────────────────────────────

    def _replace_queued_same_kind(self, cmd_id: int, cmd: RenderCmd) -> bool:
        return self._cmd_queue.replace_same_kind(cmd_id, cmd, _get_cmd_id)

    def _append_to_last_stream_cmd(self, cmd_id: int, cmd: RenderCmd) -> bool:
        if cmd_id not in _APPEND_STREAM_CMDS:
            return False
        return self._cmd_queue.append_stream(
            cmd_id,
            cmd,
            _get_cmd_id,
            lambda c: getattr(c, "text", "") or "",
            lambda c, text: dataclasses.replace(c, text=text),
            _STREAM_MERGE_MAX_CHARS,
        )

    # ── 入队 ─────────────────────────────────────────

    def push_cmd(self, cmd: RenderCmd) -> None:
        """入队渲染命令（优先级 + 合并 + 满队列背压/丢弃策略）。"""
        priority = _get_cmd_priority(cmd)
        cmd_id = _get_cmd_id(cmd)
        blocking = cmd_id in _CRITICAL_CMDS

        if cmd_id in _COALESCE_CMDS and self._replace_queued_same_kind(cmd_id, cmd):
            self._consecutive_full = 0
            self._wake_render()
            return

        if self._cmd_queue.put(
            cmd, priority, blocking=blocking,
            timeout=0.1 if blocking else None,
        ):
            self._consecutive_full = 0
            self._wake_render()
            return

        # ── 队列满：低优先级腾位 ──
        if priority < _CMD_PRIORITY_LOW:
            removed = self._cmd_queue.evict_low(_CMD_PRIORITY_LOW, _get_cmd_id)
            if removed is not None:
                self._cmd_queue_dropped += 1
                _logger.warning(
                    "渲染命令队列已满，腾位移除 LOW 命令: %s",
                    _cmd_name(_get_cmd_id(removed)),
                )
                if self._cmd_queue.put(cmd, priority):
                    self._consecutive_full = 0
                    self._wake_render()
                    return

        # ── 内容命令：尾部合并 → 背压等待（不静默丢弃） ──
        if cmd_id in _STREAM_CMDS:
            if self._append_to_last_stream_cmd(cmd_id, cmd):
                self._consecutive_full = 0
                self._wake_render()
                return
            if self._render_running and self._put_no_drop(priority, cmd):
                return

        # ── 关键命令：紧急直写兜底；其余丢弃并告警 ──
        if blocking:
            self.push_cmd_critical(cmd)
            return
        self._consecutive_full += 1
        self._cmd_queue_dropped += 1
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
        """入队关键命令 — 阻塞等待；队列满时紧急直写 stderr 兜底。"""
        priority = _CMD_PRIORITY_CRITICAL
        cmd_id = _get_cmd_id(cmd)
        if self._cmd_queue.put(cmd, priority, blocking=True, timeout=1.0):
            self._consecutive_full = 0
            self._wake_render()
            return
        self._consecutive_full += 1
        self._cmd_queue_dropped += 1
        _logger.warning("关键命令队列已满，紧急直写: %s (优先级=%d)", _cmd_name(cmd_id), priority)
        self._write_emergency(
            f"{ANSI_EMERGENCY_RED}[ChatUI] 关键命令队列已满，紧急直写: "
            f"{_cmd_name(cmd_id)}{ANSI_EMERGENCY_RESET}\n",
            stream="stderr",
        )

    def _put_no_drop(self, priority: int, cmd: RenderCmd) -> bool:
        """内容命令背压等待（渲染线程存活时最长 ``_PUT_NO_DROP_TIMEOUT``）。"""
        deadline = time.monotonic() + _PUT_NO_DROP_TIMEOUT
        while self._render_running:
            if self._cmd_queue.put(cmd, priority, blocking=True, timeout=0.5):
                self._consecutive_full = 0
                self._wake_render()
                return True
            if time.monotonic() >= deadline:
                _logger.warning(
                    "内容命令背压等待超时（>%.0fs），回退丢弃: %s (优先级=%d)",
                    _PUT_NO_DROP_TIMEOUT, _cmd_name(_get_cmd_id(cmd)), priority,
                )
                return False
        return False

    # ── 队列安全排空 ─────────────────────────────────

    def _drain_queue_safe(self, keep_content: bool = False) -> int:
        """清空队列；``keep_content=True`` 时保留用户可见核心内容命令。"""
        keep_ids = _KEEP_CONTENT_CMDS if keep_content else None
        dropped, kept = self._cmd_queue.drain(keep_ids, _get_cmd_id)
        if dropped:
            if keep_content:
                _logger.info(
                    "渲染队列清理：丢弃 %d 条非内容命令，保留 %d 条内容命令",
                    dropped, kept,
                )
            else:
                _logger.info("render 线程终止，本次丢弃 %d 条命令", dropped)
        return dropped


__all__ = [
    "_SessionQueueMixin",
    "_KEEP_CONTENT_CMDS",
    "_PUT_NO_DROP_TIMEOUT",
    "_COALESCE_CMDS",
]
