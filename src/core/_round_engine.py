"""轮次执行引擎 — ChatSession 的 round 生命周期编排

从 session.py 提取，封装一轮对话的完整执行生命周期：
  _prepare_round → _snapshot_token_stats → _execute_round → _finalize_round

职责范围：
1. 轮次编排（_execute_round / _finalize_round）
2. Token 统计计算（_snapshot_token_stats / _compute_token_delta）
3. 自动保存（_auto_save）
4. 事件发射（_emit_round_events）
5. 异常回滚（_rollback_round_on_error / _handle_round_error）

通过持有 ChatSession 引用来访问共享状态（agent/state_machine/ports 等），
使用 weakref.proxy 避免循环强引用。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import time
import weakref
from typing import AsyncIterator

from .state_machine import SessionState

_logger = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────
_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"
_LOG_TRUNCATE_LENGTH = 50


class _RoundEngine:
    """轮次执行引擎 — 封装一轮对话的完整生命周期。

    通过弱引用（weakref.proxy）访问 ChatSession 的共享状态，
    避免循环强引用导致的内存泄漏。

    生命周期方法（按调用顺序）：
    1. handle_round_error — 异常处理上下文管理器（公开）
    2. _prepare_round — 更新模型配置
    3. _snapshot_token_stats — 前置统计快照
    4. execute_round — 编排方法（公开）
    5. _finalize_round — 后置处理
    6. _compute_token_delta — 增量计算
    7. _auto_save — 自动保存
    8. _emit_round_events — 事件发射
    9. rollback_round_on_error — 异常回滚（公开）
    """

    def __init__(self, session):
        self._s = weakref.proxy(session)

    # ── 公共方法（被 run_round / retry / run_single 等调用） ──

    @contextlib.asynccontextmanager
    async def handle_round_error(self, label: str) -> AsyncIterator[None]:
        """统一处理 _execute_round 异常的上下文管理器。

        提取 run_round / retry / run_single 中重复的异常处理模式：
        - 记录异常日志
        - 强制恢复状态机

        各方法的个性化清理在 yield 返回后的 except 块中完成。
        """
        try:
            yield
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            _logger.exception("%s: _execute_round 异常", label)
            try:
                self._s._force_state_recovery()
            except Exception as recovery_exc:
                _logger.exception("_force_state_recovery 在异常处理中再次失败: %s", recovery_exc)
            raise

    def rollback_round_on_error(self) -> None:
        """run_round 异常回滚的统一清理逻辑。

        在 handle_round_error 已记录日志并恢复状态机后执行个性化的消息和缓存清理。
        """
        s = self._s
        # 回滚 orphan ID（仅限新分配的 session_id，已从持久化加载的保留）
        if getattr(s, '_session_id_newly_allocated', False):
            s._state.session_id = None

        # 异常回滚时保护 AI 已生成的内容
        last_role = s._agent.messages[-1].get(_ROLE_KEY) if s._agent.messages else None
        if last_role == "assistant":
            _logger.warning(
                "run_round 异常，_execute_round 已部分执行（最后消息为 assistant），"
                "保留 AI 内容，待 retry 机制恢复"
            )
            if s._ctx_mgr is not None:
                s._ctx_mgr.invalidate_cache()
            s.sync_retry_pending()
        elif last_role == "user":
            pop_index = len(s._agent.messages) - 1
            s._agent.messages.pop()
            _logger.warning("run_round 异常，已回滚最后一条 user 消息")
            if s._ctx_mgr is not None:
                s._ctx_mgr.invalidate_cache()
                s._ctx_mgr.notify_messages_removed([pop_index])
            s.sync_retry_pending()
        else:
            if s._ctx_mgr is not None:
                s._ctx_mgr.invalidate_cache()

        # 清理已排队的消息，防止异常后残留无效数据
        s._state.pending_messages.clear()

    async def execute_round(self) -> dict:
        """执行一轮对话的公共逻辑（编排方法）。"""
        s = self._s
        self._prepare_round()
        prev_stats = self._snapshot_token_stats()
        s._emit("round_start")
        try:
            interrupted: bool = await s._agent.run()
        except asyncio.CancelledError:
            if getattr(s._agent, '_last_checkpoint_requested', False):
                try:
                    await s.save_checkpoint()
                except Exception:
                    _logger.exception("_execute_round: CancelledError 时 save_checkpoint 异常")
            raise
        checkpoint_requested = getattr(s._agent, '_last_checkpoint_requested', False)
        return await self._finalize_round(interrupted, prev_stats, checkpoint_requested=checkpoint_requested)

    # ── 内部方法 ────────────────────────────────────────

    def _prepare_round(self) -> None:
        """更新 agent 和 context_manager 的模型配置。"""
        s = self._s
        s._agent.model = s._model
        if s._ctx_mgr:
            s._ctx_mgr.update_model(s._model)

    def _snapshot_token_stats(self) -> tuple[int, int, int]:
        """获取前置 token 统计快照，返回 (prev_input, prev_output, prev_calls)。"""
        current = self._s._stats_port.snapshot()
        return current["input"], current["output"], current["calls"]

    async def _finalize_round(self, interrupted: bool,
                              prev_stats: tuple[int, int, int],
                              checkpoint_requested: bool = False) -> dict:
        """后置处理: enforce_message_limit → 计算 delta → 防御性状态恢复 → 自动保存 → 发射事件。"""
        s = self._s
        prev_input, prev_output, prev_calls = prev_stats

        try:
            if s._ctx_mgr:
                s._ctx_mgr.enforce_message_limit()
        except Exception as exc:
            _logger.exception("enforce_message_limit 异常: %s", exc)

        delta, current = self._compute_token_delta(prev_input, prev_output, prev_calls)

        if s._state_machine.is_(SessionState.RUNNING):
            _logger.warning(
                "_finalize_round: 状态机仍在 RUNNING（StateMachineMiddleware 未执行状态转换），"
                "执行强制恢复"
            )
            s._force_state_recovery()

        session_id = await self._auto_save()
        if session_id is None:
            session_id = s._state.session_id

        s._session_id_newly_allocated = False

        return await self._emit_round_events(interrupted, session_id, delta, current, checkpoint_requested=checkpoint_requested)

    def _compute_token_delta(self, prev_input: int, prev_output: int, prev_calls: int) -> tuple[dict, dict]:
        """计算本轮 token 消耗增量，返回 (delta, current_stats)。"""
        current = self._s._stats_port.snapshot()
        delta = {
            "input": current["input"] - prev_input,
            "output": current["output"] - prev_output,
            "calls": current["calls"] - prev_calls,
        }
        return delta, current

    async def _auto_save(self) -> str | None:
        """自动保存会话，返回 session_id（无可保存内容时返回 None）。"""
        s = self._s
        try:
            (snapshot, snapshot_model, snapshot_sid) = (
                copy.deepcopy(s._agent.messages),
                s._model,
                s._state.session_id,
            )

            non_system = [m for m in snapshot if m.get(_ROLE_KEY) != _SYSTEM_ROLE]
            if not non_system:
                s._safe_save_state()
                return snapshot_sid

            session_id = await asyncio.to_thread(
                s._persistence_port.save_session,
                non_system,
                snapshot_model,
                snapshot_sid,
            )
            s._state.session_id = session_id
            s._safe_save_state()
            s._emit("saved", session_id=session_id)
            return session_id
        except Exception as exc:
            _logger.exception("自动保存会话失败: %s", exc)
            s._force_state_recovery()
            return None

    async def _emit_round_events(self, interrupted: bool, session_id: str | None,
                                  delta: dict, current: dict,
                                  checkpoint_requested: bool = False) -> dict:
        """发射 round 事件，返回结果字典。"""
        s = self._s
        try:
            start_time = s._stats_port.get_session_start_time()
            elapsed = time.time() - start_time if start_time else 0.0
        except Exception:
            _logger.exception("get_session_start_time 异常")
            elapsed = 0.0

        if delta["input"] > 0 or delta["output"] > 0:
            prices = s._config_port.get_token_prices()
            s._emit("cost_update",
                    delta=delta, total=current, model=s._model,
                    prices=prices, session_elapsed=elapsed,
                    messages=s._agent.messages)

        if interrupted:
            if checkpoint_requested:
                _logger.warning("Pipeline CancelledError 标记已检测，保存 checkpoint")
                await s.save_checkpoint()
            s._emit("interrupted")

        s._emit("round_end",
                interrupted=interrupted, session_id=session_id,
                delta=delta, elapsed=elapsed)

        s._state.retry_pending = False

        return {
            "interrupted": interrupted,
            "session_id": session_id,
            "delta": delta,
            "elapsed": elapsed,
        }
