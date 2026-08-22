"""TuiInputOrchestrator — 用户输入等待编排器。

从 ChatUIConsumer.wait_for_user_input() 提取为独立类，
负责输入等待（事件化）、prefill 注入和残留输入排空。

单一职责：
  - 阻塞等待用户输入（基于 Input._input_ready threading.Event，非忙等轮询）
  - prefill 文本注入 + 残留输入排空
  - EscapeMonitor 存活检测

方向A 步骤2（2026-07-31）：wait_for_user_input 由 50ms ``time.sleep`` 忙等
轮询改为 ``input_.wait_until_ready()`` 事件等待——线程在 threading.Event 上
休眠，Enter（render 线程 ``_enter()`` set）后立即唤醒，消除 50ms 轮询延迟；
0.2s 等待上限仅为周期性检查 monitor 存活（非忙等）。
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

        基于 ``input_.wait_until_ready()``（_input_ready threading.Event）
        事件等待，线程在 Event 上休眠而非 50ms 忙等轮询；Enter 提交后立即
        唤醒返回。

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
            # ★ W4 修复（2026-08-19，很多上文时按回车不能编辑对应消息
            #   ——1 条用户消息也复现）：stale 为**空串**时是用户在
            #   「弹窗消失 → prefill 注入前」窗口按下的 Enter（EditmsgPlugin
            #   的 clear+display 全量重放在很多上文时耗时 100ms~1s+，用户
            #   看到弹窗消失即按 Enter 提交——此时缓冲恒空，`_enter()` 产生
            #   空提交）。修复前该空提交被静默丢弃 → prefill 注入后无提交
            #   → 用户需再按一次 Enter（「按回车没反应」）。用户意图明确
            #   是提交（按了 Enter），注入 prefill 后直接作为已提交文本
            #   返回——按一次 Enter 即完成编辑重发。stale 非空（如流程
            #   内部残留的 '/editmsg' 重复提交）仍照旧丢弃。
            auto_submit = stale == ""
            if auto_submit:
                _logger.debug(
                    "wait_for_user_input: 用户 Enter 先于 prefill 注入——"
                    "注入后自动提交（不再要求再按一次）",
                )
                input_.set_buffer(prefill)
                input_.echo(prefill)
                # 提交语义收尾：缓冲清空 + 输入行清屏（对齐 _enter() 提交后
                # 状态——文本已作为返回值交给上层，输入行不残留显示）。
                input_.set_buffer("")
                input_.echo("")
                return prefill
            # ★ P2-5 修复（窗口期输入保留）：prefill 注入前若缓冲已有用户
            #   键入（editmsg 确认选择后 → prefill 注入前窗口期字符，由
            #   editmsg_plugin 经 drain_all 保存 + handle_chars 写回），拼接
            #   ``prefill + existing`` 而非无条件覆盖——修复前 set_buffer
            #   (prefill) 静默丢弃窗口期输入。
            existing = ""
            try:
                existing = input_.get_current_text() or ""
            except Exception:
                existing = ""
            merged = prefill + existing if existing else prefill
            input_.set_buffer(merged)
            input_.echo(merged)
            # 残留提交检查与恢复（editmsg 竞态兜底修复，2026-08-01；
            # P2 2026-08-07：检查移至 echo 之后）：
            # 修复 1 已在源头（dispatcher）丢弃被抑制 Enter 后的 LF；此处防御
            # LF 在 set_buffer 之后才被 render 线程处理、_enter() 已提交 prefill
            # 的残余窗口——echo 后再次 get_queued_input() 检查残留提交，若
            # 非 None 则重新注入恢复（set_buffer 清 submitted/事件，poll loop
            # 从干净状态开始）。检查放在 echo 之后（P2 竞态窗口修复：修复前
            # 检查与 echo 之间 render 线程可能处理残留 LF 提交 prefill，导致
            # prefill 被误当用户输入返回；echo 后检查覆盖该窗口，恢复后的
            # 缓冲已被回显，无需再次 echo）。
            residual = input_.get_queued_input()
            if residual is not None:
                _logger.debug(
                    "wait_for_user_input: 残留 Enter 已提交 prefill (%r)，"
                    "重新注入恢复", residual,
                )
                input_.set_buffer(merged)
                # ★ P3（review 2026-08-22）：_enter() 已清屏（echo("")），此处
                #   恢复缓冲须重新 echo(merged)——修复前只 set_buffer 不 echo，
                #   缓冲含 merged 但输入行显示为空（用户不可见直到下次编辑）。
                input_.echo(merged)
            _logger.debug("wait_for_user_input: prefill done, entering poll loop")

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            # ── 事件等待（方向A 步骤2：消除 50ms 忙等） ──
            #   _input_ready 在 render 线程 _enter() 中 set、在 get_queued_input()
            #   中 clear；wait 与 clear 之间仅单一消费者（本编排器），无竞态。
            #   prefill 路径中 set_buffer 会 clear _input_ready，随后事件等待
            #   从干净状态开始。
            # P3-4 前提锁定：``wait_until_ready`` 返回 True 与 ``get_queued_input``
            #   之间理论存在竞态（其他消费者可能先消费）——当前**单一消费者**
            #   前提成立（仅 TuiInputOrchestrator 调用 get_queued_input；editmsg
            #   等路径不经过本编排器）。若未来引入第二消费者须加锁/队列语义。
            remaining = None if deadline is None else deadline - time.monotonic()
            # 防御：remaining 为负（wait 返回 True 但 get 为 None 的下一轮）时
            # 钳制到 0——``Event.wait(负数)`` 抛 ValueError（修复前未设防）。
            wait_timeout = min(0.2, remaining) if remaining is not None else 0.2
            wait_timeout = max(0.0, wait_timeout)
            if input_.wait_until_ready(timeout=wait_timeout):
                text = input_.get_queued_input()
                if text is not None:
                    return text
                # 防御：wait 返回 True 但被其他路径先消费（当前无此调用方）
                # ——与旧轮询语义一致（get 到 None 就继续循环）。
                continue
            # wait 返回 False（0.2s 上限，周期性检查 monitor 存活 + 超时）
            if not monitor.is_alive:
                _logger.warning("EscapeMonitor 线程已死亡，退出等待")
                raise RuntimeError("EscapeMonitor thread died")
            if deadline is not None and time.monotonic() >= deadline:
                return ""


__all__ = ["TuiInputOrchestrator"]
