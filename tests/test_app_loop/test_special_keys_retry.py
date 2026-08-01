"""test_special_keys_retry — Ctrl+R retry 动作（Claude TUI parity 步骤 3.4）。

验证 _special_keys.py 工厂 'retry' 动作返回 '/retry'（复用命令层 RetryCommand）。
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.app_loop._special_keys import make_special_key_callback


def _make_factory(loop=None, session=None, state=None, chat_ui=None, monitor=None):
    return make_special_key_callback(
        loop or MagicMock(),
        session or MagicMock(),
        state or MagicMock(),
        chat_ui or MagicMock(),
        monitor=monitor,
    )


class TestRetryAction:
    def test_retry_returns_slash_retry(self):
        """'retry' 动作返回 '/retry' 文本（复用命令层 RetryCommand）。"""
        cb = _make_factory()
        assert cb("retry", "some text") == "/retry"

    def test_retry_returns_retry_regardless_of_text(self):
        """'retry' 与输入文本无关（恒返回 '/retry'）。"""
        cb = _make_factory()
        assert cb("retry", "") == "/retry"
        assert cb("retry", "任意输入") == "/retry"
