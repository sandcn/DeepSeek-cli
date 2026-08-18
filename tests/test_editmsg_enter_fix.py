"""editmsg 残留 Enter（CR+LF）竞态修复回归测试。

★ 背景（2026-08-19 bug：很多上文时按回车大概率不能编辑对应消息）：

CR+LF 终端按 Enter 发送 ``\\r\\n``——CR 触发 enter 事件后，LF 是同一按键的
残留字节，须经 ``_enter_residual_pending`` 标记丢弃。修复前标记带 0.5s
固定时间窗口（``_ENTER_RESIDUAL_WINDOW``）：消息很多时渲染线程一帧（大消息
区重放/markdown 渲染）耗时可超 0.5s，CR 与 LF 分开被 os.read 读到（终端/
SSH 分包）时 LF 的消费时刻超出窗口 → 被当作用户新按的 Enter：

  - 打开 /editmsg 的回车残留 LF → 弹窗被「自动确认」→ 编辑默认选中的
    最后一条（不是用户想编辑的那条）；
  - 确认选择的回车残留 LF → 弹窗已关、suppress_enter 已恢复 → ``_enter()``
    直接把 prefill 提交重发（用户没机会编辑）。

修复：残留 LF 丢弃改为纯字节序语义（CR 置位后**下一个分发的字节**是 LF
则丢弃，无时间窗口——标记在任何字节处理时先清除，只影响紧邻字节）；
标准路径下弹窗活跃（visible 且未 done）时 dismiss 回调忽略 Enter 确认
（组件是确认权威，持有用户导航后的 selected）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_parser import InputParser
from src.tui._input_dispatcher import InputDispatcher
from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.pipeline.message_editor import MessageEditor


def _make_dispatcher(pipe_r: int):
    """构造真实 InputDispatcher（pipe fd 模拟 stdin）。"""
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


# ── 场景 1：打开 /editmsg 的回车残留 LF 超窗后自动确认弹窗 ──

def test_open_cmd_residual_lf_after_window_not_autoconfirm():
    """/editmsg 提交回车（CR）的残留 LF 在弹窗打开后到达（超 0.5s 窗口）
    → 必须被丢弃，不得触发弹窗「确认」（dismiss 回调）。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)

    dismiss_calls = []
    # 第 1 步：正常输入模式，CR 提交（/editmsg 命令回车）→ 置位残留标记
    os.write(w, b"\r")
    assert d.read_stdin_once() is True
    assert d._enter_residual_pending is True

    # 第 2 步：渲染线程忙（大量消息重放），LF 未被消费，窗口超时
    time.sleep(0.6)

    # 第 3 步：弹窗打开（editmsg 选择期间——suppress_enter + dismiss 劫持）
    d.set_suppress_enter(True)
    d.set_dismiss_completion_callback(lambda: dismiss_calls.append(1))

    # 第 4 步：LF 终于到达（终端/SSH 分包晚到）→ 必须丢弃（不确认弹窗）
    os.write(w, b"\n")
    d.read_stdin_once()

    assert dismiss_calls == [], "残留 LF 超窗后被当作新 Enter，弹窗被自动确认"
    os.close(w)
    os.close(r)


# ── 场景 2：确认选择的回车残留 LF 超窗后误提交 prefill ──

def test_confirm_residual_lf_after_window_not_submit():
    """弹窗确认回车（CR）的残留 LF 在弹窗关闭、suppress 恢复后到达
    （超 0.5s 窗口）→ 必须被丢弃，不得把 prefill 误提交重发。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)

    enter_events = []
    # 弹窗期间：router 模拟 EditMsgSelectPopup 的 SelectInput（消费 Enter）
    d.set_input_hook_router(
        lambda ev: (enter_events.append(ev.kind) == "enter" or True)
        if ev.kind == "enter" else False
    )
    d.set_suppress_enter(True)

    # 第 1 步：确认回车 CR → router 消费（组件确认）+ 置位残留标记
    os.write(w, b"\r")
    assert d.read_stdin_once() is True
    assert enter_events == ["enter"]
    assert d._enter_residual_pending is True

    # 第 2 步：编辑器轮询退出 → 弹窗清理（router 移除）+ suppress 恢复；
    # 渲染线程忙于大重放（大量消息），LF 未被消费，窗口超时
    d.set_input_hook_router(None)
    d.set_suppress_enter(False)
    be.set_buffer("旧消息内容（prefill）")
    time.sleep(0.6)

    # 第 3 步：LF 终于到达 → 必须丢弃（不误提交 prefill）
    os.write(w, b"\n")
    d.read_stdin_once()

    assert be.has_queued_input() is False, "残留 LF 超窗后触发 _enter() 误提交"
    assert be.get_current_text() == "旧消息内容（prefill）"
    os.close(w)
    os.close(r)


# ── 残留丢弃语义不回归 ──

def test_residual_lf_in_window_still_dropped():
    """窗口内到达的 LF 仍被丢弃（既有行为不回归）。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)

    os.write(w, b"\r")
    d.read_stdin_once()
    assert d._enter_residual_pending is True

    # LF 紧随到达（未超窗）→ 丢弃
    os.write(w, b"\n")
    d.read_stdin_once()
    # 无 enter 事件产生（suppress + dismiss 计数验证）
    calls = []
    d.set_suppress_enter(True)
    d.set_dismiss_completion_callback(lambda: calls.append(1))
    d.read_stdin_once()  # 管道已空 → False，无分发
    assert calls == []
    os.close(w)
    os.close(r)


def test_non_lf_byte_after_cr_not_dropped():
    """CR 后下一个字节是普通字符 → 正常分发（标记清除，不误丢用户输入）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)

    os.write(w, b"\r")
    d.read_stdin_once()  # 提交（置位标记）
    be.reset()

    os.write(w, b"a")
    d.read_stdin_once()  # 'a' 正常入缓冲
    assert be.get_current_text() == "a"

    # 标记已被 'a' 清除：后续 LF（用户新按键）不再被丢
    os.write(w, b"\n")
    d.read_stdin_once()
    assert be.has_queued_input() is True  # LF 作为新 Enter 正常提交
    os.close(w)
    os.close(r)


def test_second_cr_after_cr_not_swallowed():
    """CR 后第二个 CR（用户双击 Enter）不被丢（2026-08-06 语义保持）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)

    os.write(w, b"\r")
    d.read_stdin_once()
    be.reset()

    os.write(w, b"\r")  # 双击：第二个 CR 是新提交
    d.read_stdin_once()
    assert be.has_queued_input() is True
    os.close(w)
    os.close(r)


# ── 修复 2：标准路径弹窗活跃时 dismiss-Enter 忽略 ──

class _FakeInput:
    """Input 桩（dismiss 回调测试用）。"""

    def __init__(self):
        self.interrupted = False


class _FakeSession:
    def request_bottom_redraw(self):
        pass


class _BB:
    def __init__(self, model, session):
        self._model = model
        self._session = session


def test_dismiss_cb_ignored_when_standard_popup_active():
    """标准路径弹窗活跃（visible 且未 done）→ Enter-dismiss 忽略
    （组件是确认权威，挂载窗口期 Enter 不再误判确认编辑最后一条）。"""
    model = AppModel()
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, options=["A", "B"], selected=1,
    )
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=_BB(model, _FakeSession()), input_=inp)

    editor._editmsg_dismiss_cb()
    assert editor._selection_confirmed is False
    assert editor._selection_cancelled is False
    assert editor._selection_ready.is_set() is False


def test_dismiss_cb_cancel_still_works_when_popup_active():
    """弹窗活跃时 Esc（interrupted=True）→ 取消语义保留（挂载窗口 Esc 可取消）。"""
    model = AppModel()
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, options=["A"], selected=0,
    )
    inp = _FakeInput()
    inp.interrupted = True
    editor = MessageEditor(bottom_bar=_BB(model, _FakeSession()), input_=inp)

    editor._editmsg_dismiss_cb()
    assert editor._selection_cancelled is True
    assert editor._selection_ready.is_set() is True


def test_dismiss_cb_after_popup_done_still_confirms():
    """弹窗已 done（组件已确认、轮询尚未退出）→ dismiss 确认语义保持
    （P2-5 双信号兜底不回归）。"""
    model = AppModel()
    es = EditMsgSelectState(visible=True, seq=1, options=["A"], selected=0)
    es.try_set_final("confirmed", ["A"])
    model.editmsg_select = es
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=_BB(model, _FakeSession()), input_=inp)

    editor._editmsg_dismiss_cb()
    assert editor._selection_confirmed is True
    assert editor._selection_ready.is_set() is True


def test_dismiss_cb_legacy_path_still_confirms():
    """无 model（legacy 补全弹窗路径）→ dismiss 保持确认语义（兼容不回归）。"""
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=None, input_=inp)
    editor._editmsg_dismiss_cb()
    assert editor._selection_confirmed is True
    assert editor._selection_ready.is_set() is True


# ── 挂载窗口期残留 Enter 不再截断消息（editor 轮询级） ──

def test_select_mount_window_stale_enter_waits_for_component(monkeypatch):
    """弹窗设置后、组件挂载前的窗口期 dismiss-Enter → 轮询不退出；
    组件随后确认（selected=0 用户导航值）→ 编辑第一条而非默认最后一条。"""
    import src.tui.pipeline.message_editor as me_mod

    model = AppModel()
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=_BB(model, _FakeSession()), input_=inp)

    state = {"n": 0}

    def _sleep(_s):
        state["n"] += 1
        if state["n"] == 1:
            # 挂载窗口期：Enter 经旧路径触发 dismiss（修复前 → 误确认）
            editor._editmsg_dismiss_cb()
        elif state["n"] == 2:
            # 组件挂载后：用户导航 selected=0 并确认
            es = model.editmsg_select
            if not es.done:
                es.selected = 0
                es.try_set_final("confirmed", [es.options[0]])

    monkeypatch.setattr(me_mod.time, "sleep", _sleep)

    user_msgs = [(0, {"role": "user", "content": "hi"}), (2, {"role": "user", "content": "yo"})]
    idx = editor._interactive_message_select(
        user_msgs, ["1. ● │ hi", "2. ● │ yo"],
    )
    # 组件确认的 selected=0（用户导航值）生效——不是 dismiss 路径的默认最后一条
    assert idx == 0
