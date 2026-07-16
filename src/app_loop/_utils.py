"""应用主循环工具函数 — 从 app_loop.py 拆分

纯工具函数：消息列表过滤、队列操作、prefill 合并、退出保存。
"""

from __future__ import annotations

import asyncio
import logging

from ..chat_msgs import save_session, get_recover_cmd
from ..paths import CHAT_MSGS_DIR
from ..ui.colors import DIM, RESET

_logger = logging.getLogger(__name__)

# ── 常量 ──

# 重试哨兵 — 放入 MessageQueue 表示执行 session.retry()
_RETRY_SENTINEL = object()

# _put_and_wait 中 msg_done.wait() 的最大等待秒数
# Bug 7: 超时保护防止死锁，正常情况下 set 在数百毫秒内完成
_MSG_DONE_TIMEOUT = 30.0


# ── 辅助函数 ──

def _non_system_messages(session) -> list[dict]:
    """返回非 system 角色的消息列表。"""
    return [m for m in session.messages if m.get('role') != 'system']


async def _put_and_wait(queue, msg: object, msg_done: asyncio.Event) -> None:
    """将消息放入队列并等待消费者处理完成。

    封装了 queue.put() + msg_done.wait() + msg_done.clear() 三步操作，
    消除 _handle_round 中的重复代码。
    """
    await queue.put(msg)
    if not msg_done.is_set():
        try:
            await asyncio.wait_for(msg_done.wait(), timeout=_MSG_DONE_TIMEOUT)
        except asyncio.TimeoutError:
            _logger.warning("msg_done.wait() 超时 (%ss)，强制继续", _MSG_DONE_TIMEOUT)
    msg_done.clear()


def _merge_prefill(state, session) -> str:
    """合并预填文本：将 LLM 生成期间捕获的用户键入与 state.prefill 合并。"""
    prefill = state.prefill
    state.prefill = ""
    captured = session.captured_prefill
    if captured:
        captured = ''.join(c for c in captured if c.isprintable() or c in ('\n', '\t'))
        if captured:
            prefill = (captured + " " + prefill).strip() if prefill else captured
        session.captured_prefill = ''
    return prefill


async def _save_loop_snapshot(session, chat_ui=None) -> None:
    """保存 /loop 循环前后的对话快照"""
    non_system = _non_system_messages(session)
    if non_system:
        sid = await asyncio.to_thread(save_session, session.messages, session.model)
        filepath = CHAT_MSGS_DIR / f"{sid}.json"
        if chat_ui is not None:
            chat_ui.write_line(f"  {filepath.name}")
        else:
            print(f"  {filepath.name}", flush=True)


def _exit_save_and_stop(session, chat_ui=None) -> None:
    """退出前保存会话 + 停止 EscapeMonitor"""
    try:
        non_system = _non_system_messages(session)
        if non_system:
            _save_and_show_recover(session, chat_ui)
    except Exception:
        _logger.debug("退出前保存会话失败（非关键）")
    from ..api.escape_monitor import stop_active_monitor
    stop_active_monitor()


def _save_and_show_recover(session, chat_ui=None):
    """保存对话并输出恢复命令"""
    non_system_msgs = _non_system_messages(session)
    if not non_system_msgs:
        if chat_ui is not None:
            chat_ui.write_line(f"\n{DIM}再见{RESET}")
        else:
            print(f"\n{DIM}再见{RESET}", flush=True)
        return

    sid = session.save()
    if sid:
        msg = f"\n{DIM}再见   恢复: {get_recover_cmd(sid)}{RESET}"
    else:
        msg = f"\n{DIM}再见{RESET}"
    if chat_ui is not None:
        chat_ui.write_line(msg)
    else:
        print(msg, flush=True)
