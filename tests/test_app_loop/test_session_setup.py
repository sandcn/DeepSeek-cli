"""测试 _session_setup.py — 会话初始化 + 恢复消息显示委托（P2-6 新增）。

覆盖：
  - _setup_session 在 chat_ui 非 None 时委托 display_messages（路径 A）且参数正确
    （非 system 消息过滤 + speed=0 + flush 确保首屏渲染顺序）
  - chat_ui=None 分支不抛异常（跳过恢复消息显示，非 ChatUI 上下文）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_session_mock(model: str = "model-a", messages: list | None = None,
                       retry_pending: bool = False, loaded: dict | None = None):
    """构造 mock ChatSession：满足 _setup_session 的访问路径。"""
    session = MagicMock()
    session.model = model
    session.messages = messages if messages is not None else []
    session.retry_pending = retry_pending
    session.load.return_value = loaded
    return session


class TestSetupSession:
    """_setup_session 会话初始化 + 消息显示委托。"""

    def test_delegates_display_messages_with_chat_ui(self):
        """chat_ui 非 None 时 display_messages 被调用且参数正确（P2-6）。"""
        from src.app_loop._session_setup import _setup_session

        chat_ui = MagicMock()
        session = _make_session_mock(
            model="model-b",
            messages=[
                {"role": "user", "content": "hi"},
                {"role": "system", "content": "sys"},
                {"role": "assistant", "content": "answer"},
            ],
            retry_pending=False,
            loaded={"model": "model-b"},
        )

        with patch(
            "src.app_loop._session_setup.ChatSession", return_value=session,
        ), patch("src.app_loop._single._make_event_agent"):
            result = _setup_session({"id": "sess-1"}, chat_ui)

        returned_session, state = result
        assert returned_session is session
        assert state.model == "model-b"

        # display_messages 被调用且参数正确：非 system 消息 + speed=0
        assert chat_ui.display_messages.call_count == 1
        args, kwargs = chat_ui.display_messages.call_args
        msgs = args[0]  # 位置参数：消息列表
        speed = kwargs.get("speed", 0)
        assert speed == 0
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant"]  # system 已过滤
        assert msgs[0]["content"] == "hi"

        # P3-7：display_messages 后追加 flush（确保首屏渲染顺序）
        chat_ui.flush.assert_called_once()

    def test_chat_ui_none_no_exception(self):
        """chat_ui=None 分支不抛异常（跳过恢复消息显示，P2-6）。"""
        from src.app_loop._session_setup import _setup_session

        session = _make_session_mock(
            model="model-a",
            messages=[{"role": "user", "content": "hi"}],
            loaded={"model": "model-a"},
        )

        with patch(
            "src.app_loop._session_setup.ChatSession", return_value=session,
        ), patch("src.app_loop._single._make_event_agent"):
            session_out, state = _setup_session({"id": "sess-1"}, None)

        assert session_out is session
        assert state.model == "model-a"

    def test_no_loaded_data_no_display(self):
        """loaded_data 为 None 时不调用 display_messages（无恢复消息）。"""
        from src.app_loop._session_setup import _setup_session

        chat_ui = MagicMock()
        session = _make_session_mock(model="model-a", messages=[])

        with patch(
            "src.app_loop._session_setup.ChatSession", return_value=session,
        ), patch("src.app_loop._single._make_event_agent"):
            _setup_session(None, chat_ui)

        chat_ui.display_messages.assert_not_called()
        chat_ui.flush.assert_not_called()


class TestSetupSessionTitleSync:
    """--load 恢复会话后同步终端窗口标题（起完标题 → 终端标题跟随）。"""

    def test_load_syncs_terminal_title(self):
        """恢复会话且标题非空时同步终端窗口标题并标记 ai_title_done。"""
        from src.app_loop._session_setup import _setup_session

        captured: list[str] = []
        chat_ui = MagicMock()
        session = _make_session_mock(
            model="model-b",
            messages=[{"role": "user", "content": "hi"}],
            loaded={"model": "model-b", "title": "恢复的会话标题"},
        )
        session._state = MagicMock()

        with patch(
            "src.app_loop._session_setup.ChatSession", return_value=session,
        ), patch("src.app_loop._single._make_event_agent"), patch(
            "src.tui._screen.set_window_title",
            side_effect=lambda t: captured.append(t),
        ):
            _setup_session({"id": "sess-1"}, chat_ui)

        assert captured == ["恢复的会话标题"]
        # 已有标题 → 本进程不再自动生成 AI 标题覆盖
        assert session._state.ai_title_done is True

    def test_load_skip_sync_when_no_title(self):
        """恢复会话无标题时不调用 set_window_title 也不标记 ai_title_done。"""
        from src.app_loop._session_setup import _setup_session

        captured: list[str] = []
        chat_ui = MagicMock()
        session = _make_session_mock(
            model="model-b",
            messages=[{"role": "user", "content": "hi"}],
            loaded={"model": "model-b"},
        )
        session._state = MagicMock()
        session._state.ai_title_done = False  # 模拟真实 dataclass 默认值

        with patch(
            "src.app_loop._session_setup.ChatSession", return_value=session,
        ), patch("src.app_loop._single._make_event_agent"), patch(
            "src.tui._screen.set_window_title",
            side_effect=lambda t: captured.append(t),
        ):
            _setup_session({"id": "sess-1"}, chat_ui)

        assert captured == []
        # 无标题 → 后续轮次可触发 AI 标题生成
        assert session._state.ai_title_done is False
