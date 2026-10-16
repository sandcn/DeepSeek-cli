"""F12 = 会话日志视图开关 —— 解析 / 分发 / 开关回调 / 帮助表单元测试。

覆盖：
- CSI ``\\x1b[N~`` 功能键解析（F1-F12）；
- ``_dispatch_key_event`` 的 f12 → logs_toggle 回调（**不经命令队列**，
  流式输出期间也能立即打开）；
- ESC 路径（``\\x1b[24~`` 经 read_stdin_once）同样到达 logs_toggle；
- ``_make_logs_toggle_cb`` 翻转 fullscreen + 强制构建视图数据；
- 快捷键速查表（``SHORTCUT_ROWS``）包含 F12。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent


def _make_dispatcher():
    """构造真实 InputDispatcher（pipe fd 模拟 stdin）。"""
    r, w = os.pipe()
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), w, be


# ── 解析 ──────────────────────────────────────────────


@pytest.mark.parametrize("param,kind", [
    ([24], "f12"),
    ([23], "f11"),
    ([21], "f10"),
    ([15], "f5"),
    ([11], "f1"),
    ([12], "f2"),
])
def test_csi_tilde_function_keys(param, kind):
    assert InputParser._dispatch_csi(list(param), "~").kind == kind


@pytest.mark.parametrize("param,kind", [
    ([1], "home"), ([7], "home"), ([3], "delete"), ([4], "end"),
    ([5], "page_up"), ([6], "page_down"),
])
def test_csi_tilde_non_function_keys_unchanged(param, kind):
    """功能键扩展不改变既有 CSI ~ 语义（Home/Delete/End/PgUp/PgDn）。"""
    assert InputParser._dispatch_csi(list(param), "~").kind == kind


# ── 分发 ──────────────────────────────────────────────


def test_f12_dispatches_logs_toggle():
    d, w, _be = _make_dispatcher()
    try:
        hits: list = []
        d.set_logs_toggle_callback(lambda: hits.append(True))
        d._dispatch_key_event(KeyEvent(kind="f12"))
        assert hits == [True]
    finally:
        os.close(w)


def test_f12_through_read_stdin_once_escape_path():
    """真实 ESC 路径：``\\x1b[24~`` 经 read_stdin_once 到达 logs_toggle。

    回归防护：ESC 转义路径的分发元组必须包含 f5-f12（修复前仅 f1-f4，
    F12 解析后未进入 ``_dispatch_key_event`` 被静默忽略）。
    """
    d, w, _be = _make_dispatcher()
    try:
        hits: list = []
        d.set_logs_toggle_callback(lambda: hits.append(True))
        os.write(w, b"\x1b[24~")
        assert d.read_stdin_once() is True
        assert hits == [True]
    finally:
        os.close(w)


def test_f1_through_read_stdin_once_ss3_path():
    """真实 SS3 路径：``\\x1bOP``（F1）→ 帮助速查开关（回归防护）。"""
    d, w, _be = _make_dispatcher()
    try:
        hits: list = []
        d.set_help_toggle_callback(lambda: hits.append(True))
        os.write(w, b"\x1bOP")
        assert d.read_stdin_once() is True
        assert hits == [True]
    finally:
        os.close(w)


def test_other_function_keys_noop():
    d, w, _be = _make_dispatcher()
    try:
        hits: list = []
        d.set_logs_toggle_callback(lambda: hits.append(True))
        d._dispatch_key_event(KeyEvent(kind="f5"))
        assert hits == []
    finally:
        os.close(w)


def test_logs_toggle_callback_registered_name():
    """回调名登记在统一注册表（``set_logs_toggle_callback`` 经 register_callback）。"""
    d, w, _be = _make_dispatcher()
    try:
        cb = lambda: None  # noqa: E731
        d.set_logs_toggle_callback(cb)
        assert d.get_callback("logs_toggle") is cb
    finally:
        os.close(w)


# ── 开关回调（装配层） ────────────────────────────────


def test_make_logs_toggle_cb_builds_and_toggles():
    from src.tui._assembly_steps import _make_logs_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    forced: list = []
    model.logs_refresher = lambda force=False: forced.append(force) or True
    redraws: list = []
    session = SimpleNamespace(request_bottom_redraw=lambda: redraws.append(True))

    cb = _make_logs_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "logs"
    assert forced == [True]           # 打开时强制构建数据（立即显示）
    assert model.logs_view.visible is False or model.logs_view.visible is True
    assert redraws

    cb()
    assert model.fullscreen == ""     # 再按关闭


def test_make_logs_toggle_cb_without_refresher():
    """未注入刷新器（无会话 / 测试桩）时仍可打开视图（不崩溃）。"""
    from src.tui._assembly_steps import _make_logs_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_logs_toggle_cb(model, session)
    cb()
    assert model.fullscreen == "logs"


def test_make_logs_toggle_cb_resets_state_on_reopen():
    from src.tui._assembly_steps import _make_logs_toggle_cb
    from src.tui.app.model import AppModel

    model = AppModel()
    model.logs_refresher = lambda force=False: True
    session = SimpleNamespace(request_bottom_redraw=lambda: None)
    cb = _make_logs_toggle_cb(model, session)

    cb()
    model.logs_view.done = True
    model.logs_view.selected = 3
    model.logs_view.follow_tail = False
    cb()          # 关闭
    cb()          # 再次打开
    assert model.fullscreen == "logs"
    assert model.logs_view.done is False
    assert model.logs_view.selected == 0
    assert model.logs_view.follow_tail is True


# ── 帮助表 / 特殊键清理 ───────────────────────────────


def test_shortcut_rows_include_f12():
    from src.core.internal.commands._command_core import SHORTCUT_ROWS

    flat = {key: desc for row in SHORTCUT_ROWS for key, desc in row}
    assert flat.get("F12") == "会话日志"
    assert "F1 / Ctrl+/" in flat


def test_submit_special_actions_excludes_logs():
    """F12 不走 special key 提交路径（改为直接 toggle 回调）。"""
    from src.app_loop._special_handlers import builtin_special_key_ids
    from src.tui._input_dispatcher import _SUBMIT_SPECIAL_ACTIONS

    assert set(_SUBMIT_SPECIAL_ACTIONS) == {"editmsg", "retry"}
    assert "logs" not in builtin_special_key_ids()
