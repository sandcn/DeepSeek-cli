"""外部编辑器终端接管/恢复测试（user_select 自定义输入 Ctrl+G，2026-10-21）。

验证 ``src.tui._external_editor.run_external_editor`` 的执行顺序与失败路径：
  - 恢复终端设置 → 拆底部栏 → 调编辑器 → 恢复底部栏 → 恢复 cbreak
    → flush stdin → 请求重绘；
  - 编辑器返回 None（不可用/异常）时仍完整恢复，并返回 None。
"""

from __future__ import annotations

from src.tui._external_editor import run_external_editor


class _Input:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def flush_stdin_buffer(self) -> None:
        self._calls.append("flush")


class _Monitor:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def restore_terminal_settings(self) -> None:
        self._calls.append("restore")

    def apply_monitor_settings(self) -> None:
        self._calls.append("apply")


class _UI:
    def __init__(self, calls: list) -> None:
        self._calls = calls
        self.input = _Input(calls)

    def teardown_bottom_bar(self) -> None:
        self._calls.append("teardown")

    def setup_bottom_bar(self) -> None:
        self._calls.append("setup")

    def request_bottom_redraw(self) -> None:
        self._calls.append("redraw")


def _patch(monkeypatch, calls: list, edited):
    ui = _UI(calls)
    monkeypatch.setattr(
        "src.core.display_target.get_display_target", lambda: ui,
    )
    monkeypatch.setattr(
        "src.api.escape_monitor.get_active_monitor",
        lambda: _Monitor(calls),
    )
    monkeypatch.setattr(
        "src.tui._external_editor.edit_in_vim_sync", lambda text: edited,
    )
    return ui


def test_run_external_editor_restores_terminal_and_bottom_bar(monkeypatch) -> None:
    calls: list = []
    _patch(monkeypatch, calls, "edited")

    assert run_external_editor("original") == "edited"
    assert calls == ["restore", "teardown", "setup", "apply", "flush", "redraw"]


def test_run_external_editor_restores_even_on_editor_failure(monkeypatch) -> None:
    calls: list = []
    _patch(monkeypatch, calls, None)

    assert run_external_editor("original") is None
    assert calls == ["restore", "teardown", "setup", "apply", "flush", "redraw"]
