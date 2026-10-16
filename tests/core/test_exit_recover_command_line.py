"""退出提示（恢复命令行）测试 — ``--load`` 恢复用命令行的上屏输出。

用户需求：``exit`` 命令执行（及一切退出路径）时必须在屏幕上打印出用于
``--load`` 加载该会话的命令行，便于用户下次直接恢复会话。历史缺陷：
``--load`` 启动因会话日志恢复路径的 ``UnboundLocalError`` 直接崩溃，退出
提示完全不输出；另一缺陷为渲染侧空闲帧复用吞掉该提示命令。
"""

from __future__ import annotations

from src.app_loop._utils import _save_and_show_recover
from src.chat_msgs import get_recover_cmd


class _FakeUi:
    """最小 ChatUI 桩：记录 write_line 文本。"""

    def __init__(self):
        self.lines: list[str] = []

    def write_line(self, text: str) -> None:
        self.lines.append(text)


class _FakeSession:
    def __init__(self, messages, sid="abc123"):
        self.messages = messages
        self._sid = sid
        self.save_calls = 0

    def save(self):
        self.save_calls += 1
        return self._sid


def test_exit_prints_load_command_line():
    """有对话内容：退出提示含 ``python chat.py --load <sid>``。"""
    ui = _FakeUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="abc123")

    _save_and_show_recover(session, ui)

    assert session.save_calls == 1
    joined = "\n".join(ui.lines)
    assert "再见" in joined
    assert get_recover_cmd("abc123") == "python chat.py --load abc123"
    assert "--load abc123" in joined


def test_exit_without_content_prints_goodbye_only():
    """无对话内容（仅有 system 消息）：不保存、不发恢复命令，仅告别。"""
    ui = _FakeUi()
    session = _FakeSession([{"role": "system", "content": "sys"}])

    _save_and_show_recover(session, ui)

    assert session.save_calls == 0
    assert ui.lines and "再见" in ui.lines[-1]
    assert "--load" not in ui.lines[-1]


def test_exit_save_and_stop_prints_farewell_for_empty_session():
    """``_exit_save_and_stop``：空会话（无对话内容）也要上屏告别信息。"""
    from src.app_loop._utils import _exit_save_and_stop

    ui = _FakeUi()
    session = _FakeSession([{"role": "system", "content": "sys"}])

    _exit_save_and_stop(session, ui)

    assert ui.lines, "无对话内容时退出提示为空（用户看不到任何退出信息）"
    assert "再见" in "\n".join(ui.lines)
    assert session.save_calls == 0


def test_exit_save_and_stop_prints_recover_for_content_session():
    """``_exit_save_and_stop``：有对话内容时上屏恢复命令行。"""
    from src.app_loop._utils import _exit_save_and_stop

    ui = _FakeUi()
    session = _FakeSession([{"role": "user", "content": "hi"}], sid="sid42")

    _exit_save_and_stop(session, ui)

    assert session.save_calls == 1
    assert "--load sid42" in "\n".join(ui.lines)


def test_exit_falls_back_to_publish_output_without_chat_ui():
    """无 ChatUI（无头上下文）：经 publish_output 输出恢复命令行。"""
    import src.app_loop._utils as utils

    captured: list[str] = []
    original = utils.publish_output
    utils.publish_output = lambda text, level="raw": captured.append(text)
    try:
        session = _FakeSession([{"role": "assistant", "content": "yo"}], sid="sid9")
        _save_and_show_recover(session, None)
    finally:
        utils.publish_output = original

    assert captured
    assert "python chat.py --load sid9" in captured[-1]
