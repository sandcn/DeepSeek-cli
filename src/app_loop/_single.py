"""单次模式 — 从 app_loop.py 拆分

包含：run_single_mode_async()。
"""

from __future__ import annotations

import logging

from ._utils import _non_system_messages, _save_and_show_recover
from ._session_setup import _register_session_handlers
from ._session_factory import create_session
from ._ui_factory import create_chat_ui
from ._agent_factory import _make_event_agent  # noqa: F401 — 兼容 re-export
from ..core.constants import CYAN, DIM, RESET, YELLOW
from ..api.escape_monitor import EscapeMonitor, stop_active_monitor

_logger = logging.getLogger(__name__)


def _missing_model_profile_notice() -> str | None:
    """未配置任何模型档案时返回提示文案（已配置 → None）。

    LLM 访问参数（api_key / base_url / model / provider）唯一来源 = 模型档案，
    单次模式（``-p``）无法交互配置，因此在启动时直接提示并退出。
    """
    try:
        from ..config.model_profiles import build_model_entries

        if build_model_entries():
            return None
    except Exception:
        _logger.debug("单次模式模型档案检查失败", exc_info=True)
        return None
    return (
        "未配置模型档案 —— 请先运行 `python chat.py` 并用 /models 新增："
        "① 选提供商 ② 填模型名 ③ 填 API 密钥"
    )


async def run_single_mode_async(prompt_text):
    """单次对话模式（异步版）：输入一句话，回答后退出"""
    chat_ui = create_chat_ui()
    chat_ui.start()
    from ..tui._screen import narrow_sep_width
    _sep_w = narrow_sep_width(30)
    chat_ui.write_line(f"{CYAN}  > Chat{RESET} {DIM}· 单次模式{RESET}")
    chat_ui.write_line(f"{DIM}  {'─' * _sep_w}{RESET}")

    notice = _missing_model_profile_notice()
    if notice:
        chat_ui.write_line(f"{YELLOW}  ! {notice}{RESET}")
        chat_ui.stop()
        stop_active_monitor()
        return

    session = create_session(event_agent=True)

    monitor = EscapeMonitor(input_instance=chat_ui._components.input)
    _register_session_handlers(session, monitor, chat_ui=chat_ui)

    try:
        await session.run_single(prompt_text)

        _save_and_show_recover(session, chat_ui)
    except Exception:
        # 异常时尝试保存会话（如果已有消息），避免对话丢失
        try:
            non_system = _non_system_messages(session)
            if non_system:
                session.save()
        except Exception:
            _logger.exception("单次模式异常路径保存会话失败")
        raise
    finally:
        chat_ui.stop()
        stop_active_monitor()
