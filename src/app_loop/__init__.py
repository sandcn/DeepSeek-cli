"""应用主循环包 — 从 app_loop.py 拆分

子模块分工：
- _loop.py — InteractiveLoop 类（主循环）/ run_interactive_mode_async
- _single.py — 单次模式（_make_event_agent / run_single_mode_async）
- _utils.py — 工具函数（_non_system_messages / _put_and_wait / _merge_prefill / etc.）
- _session_setup.py — 会话设置（SessionState / _RoundResult / _setup_session / etc.）
- _handlers.py — 命令处理器（_handle_retry_sentinel / _handle_editmsg_cmd / _handle_model_cmd）
- _editor.py — 编辑器集成（edit_in_vim_sync / vim 编辑功能）
- _special_keys.py — 特殊按键回调工厂（make_special_key_callback / vim/editmsg/switch_model）
"""

from __future__ import annotations

# 公开 API
from ._loop import InteractiveLoop, run_interactive_mode_async
from ._single import run_single_mode_async, _make_event_agent
from ._utils import (
    _non_system_messages, _put_and_wait, _merge_prefill,
    _exit_save_and_stop, _save_and_show_recover, _save_loop_snapshot,
    _RETRY_SENTINEL, _MSG_DONE_TIMEOUT,
)
from ._session_setup import (
    SessionState, _RoundResult, _setup_session,
    _make_round_callbacks, _register_session_handlers,
)
from ._editor import edit_in_vim_sync
from ._special_keys import make_special_key_callback
from ._handlers import (
    _handle_retry_sentinel, _handle_editmsg_cmd, _handle_model_cmd,
)

__all__ = [
    "InteractiveLoop", "run_interactive_mode_async",
    "run_single_mode_async", "_make_event_agent",
    "_non_system_messages", "_put_and_wait", "_merge_prefill",
    "_exit_save_and_stop", "_save_and_show_recover", "_save_loop_snapshot",
    "_RETRY_SENTINEL", "_MSG_DONE_TIMEOUT",
    "SessionState", "_RoundResult", "_setup_session",
    "_make_round_callbacks", "_register_session_handlers",
    "_handle_retry_sentinel", "_handle_editmsg_cmd", "_handle_model_cmd",
    "edit_in_vim_sync", "make_special_key_callback",
]
