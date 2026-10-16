"""Esc 清空输入（空闲时单次按下）—— 默认值 / 分发 / 装配 / 帮助表单元测试。

需求（2026-10）：「按一次 esc 要清空输入，在没有流式输出的时候」。

覆盖：
- ``TuiConfig.esc_cancel_input`` 默认 True（空闲时单次 Esc 清空输入）；
- ``InputDispatcher._esc_cancel_input`` 默认 True（无装配注入场景一致）；
- 真实 stdin 字节路径（``\\x1b`` 经 ``read_stdin_once``）在空闲 + 缓冲非空时
  **单次** Esc 清空缓冲、不触发中断回调（ESC 的清空/中断分发在
  ``_dispatch_byte`` 内联路径，故全部用例走真实字节输入）；
- 流式输出中（``status_active=True``）Esc 不取消输入（走中断、保留输入）；
- 显式 ``set_esc_cancel_input(False)`` 回退旧语义（仅中断、保留输入）；
- 空缓冲 + 空闲时 Esc 走中断路径（无内容可清）；
- 清空路径仍触发「杀后台 bash/subagent」回调（2026-08-21 语义保持）；
- 清空路径回显空串 + 关闭补全弹窗（dismiss 回调被调用）；
- 搜索模式（Ctrl+R）优先退搜索、双 Esc（interrupt 事件）不误清空；
- 装配接线：``TuiConfig.esc_cancel_input`` → ``Input.set_esc_cancel_input``，
  活跃状态回调读 ``model.status.status_active``；
- 帮助表 ``SHORTCUT_ROWS`` 与输入区占位提示同步（单一真源，避免两处漂移）。
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from src.tui._config import TuiConfig
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser


def _make_dispatcher():
    """构造真实 dispatcher（pipe fd 模拟 stdin）；返回 (d, be, r, w)。"""
    r, w = os.pipe()
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    d = InputDispatcher(io, be, InputParser(io=io))
    return d, be, r, w


def _press_esc(d, w) -> None:
    """经真实 stdin 字节路径按一次纯 Esc（``0x1b``）并处理。"""
    os.write(w, b"\x1b")
    assert d.read_stdin_once() is True


# ── 默认值 ────────────────────────────────────────────


def test_tui_config_default_esc_cancel_input_enabled():
    """``TuiConfig`` 默认启用 Esc 取消输入（空闲时单次 Esc 清空输入）。"""
    assert TuiConfig.defaults().esc_cancel_input is True
    assert TuiConfig().esc_cancel_input is True


def test_dispatcher_default_esc_cancel_input_enabled():
    """dispatcher 自身默认启用（无装配注入场景与生产默认一致）。"""
    d, _be, r, w = _make_dispatcher()
    try:
        assert d._esc_cancel_input is True
    finally:
        os.close(r)
        os.close(w)


# ── 单次 Esc 清空输入（空闲 / 非流式） ──────────────────


def test_single_esc_clears_input_when_idle():
    """空闲 + 缓冲非空：**一次** Esc（``\\x1b`` 字节）即清空输入，不中断。"""
    d, be, r, w = _make_dispatcher()
    try:
        interrupts: list = []
        d.set_interrupt_callback(lambda: interrupts.append("interrupt"))
        be.set_buffer("未提交的草稿")

        _press_esc(d, w)

        assert be.get_current_text() == ""
        assert interrupts == []  # 不触发中断（清空语义优先）
    finally:
        os.close(r)
        os.close(w)


def test_single_esc_clear_echoes_empty_and_dismisses_completion():
    """清空路径回显空串 + 关闭补全弹窗（dismiss 回调被调用）。"""
    d, be, r, w = _make_dispatcher()
    try:
        echoed: list = []
        dismissed: list = []
        be.set_echo_callback(lambda text, pos=-1: echoed.append(text))
        d.set_dismiss_completion_callback(lambda: dismissed.append(True))
        be.set_buffer("abc")

        _press_esc(d, w)

        assert be.get_current_text() == ""
        assert echoed and echoed[-1] == ""
        assert dismissed == [True]
    finally:
        os.close(r)
        os.close(w)


def test_esc_clear_still_kills_background_tasks():
    """清空路径仍触发纯 Esc 的「杀后台 bash/subagent」回调（2026-08-21 语义）。"""
    d, be, r, w = _make_dispatcher()
    try:
        kills: list = []
        d.set_kill_background_callback(lambda: kills.append("kill"))
        be.set_buffer("draft")

        _press_esc(d, w)

        assert be.get_current_text() == ""
        assert kills == ["kill"]
    finally:
        os.close(r)
        os.close(w)


# ── 流式输出中不清空（中断语义保持） ────────────────────


def test_esc_keeps_input_while_streaming():
    """流式输出中（status_active=True）：Esc 中断且**保留**输入内容。"""
    d, be, r, w = _make_dispatcher()
    try:
        interrupts: list = []
        d.set_interrupt_callback(lambda: interrupts.append("interrupt"))
        d.set_active_status_callback(lambda: True)
        be.set_buffer("生成期间敲的草稿")

        _press_esc(d, w)

        assert be.get_current_text() == "生成期间敲的草稿"
        assert interrupts == ["interrupt"]
        assert d._io.interrupted is True
    finally:
        os.close(r)
        os.close(w)


def test_esc_keeps_input_when_cancel_disabled():
    """显式关闭 esc_cancel_input：Esc 保持旧语义（仅中断、保留输入）。"""
    d, be, r, w = _make_dispatcher()
    try:
        interrupts: list = []
        d.set_interrupt_callback(lambda: interrupts.append("interrupt"))
        d.set_esc_cancel_input(False)
        be.set_buffer("保留我")

        _press_esc(d, w)

        assert be.get_current_text() == "保留我"
        assert interrupts == ["interrupt"]
    finally:
        os.close(r)
        os.close(w)


def test_esc_with_empty_buffer_takes_interrupt_path():
    """空缓冲 + 空闲：无可清内容 → 走中断路径（不清缓冲、仍杀后台任务）。"""
    d, be, r, w = _make_dispatcher()
    try:
        interrupts: list = []
        kills: list = []
        d.set_interrupt_callback(lambda: interrupts.append("interrupt"))
        d.set_kill_background_callback(lambda: kills.append("kill"))

        _press_esc(d, w)

        assert be.get_current_text() == ""
        assert interrupts == ["interrupt"]
        assert kills == ["kill"]
    finally:
        os.close(r)
        os.close(w)


def test_double_esc_batch_is_interrupt_and_keeps_input():
    """双 Esc 同批到达（``\\x1b\\x1b`` → interrupt 事件）不在取消路径，保留输入。"""
    d, be, r, w = _make_dispatcher()
    try:
        interrupts: list = []
        d.set_interrupt_callback(lambda: interrupts.append("interrupt"))
        be.set_buffer("草稿")

        os.write(w, b"\x1b\x1b")
        assert d.read_stdin_once() is True

        assert be.get_current_text() == "草稿"
        assert interrupts == ["interrupt"]
    finally:
        os.close(r)
        os.close(w)


def test_esc_in_search_mode_exits_search_first():
    """搜索模式（Ctrl+R）优先：Esc 先退出搜索（恢复搜索前缓冲），不清空。"""
    d, be, r, w = _make_dispatcher()
    try:
        be.set_buffer("历史")
        be.search_enter("历史")
        assert be.is_search_active() is True

        _press_esc(d, w)

        assert be.is_search_active() is False
        assert be.get_current_text() == "历史"
    finally:
        os.close(r)
        os.close(w)


# ── 判定条件 / 装配接线 ───────────────────────────────


def test_should_cancel_input_requires_enabled_nonempty_idle():
    """``_should_cancel_input`` 三条件：启用 + 缓冲非空 + 空闲（无流式输出）。"""
    d, be, r, w = _make_dispatcher()
    try:
        assert d._should_cancel_input() is False  # 空缓冲
        be.set_buffer("x")
        assert d._should_cancel_input() is True
        d.set_active_status_callback(lambda: True)
        assert d._should_cancel_input() is False  # 流式中
        d.set_active_status_callback(lambda: False)
        d.set_esc_cancel_input(False)
        assert d._should_cancel_input() is False  # 显式关闭
    finally:
        os.close(r)
        os.close(w)


def test_active_status_cb_reads_model_status():
    """``_make_active_status_cb`` 读 ``model.status.status_active``（流式判定真源）。"""
    from src.tui._assembly_steps import _make_active_status_cb

    status = SimpleNamespace(status_active=False)
    cb = _make_active_status_cb(SimpleNamespace(status=status))
    assert cb() is False
    status.status_active = True
    assert cb() is True


def test_assembly_wires_esc_cancel_input_and_active_status():
    """装配接线：config → input.set_esc_cancel_input + 活跃状态回调注入。"""
    import src.tui._assembly_steps as steps

    src = open(steps.__file__, encoding="utf-8").read()
    assert "input_instance.set_esc_cancel_input(tui_config.esc_cancel_input)" in src
    assert "input_instance.set_active_status_callback(_make_active_status_cb(model))" in src


def test_input_facade_forwards_esc_cancel_input():
    """``Input`` 外观转发到 dispatcher（薄外观零逻辑改动）。"""
    from src.tui._input import Input

    inp = Input(fd=os.open(os.devnull, os.O_RDONLY), history_file=Path("/dev/null"))
    try:
        inp.set_esc_cancel_input(False)
        assert inp._dispatcher._esc_cancel_input is False
        inp.set_esc_cancel_input(True)
        assert inp._dispatcher._esc_cancel_input is True
    finally:
        os.close(inp.fd)


# ── 帮助表 / 提示同步 ─────────────────────────────────


def test_shortcut_rows_describe_esc_clear_input():
    from src.core.internal.commands._command_core import SHORTCUT_ROWS

    flat = {key: desc for row in SHORTCUT_ROWS for key, desc in row}
    assert flat.get("Esc") == "清空输入/中断"


def test_input_placeholder_mentions_single_esc_clear():
    from src.tui.app.input_area import _PLACEHOLDER_ROTATION

    joined = " ".join(_PLACEHOLDER_ROTATION)
    assert "Esc 清空输入" in joined
    assert "双击清空" not in joined


def test_help_view_esc_row_rendered():
    """帮助速查视图渲染快捷键表含更新后的 Esc 描述（同源 SHORTCUT_ROWS）。"""
    from src.tui.app.help_view import _help_rows

    text = "\n".join("".join(run.text for run in line) for line in _help_rows(0))
    assert "清空输入/中断" in text
