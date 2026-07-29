"""单次模式 — 从 app_loop.py 拆分

包含：_make_event_agent() 和 run_single_mode_async()。
"""

from __future__ import annotations

import asyncio
import logging

from ._utils import _non_system_messages, _save_and_show_recover
from ._session_setup import _register_session_handlers

from ..core.session import ChatSession
from ..core.agent import Agent
from ..core.constants import CYAN, DIM, RESET
from ..api.escape_monitor import EscapeMonitor, stop_active_monitor
from ..tui.consumer import ChatUIConsumer

_logger = logging.getLogger(__name__)


def _make_event_agent():
    """创建通过 EventBus 发布事件的 Agent 实例。"""
    from ..core.adapters.display import DefaultDisplayAdapter
    from ..core.adapters.events import DisplayEventBusAdapter
    from ..core.adapters.output import DefaultOutputAdapter
    return Agent(
        display_port=DefaultDisplayAdapter(source="agent"),
        event_port=DisplayEventBusAdapter(source="agent"),
        output_port=DefaultOutputAdapter(),
    )


async def run_single_mode_async(prompt_text):
    """单次对话模式（异步版）：输入一句话，回答后退出"""
    chat_ui = ChatUIConsumer()
    chat_ui.start()
    from ..tui._screen import narrow_sep_width
    _sep_w = narrow_sep_width(30)
    chat_ui.write_line(f"{CYAN}  > Chat{RESET} {DIM}· 单次模式{RESET}")
    chat_ui.write_line(f"{DIM}  {'─' * _sep_w}{RESET}")

    session = ChatSession(agent=_make_event_agent())
    session.initialize()

    monitor = EscapeMonitor(input_instance=chat_ui._components.input)
    _register_session_handlers(session, monitor, chat_ui=chat_ui)

    try:
        result = await session.run_single(prompt_text)

        delta = result.get("delta", {})
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
