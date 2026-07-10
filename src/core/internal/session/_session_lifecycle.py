"""_session_lifecycle — ChatSession 生命周期编排逻辑（从 session.py 提取）

职责范围：
1. 对话生命周期管理（run_round / retry / run_single / run_pending_loop）
2. 回合编排（_execute_round / _prepare_round / _finalize_round）
3. 异常恢复（_force_state_recovery / _handle_round_error / _rollback_round_on_error）
4. Token 统计（_snapshot_token_stats / _compute_token_delta）
5. 自动保存与事件发射（_auto_save / _emit_round_events）

设计原则：
- 所有函数以 session 实例作为第一参数（方案①）
- 零 UI 依赖
- 不直接访问 ChatSession 私有属性，通过 session 实例间接访问
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import logging
from typing import AsyncIterator

from ...state_machine import SessionState, InvalidTransitionError

_logger = logging.getLogger(__name__)

# ── 魔法数字常量 ─────────────────────────────────────
_LOG_TRUNCATE_LENGTH = 50           # 日志截断长度（run_round 排队消息日志）
_MAX_PENDING_LOOP_ITER = 10         # run_pending_loop 最大轮次熔断阈值

# ── 消息字典键常量 ─────────────────────────────────
_ROLE_KEY = "role"
_SYSTEM_ROLE = "system"


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════

def _ensure_idle(session) -> None:
    """确保状态机不在 INIT 状态（兼容未调用 initialize 的场景）。"""
    if session._state_machine.is_(SessionState.INIT):
        try:
            session._state_machine.initialize()
        except InvalidTransitionError:
            pass


# ═══════════════════════════════════════════════════════════════
# 状态恢复
# ═══════════════════════════════════════════════════════════════

def _force_state_recovery(session) -> None:
    """异常后强制恢复状态机离开当前状态回到 IDLE。

    当 _execute_round 在状态转换代码执行前/后抛异常时，
    状态机可能残留在 RUNNING/COMPLETED/INTERRUPTED。
    此方法尝试依次使用多个方案恢复，确保后续 run_round 能正常处理消息。

    修复：不再在第一个成功方法后立即返回，而是持续尝试直到达到 IDLE。
    例如 RUNNING → complete_round() → COMPLETED，此时需要额外调用 save()
    才能回到 IDLE，否则 run_round 再次调用 start_round() 会因
    (COMPLETED, "run_round") 不存在而抛出 InvalidTransitionError。
    """
    current_state = session._state_machine.name
    if session._state_machine.is_(SessionState.IDLE, SessionState.INIT):
        return
    _logger.warning("状态机残留在 %s，执行强制恢复", current_state)
    # 尝试多种转换路径回到 IDLE，持续尝试直到达到 IDLE 或 INIT
    for method_name in ['complete_round', 'interrupt', 'save', 'clear', 'reset']:
        method = getattr(session._state_machine, method_name, None)
        if method is None:
            _logger.warning("强制恢复: 方法 %s 不存在于状态机", method_name)
            continue
        try:
            method()
            _logger.info("强制恢复: %s() 成功", method_name)
            if session._state_machine.is_(SessionState.IDLE, SessionState.INIT):
                return
        except InvalidTransitionError:
            _logger.debug("强制恢复: %s() 转换无效，尝试下一个", method_name)
            continue
    # 保底
    _logger.error("所有状态恢复方案均失败，执行 reset 到 INIT")
    session._state_machine.reset()
    session._emit("state_recovered", old_state=current_state)


# ═══════════════════════════════════════════════════════════════
# 异常处理上下文
# ═══════════════════════════════════════════════════════════════

@contextlib.asynccontextmanager
async def _handle_round_error(session, error_context: str) -> AsyncIterator[None]:
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
        _logger.exception("%s: _execute_round 异常", error_context)
        try:
            _force_state_recovery(session)
        except Exception as recovery_exc:
            _logger.exception("_force_state_recovery 在异常处理中再次失败: %s", recovery_exc)
        raise


# ═══════════════════════════════════════════════════════════════
# run_round — 主要对话入口
# ═══════════════════════════════════════════════════════════════

async def run_round(session, user_input: str) -> dict:
    """添加用户消息并执行一轮对话。

    变更行为：
    - 已在执行中时消息排队（Bug 3）：若状态机为 RUNNING，则将消息暂存到
      pending_messages 队列，返回 {"pending": True} 不阻塞当前轮次。
    - 异常回滚保护 AI 内容（Bug 2）：_execute_round 已部分执行（最后一条消息
      为 assistant）时，跳过 pop user 消息，保留 AI 已生成的内容供 retry 恢复。
    - 回滚后同步 context_manager 缓存（Bug 6）：pop user 消息或保留 AI 内容后，
      调用 context_manager.invalidate_cache() 确保缓存与消息列表一致。

    Args:
        session: ChatSession 实例
        user_input: 用户输入文本

    Returns:
        {"interrupted": bool, "session_id": str|None,
         "delta": dict, "pending": bool}
         pending=True 表示消息已排队（上一轮尚在执行中）。
    """
    async with session._state.round_lock:
        # ★ Bug3 修复：已在执行中则排队消息，不影响当前轮次
        if session._state_machine.is_(SessionState.RUNNING):
            session._state.pending_messages.append(user_input)
            _logger.warning("run_round 被重复调用，消息已排队 (#%d): %s...",
                            len(session._state.pending_messages), user_input[:_LOG_TRUNCATE_LENGTH])
            return {"interrupted": False, "session_id": None,
                    "delta": {"input": 0, "output": 0, "calls": 0},
                    "pending": True}
        _ensure_idle(session)
        session._state_machine.start_round()
        try:
            session._agent.add_user_message(user_input)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            _logger.exception("run_round: add_user_message 异常")
            _force_state_recovery(session)
            raise
        # ── AI 回复前提前分配 session_id ──────────────────
        # 标题生成在后台并行执行，需要 session_id 已就绪才能保存标题。
        if not session._state.session_id:
            session._state.session_id = session._persistence_port.generate_id()
        try:
            async with _handle_round_error(session, "run_round"):
                return await _execute_round(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            # 个性化清理（_handle_round_error 已记录日志并恢复状态机）
            _rollback_round_on_error(session)
            raise


# ═══════════════════════════════════════════════════════════════
# _rollback_round_on_error
# ═══════════════════════════════════════════════════════════════

def _rollback_round_on_error(session) -> None:
    """run_round 异常回滚的统一清理逻辑。

    提取自 run_round 的 except Exception 块（Bug 2 + Bug 6 修复），
    在 _handle_round_error 已记录日志并恢复状态机后执行个性化的消息和缓存清理。
    """
    # 回滚 orphan ID
    session._state.session_id = None

    # ★ Bug2：异常回滚时保护 AI 已生成的内容
    #   检查最后一条消息的角色——若为 assistant 说明 _execute_round
    #   已部分执行（AI 已生成回复），此时保留 AI 内容和对应的 user 消息；
    #   若仍为 user 说明 _execute_round 未开始，回滚该 user 消息。
    last_role = session._agent.messages[-1].get(_ROLE_KEY) if session._agent.messages else None
    if last_role == "assistant":
        # _execute_round 已部分执行，AI 已生成回复
        # 跳过 pop user 消息，保留 AI 已生成的内容供 retry 机制恢复
        _logger.warning(
            "run_round 异常，_execute_round 已部分执行（最后消息为 assistant），"
            "保留 AI 内容，待 retry 机制恢复"
        )
        # ★ Bug6：assistant 分支虽然没有 pop，但消息已变更，
        # 缓存可能不准确，也 invalidate 确保下次访问时重建
        if session._ctx_mgr is not None:
            session._ctx_mgr.invalidate_cache()
    elif last_role == "user":
        # _execute_round 未开始，回滚已添加的 user 消息
        pop_index = len(session._agent.messages) - 1
        session._agent.messages.pop()
        _logger.warning("run_round 异常，已回滚最后一条 user 消息")
        # ★ Bug6：回滚后同步 context_manager 状态
        if session._ctx_mgr is not None:
            session._ctx_mgr.invalidate_cache()
            session._ctx_mgr.notify_messages_removed([pop_index])
    else:
        # 其他情况（消息列表为空等），也 invalidate 缓存确保一致性
        if session._ctx_mgr is not None:
            session._ctx_mgr.invalidate_cache()

    # 清理已排队的消息，防止异常后残留无效数据
    session._state.pending_messages.clear()


# ═══════════════════════════════════════════════════════════════
# run_pending_loop
# ═══════════════════════════════════════════════════════════════

async def run_pending_loop(session, max_iter: int = _MAX_PENDING_LOOP_ITER) -> tuple:
    """处理 run_round 执行期间产生的所有排队消息。

    将 _pending_messages 中的所有消息串行调用 run_round 处理，
    每处理完一轮后再次检查是否有新排队的消息，直到全部处理完毕或达到熔断阈值。

    CLI 和 WebUI 共用此方法，消除两端重复的排队消息处理逻辑。

    变更行为：
    - 增量 checkpoint（Bug 3）：每成功处理一条排队消息后立即调用 save_checkpoint()
      保存增量 checkpoint，确保中途异常时不丢失已成功处理的消息。

    Args:
        session: ChatSession 实例
        max_iter: 最大轮次阈值，防止无限循环（默认 10）

    Returns:
        (breached, unprocessed)
        - breached: 是否触发熔断（True 表示超过 max_iter 轮仍未处理完毕）
        - unprocessed: 熔断时残留的未处理消息列表（已重新放回 _pending_messages）
    """
    pending = session.pop_pending_messages()
    if not pending:
        return False, []

    total_count = 0
    while pending and total_count < max_iter:
        total_count += len(pending)
        for i, msg in enumerate(pending):
            try:
                await run_round(session, msg)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception:
                remaining = pending[i + 1:]
                if remaining:
                    session._state.pending_messages = remaining + session._state.pending_messages
                    _logger.error("排队消息处理异常，剩余 %d 条已重新入队", len(remaining))
                raise
            else:
                # ★ Bug3 修复：每成功处理一条排队消息，立即保存增量 checkpoint
                try:
                    session.save_checkpoint()
                except Exception:
                    _logger.exception("run_pending_loop: save_checkpoint 异常，不阻断消息处理")
        pending = session.pop_pending_messages()

    if total_count >= max_iter:
        _logger.error("排队消息处理超过熔断阈值 (%d)，终止循环", max_iter)
        remaining = session.pop_pending_messages()
        if remaining:
            session._state.pending_messages = remaining + session._state.pending_messages
        return True, list(session._state.pending_messages)

    return False, []


# ═══════════════════════════════════════════════════════════════
# retry
# ═══════════════════════════════════════════════════════════════

async def retry(session) -> dict:
    """重新执行上一轮对话。"""
    async with session._state.round_lock:
        session._state.retry_pending = False
        if session._state_machine.is_(SessionState.INIT):
            _ensure_idle(session)
        session._state_machine.retry()
        try:
            async with _handle_round_error(session, "retry"):
                return await _execute_round(session)
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise
        except Exception:
            # 个性化清理（_handle_round_error 已记录日志并恢复状态机）
            session._state.retry_pending = False
            raise


# ═══════════════════════════════════════════════════════════════
# run_single
# ═══════════════════════════════════════════════════════════════

async def run_single(session, prompt: str) -> dict:
    """单次对话模式。"""
    async with session._state.round_lock:
        _ensure_idle(session)
        session._agent.add_user_message(prompt)
        # ── AI 回复前提前分配 session_id ──────────────────
        if not session._state.session_id:
            session._state.session_id = session._persistence_port.generate_id()
        async with _handle_round_error(session, "run_single"):
            result = await _execute_round(session)
        session.save()
        return result


# ═══════════════════════════════════════════════════════════════
# _execute_round 子方法
# ═══════════════════════════════════════════════════════════════

def _prepare_round(session) -> None:
    """更新 agent 和 context_manager 的模型配置。"""
    session._agent.model = session._model
    if session._ctx_mgr:
        session._ctx_mgr.update_model(session._model)


def _snapshot_token_stats(session) -> tuple[int, int, int]:
    """获取前置 token 统计快照，返回 (prev_input, prev_output, prev_calls)。"""
    from ....api.stats import get_token_stats
    current = get_token_stats()
    return current["input"], current["output"], current["calls"]


async def _finalize_round(session, interrupted: bool,
                          prev_stats: tuple[int, int, int]) -> dict:
    """后置处理: enforce_message_limit → 计算 delta → 状态转换 → 自动保存 → 发射事件。

    Args:
        session: ChatSession 实例
        interrupted: agent.run() 是否被中断
        prev_stats: _snapshot_token_stats() 返回的前置快照

    Returns:
        结果字典（含 interrupted/session_id/delta/elapsed）
    """
    # 消息限制（用 try 保护，确保即使异常也能执行后续状态转换）
    try:
        if session._ctx_mgr:
            session._ctx_mgr.enforce_message_limit()
    except Exception as exc:
        _logger.exception("enforce_message_limit 异常: %s", exc)

    # 计算本轮消耗
    delta = _compute_token_delta(session, prev_stats)

    # 状态转换（由 StateMachineMiddleware 自动完成，此处为兜底）
    if session._state_machine.is_(SessionState.RUNNING):
        try:
            if interrupted:
                session._state_machine.interrupt()
            else:
                session._state_machine.complete_round()
        except InvalidTransitionError:
            _logger.warning("异步状态转换失败: %s → %s，执行强制恢复",
                            session._state_machine.name,
                            "interrupt" if interrupted else "complete")
            _force_state_recovery(session)
        except Exception as exc:
            _logger.exception("状态转换异常，执行强制恢复: %s", exc)
            _force_state_recovery(session)

    # 自动保存
    session_id = await _auto_save(session)

    # 发射事件并返回
    return _emit_round_events(session, interrupted, session_id, delta)


async def _execute_round(session) -> dict:
    """执行一轮对话的公共逻辑（编排方法）。"""
    _prepare_round(session)
    prev_stats = _snapshot_token_stats(session)
    session._emit("round_start")
    interrupted: bool = await session._agent.run()
    return await _finalize_round(session, interrupted, prev_stats)


def _compute_token_delta(session, prev_stats: tuple[int, int, int]) -> dict:
    """计算本轮 token 消耗增量，返回 delta 字典。

    Args:
        session: ChatSession 实例
        prev_stats: (prev_input, prev_output, prev_calls) 前置快照元组

    Returns:
        {"input": int, "output": int, "calls": int}
    """
    from ....api.stats import get_token_stats
    prev_input, prev_output, prev_calls = prev_stats
    current = get_token_stats()
    delta = {
        "input": current["input"] - prev_input,
        "output": current["output"] - prev_output,
        "calls": current["calls"] - prev_calls,
    }
    return delta


async def _auto_save(session) -> str | None:
    """自动保存会话，返回 session_id（无可保存内容时返回 None）。"""
    try:
        return await session._persistence_mgr.auto_save(
            lambda: session._agent.messages,
        )
    except Exception as exc:
        _logger.exception("自动保存会话失败: %s", exc)
        _force_state_recovery(session)
        return None


def _emit_round_events(session, interrupted: bool, session_id: str | None,
                       delta: dict) -> dict:
    """发射 round 事件，返回结果字典。

    变更行为：
    - Pipeline CancelledError 时额外保存 checkpoint（Bug 4）：检查 pipeline
      的 ctx.checkpoint_requested 标记，若为 True 则在已有 save_checkpoint()
      之后再次调用 save_checkpoint()，确保被取消的模型调用状态也被持久化。
    """
    from ....api.stats import get_token_stats, get_session_start_time

    current = get_token_stats()
    elapsed = time.time() - get_session_start_time()
    if delta["input"] > 0 or delta["output"] > 0:
        prices = session._config_port.get_token_prices()
        session._emit("cost_update",
                      delta=delta,
                      total=current,
                      model=session._model,
                      prices=prices,
                      session_elapsed=elapsed,
                      messages=session._agent.messages)

    if interrupted:
        # ★ Bug4: 检查 Pipeline 的 checkpoint_requested 标记
        #   （CancelledError 路径设置的），避免重复调用 save_checkpoint()
        # HACK: 通过 pipeline._last_ctx（私有属性）跨模块访问 checkpoint_requested
        #       是设计上的耦合。当前阶段保持此耦合以最小化变更范围，
        #       后续大版本重构时可考虑通过 Event/Callback 机制解耦。
        pipe_ctx = getattr(session._agent.pipeline, '_last_ctx', None)
        try:
            if pipe_ctx is not None and pipe_ctx.checkpoint_requested:
                _logger.warning("Pipeline CancelledError 标记已检测，保存 checkpoint")
                session.save_checkpoint()
            else:
                session.save_checkpoint()
        except Exception as exc:
            _logger.warning("save_checkpoint 失败，不阻断事件发射: %s", exc)
        session._emit("interrupted")

    session._emit("round_end",
                  interrupted=interrupted,
                  session_id=session_id,
                  delta=delta,
                  elapsed=elapsed)

    # ★ 修复：每轮执行完成后复位 retry_pending，避免应用层误判需要自动续接
    session._state.retry_pending = False

    return {
        "interrupted": interrupted,
        "session_id": session_id,
        "delta": delta,
        "elapsed": elapsed,
    }
