"""editmsg「很多上文时按回车不能编辑对应消息」窗口期 Enter 丢失修复回归测试。

★ 背景（2026-08-19 bug：弹窗确认后按 Enter 大概率没反应，再按一次才编辑）：

从「弹窗确认」到「prefill 注入输入框」之间存在长窗口（大量上文时 1~10s）：
flush_input_router 等慢帧 → flush_stdin_buffer 丢字节 → 插件清 _input_ready
丢空提交 → clear+display 全量重放 + flush。期间用户按的 Enter 走三条无痕
丢弃路径（suppress 吞 / flush 丢 / 清 _input_ready 丢）→「按回车没反应」；
prefill 终于注入后再按 Enter 才生效（「再按次回车又能编辑了」）。

修复（双管齐下，底层）：
  1. **prefill 提前注入**（editmsg/deitmsg 插件）：截断完成后立即
     ``set_buffer(prefill)`` + ``echo``（不再等 wait_for_user_input——那在
     全量重放 flush 之后），``state["prefill"]`` 清空（已履行注入职责）。
     窗口期输入框立即可见可编辑，用户 Enter 提交的是实际内容（非空提交，
     orchestrator 直接从队列返回）——一次 Enter 完成编辑。
  2. **窗口期 Enter 提交意图捕获**（dispatcher capture/deferred）：
     message_editor 弹窗确认后开启 capture；此后被抑制吞掉（enter 分支
     else）、被 flush 丢弃（InputIO 报告 Enter 字节）的 Enter 记为
     deferred 提交意图；插件 finally 清 _input_ready / set_buffer 覆盖
     前把存活提交转 deferred；注入 prefill 后消费兑现（自动 ``_enter()``
     提交）——用户那一次 Enter 的意图不丢。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_parser import InputParser
from src.tui._input_dispatcher import InputDispatcher
from src.tui.app.model import AppModel
from src.tui.pipeline.message_editor import MessageEditor
import src.tui.pipeline.message_editor as me


# ── 测试桩 ────────────────────────────────────────────────

class _FakeSession:
    def __init__(self, messages):
        agent = SimpleNamespace(messages=messages)
        self.agent = agent
        self.messages = messages
        self.captured_prefill = ""
        self.retry_pending = False

    def sync_retry_pending(self):
        last = self.messages[-1] if self.messages else None
        self.retry_pending = bool(
            last is not None and last.get("role") == "user"
        )

    def reset_retry_pending_for_edit(self, has_prefill):
        self.retry_pending = False


class _FakeMonitor:
    def clear_interrupted(self):
        pass


class _CapInput:
    """Input 桩：完整实现 capture/deferred 协议并记录调用。"""

    def __init__(self):
        self.interrupted = False
        self.buffer = ""
        self.set_calls = []
        self.echo_calls = []
        self.enter_calls = []
        self.capture_calls = []
        self.deferred_marks = 0
        self.queued = None
        self.has_queued = False

    def clear_interrupted(self):
        self.interrupted = False

    def get_queued_input(self):
        text = self.queued
        self.queued = None
        self.has_queued = False
        return text

    def has_queued_input(self):
        return self.has_queued

    def get_current_text(self):
        return self.buffer

    def set_buffer(self, text):
        self.set_calls.append(text)
        self.buffer = text
        self.queued = None
        self.has_queued = False

    def echo(self, text=""):
        self.echo_calls.append(text)

    def wait_until_ready(self, timeout=None):
        return self.has_queued

    def set_suppress_enter(self, v):
        self.set_calls.append(("suppress", v))

    def get_dismiss_completion_callback(self):
        return None

    def set_dismiss_completion_callback(self, cb):
        pass

    def flush_stdin_buffer(self, *a, **k):
        pass

    def drain_all(self):
        return (None, "")

    def set_enter_capture(self, active):
        self.capture_calls.append(active)

    def mark_deferred_enter(self):
        self.deferred_marks += 1

    def consume_deferred_enter(self):
        consumed = self.deferred_marks > 0
        self.deferred_marks = 0
        return consumed

    def _enter(self):
        self.enter_calls.append(self.buffer)
        self.queued = self.buffer
        self.has_queued = True
        self.buffer = ""


class _FakeInkSession:
    def __init__(self):
        self.redraws = 0

    def request_bottom_redraw(self):
        self.redraws += 1


class _BB:
    def __init__(self, model, session):
        self._model = model
        self._session = session


class _FakeChatUI:
    def __init__(self, input_=None):
        self.lines = []
        self.input = input_
        self._input = input_
        self.bottom_bar = None

    def get_input(self):
        return self._input

    def write_line(self, text):
        self.lines.append(text)

    def clear_messages(self):
        self.lines.append(("<clear>",))

    def display_messages(self, messages, speed=0):
        self.lines.append(("<display>", len(messages)))

    def flush(self, timeout=None):
        pass


def _make_dispatcher(pipe_r: int):
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


# ═══════════════════════════════════════════════════════════
# 1. dispatcher 层：capture / deferred 协议
# ═══════════════════════════════════════════════════════════

def test_suppressed_enter_recorded_when_capture_active():
    """capture 激活期 suppress 吞掉的 Enter 记为提交意图（不再无痕丢失）。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    d.set_suppress_enter(True)
    d.set_enter_capture(True)

    os.write(w, b"\n")
    d.read_stdin_once()

    assert d._deferred_enter_pending is True, \
        "capture 激活期 suppress 吞掉的 Enter 应记为提交意图"
    os.close(w)
    os.close(r)


def test_suppressed_enter_not_recorded_without_capture():
    """capture 未激活（弹窗打开期间）suppress 吞掉的 Enter 不记录——
    那是「确认弹窗」意图，组件才是确认权威。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    d.set_suppress_enter(True)

    os.write(w, b"\n")
    d.read_stdin_once()

    assert d._deferred_enter_pending is False
    os.close(w)
    os.close(r)


def test_consume_deferred_enter_resets_flag():
    """consume 读取并清除标志（幂等——二次消费返回 False）。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    d.mark_deferred_enter()
    assert d.consume_deferred_enter() is True
    assert d.consume_deferred_enter() is False
    os.close(w)
    os.close(r)


def test_notify_flushed_enter_gated_by_capture():
    """notify_flushed_enter 仅 capture 激活时置位（普通 flush 丢弃的 Enter
    与提交意图无关）。"""
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    d.notify_flushed_enter()
    assert d._deferred_enter_pending is False
    d.set_enter_capture(True)
    d.notify_flushed_enter()
    assert d._deferred_enter_pending is True
    os.close(w)
    os.close(r)


def test_set_enter_capture_toggles_state():
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    assert d.is_enter_capture_active() is False
    d.set_enter_capture(True)
    assert d.is_enter_capture_active() is True
    d.set_enter_capture(False)
    assert d.is_enter_capture_active() is False
    os.close(w)
    os.close(r)


# ═══════════════════════════════════════════════════════════
# 2. InputIO 层：flush_stdin_buffer 报告丢弃的 Enter 字节
# ═══════════════════════════════════════════════════════════

def test_flush_stdin_buffer_reports_dropped_enter_lf():
    r, w = os.pipe()
    io = InputIO(r)
    os.write(w, b"\n")
    assert io.flush_stdin_buffer() is True, "丢弃 LF 字节应报告 True"
    os.close(w)
    os.close(r)


def test_flush_stdin_buffer_reports_dropped_enter_cr():
    r, w = os.pipe()
    io = InputIO(r)
    os.write(w, b"\r")
    assert io.flush_stdin_buffer() is True, "丢弃 CR 字节应报告 True"
    os.close(w)
    os.close(r)


def test_flush_stdin_buffer_no_enter_returns_false():
    r, w = os.pipe()
    io = InputIO(r)
    os.write(w, b"abc")
    assert io.flush_stdin_buffer() is False, "普通字符字节应报告 False"
    os.close(w)
    os.close(r)


def test_flush_stdin_buffer_empty_returns_false():
    r, w = os.pipe()
    io = InputIO(r)
    assert io.flush_stdin_buffer() is False
    os.close(w)
    os.close(r)


def test_input_facade_flush_forwards_enter_to_dispatcher():
    """Input 外观转发：io 丢弃 Enter → capture 激活时 dispatcher 记意图。"""
    from src.tui._input import Input
    r, w = os.pipe()
    inp = Input.__new__(Input)
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    inp._io = io
    inp._buffer_editor = be
    inp._dispatcher = InputDispatcher(io, be, parser)

    inp.set_enter_capture(True)
    os.write(w, b"\n")
    inp.flush_stdin_buffer()
    assert inp._dispatcher._deferred_enter_pending is True

    # capture 未激活：普通 flush 丢弃 Enter 不记意图
    inp.set_enter_capture(False)
    inp._dispatcher.consume_deferred_enter()
    os.write(w, b"\n")
    inp.flush_stdin_buffer()
    assert inp._dispatcher._deferred_enter_pending is False
    os.close(w)
    os.close(r)


def test_input_facade_capture_api_forwards():
    from src.tui._input import Input
    r, w = os.pipe()
    inp = Input.__new__(Input)
    io = InputIO(r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    inp._io = io
    inp._buffer_editor = be
    inp._dispatcher = InputDispatcher(io, be, parser)

    inp.set_enter_capture(True)
    assert inp._dispatcher.is_enter_capture_active() is True
    inp.mark_deferred_enter()
    assert inp.consume_deferred_enter() is True
    os.close(w)
    os.close(r)


# ═══════════════════════════════════════════════════════════
# 3. message_editor 层：弹窗确认后开启 capture
# ═══════════════════════════════════════════════════════════

def test_interactive_select_opens_capture_after_cleanup():
    """弹窗到终态（确认/取消）后 finally 开启 capture——此后 Enter 是
    「提交编辑」意图（被吞/被丢都记 deferred）。"""
    model = AppModel()

    # 经 request_bottom_redraw 挂钩同步确认（es 设置后主线程调用 redraw——
    # 此刻弹窗模型已 visible，模拟组件立即写 done；无跨线程时序依赖）
    class _AutoConfirmSession(_FakeInkSession):
        def request_bottom_redraw(self):
            super().request_bottom_redraw()
            es = getattr(model, "editmsg_select", None)
            if es is not None and getattr(es, "visible", False):
                es.try_set_final("confirmed", ["x"])

    sess = _AutoConfirmSession()
    inp = _CapInput()
    bb = _BB(model, sess)
    editor = MessageEditor(bottom_bar=bb, input_=inp)

    assert inp.capture_calls == []

    msgs = [(0, {"role": "user", "content": "hi"})]
    idx = editor._interactive_message_select(msgs, ["1. ● │ hi"])

    assert idx == 0, "确认后应返回选中的原始索引"
    # capture 在 finally 中开启（True）
    assert True in inp.capture_calls, "弹窗终态后应开启 Enter 捕获"


# ═══════════════════════════════════════════════════════════
# 4. editmsg_plugin 层：提前注入 + deferred 兑现
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_editmsg_prefill_injected_before_replay(monkeypatch):
    """提前注入：prefill 在消息区重放（clear_messages）**之前**注入缓冲——
    重放期间输入框已可见可编辑可提交。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [
        {"role": "user", "content": "第一"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": "第二"},
    ]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    editor_obj = MessageEditor(bottom_bar=None, input_=inp)
    monkeypatch.setattr(
        MessageEditor, "_interactive_message_select",
        lambda self, um, di: um[1][0],  # 选第 2 条用户消息（原始 idx=2）
    )

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # 提前注入：缓冲 = 旧消息内容；state["prefill"] 清空（已履行）
    assert inp.buffer == "第二"
    assert "第二" in inp.set_calls
    assert "第二" in inp.echo_calls
    assert state["prefill"] == ""
    # 截断生效（第 2 条 user（原始 idx=2）及其后被删 → 剩 2 条）
    assert len(messages) == 2
    # capture 收尾：True（editor 开启）→ False（插件收尾关闭）
    assert inp.capture_calls[-1] is False


@pytest.mark.asyncio
async def test_editmsg_deferred_enter_auto_submits_prefill(monkeypatch):
    """端到端：弹窗确认后用户按的 Enter（被 suppress 吞 / flush 丢 /
    清 _input_ready 丢）→ 注入 prefill 后自动提交——一次 Enter 完成编辑。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [{"role": "user", "content": "旧内容"}]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    # 弹窗交互桩：模拟真实流程——确认后开启 capture（message_editor finally
    # 行为）+ 用户窗口期按 Enter（suppress 吞 → deferred）
    def _fake_select(self, um, di):
        inp.set_enter_capture(True)   # 弹窗终态后 capture 开启
        inp.set_suppress_enter(True)  # （edit_current_messages 外层 finally 才恢复）
        inp.mark_deferred_enter()     # 用户 Enter 被 suppress 吞 → deferred
        inp.set_suppress_enter(False)
        return um[0][0]

    monkeypatch.setattr(MessageEditor, "_interactive_message_select", _fake_select)

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # deferred 兑现：注入 prefill 后自动 _enter 提交
    assert inp.enter_calls == ["旧内容"], "窗口期 Enter 应自动提交 prefill"
    assert inp.queued == "旧内容"
    assert inp.has_queued is True


@pytest.mark.asyncio
async def test_editmsg_deferred_not_consumed_when_live_submission(monkeypatch):
    """防重：重放期间用户又实际按 Enter（存活提交 has_queued）时不兑现
    deferred（防重复提交）。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [{"role": "user", "content": "旧内容"}]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    def _fake_select(self, um, di):
        return um[0][0]

    monkeypatch.setattr(MessageEditor, "_interactive_message_select", _fake_select)

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}

    # 注入前窗口期已有存活提交（用户 Enter 已分发提交 prefill——模拟
    # 提前注入生效后用户按 Enter 的场景）
    orig_set_buffer = inp.set_buffer

    def _set_buffer_then_submit(text):
        orig_set_buffer(text)
        inp.queued = text
        inp.has_queued = True

    inp.set_buffer = _set_buffer_then_submit
    try:
        await plugin.async_execute(SimpleNamespace(session=session, state=state))
    finally:
        inp.set_buffer = orig_set_buffer

    # 已有存活提交 → deferred 不兑现（无重复 _enter）
    assert inp.enter_calls == []
    assert inp.queued == "旧内容"


@pytest.mark.asyncio
async def test_editmsg_cancel_closes_capture_and_clears_deferred(monkeypatch):
    """取消路径（Esc 取消/未编辑）：capture 关闭 + deferred 清残留——
    标志不泄漏到下一轮（防下一轮正常 Enter 意外触发自动提交）。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [{"role": "user", "content": "hi"}]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    def _fake_select(self, um, di):
        inp.set_enter_capture(True)   # 弹窗终态（取消）后 capture 已开启
        inp.mark_deferred_enter()     # 取消后窗口期用户按了 Enter（残留）
        return None                   # 取消

    monkeypatch.setattr(MessageEditor, "_interactive_message_select", _fake_select)

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # 收尾：capture 关闭 + deferred 清除
    assert inp.capture_calls[-1] is False
    assert inp.deferred_marks == 0
    assert inp.enter_calls == []
    assert any("未编辑" in str(l) for l in chat_ui.lines)


@pytest.mark.asyncio
async def test_editmsg_queued_submission_converted_to_deferred(monkeypatch):
    """finally 清 _input_ready 前的存活空提交 → 转 deferred 意图
    （修复前直接清除 = 无痕丢弃 →「按回车没反应」）。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [{"role": "user", "content": "旧内容"}]
    session = _FakeSession(messages)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    class _LockableInput(_CapInput):
        """带 _lock/_input_ready（editmsg_plugin finally 直接访问）。"""

        def __init__(self):
            super().__init__()
            self._lock = threading.Lock()
            self._input_ready = threading.Event()
            self._submitted_text = ""

    inp = _LockableInput()
    chat_ui = _FakeChatUI(inp)

    def _fake_select(self, um, di):
        # 窗口期用户按 Enter → 空提交（缓冲空）存活于队列
        inp.queued = ""
        inp.has_queued = True
        return um[0][0]

    monkeypatch.setattr(MessageEditor, "_interactive_message_select", _fake_select)

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # 空提交被转 deferred 并在注入后兑现（自动提交 prefill）
    assert inp.enter_calls == ["旧内容"]
    assert inp.queued == "旧内容"


# ═══════════════════════════════════════════════════════════
# 5. deitmsg_plugin 层：提前注入（同构修复）
# ═══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_deitmsg_prefill_injected_before_replay(monkeypatch):
    from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin

    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "答"},
        {"role": "user", "content": "第二条"},
    ]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {"a.py": True}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    plugin = DeitmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    assert inp.buffer == "第二条"
    assert "第二条" in inp.set_calls
    assert "第二条" in inp.echo_calls
    assert state["prefill"] == ""
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_deitmsg_deferred_enter_auto_submits(monkeypatch):
    from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin

    messages = [{"role": "user", "content": "旧内容"}]
    session = _FakeSession(messages)
    inp = _CapInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    # 模拟：finally 块的 flush 时机用户按 Enter 产生空提交（只第一次——
    # needs_rerender 块内的 flush 不再模拟新按键）
    flush_count = {"n": 0}
    orig_flush = chat_ui.flush

    def _flush_then_submit(*a, **k):
        flush_count["n"] += 1
        if flush_count["n"] == 1:
            inp.queued = ""
            inp.has_queued = True

    chat_ui.flush = _flush_then_submit

    plugin = DeitmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # 空提交转 deferred → 注入后兑现（一次 Enter 完成编辑）
    assert inp.enter_calls == ["旧内容"]
    assert inp.queued == "旧内容"


# ═══════════════════════════════════════════════════════════
# 6. orchestrator 集成：prefill 已提前注入时的提交路径
# ═══════════════════════════════════════════════════════════

def test_wait_for_user_input_empty_prefill_returns_live_submission():
    """prefill 已提前注入（state 清空）后，wait_for_user_input("") 直接
    返回用户提交的实际内容（提前注入的 prefill / 编辑后文本）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _CapInput()
    orch = TuiInputOrchestrator(inp)
    # 用户 Enter 提交（缓冲已含提前注入的 prefill）
    inp.buffer = "旧内容"
    inp._enter()

    monitor = SimpleNamespace(is_alive=True)
    result = orch.wait_for_user_input(monitor, prefill="")
    assert result == "旧内容"


def test_wait_for_user_input_auto_submit_path_still_works():
    """W4 空提交自动提交路径保留（其他 prefill 来源未提前注入时兜底）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _CapInput()
    orch = TuiInputOrchestrator(inp)
    inp.queued = ""
    inp.has_queued = True

    monitor = SimpleNamespace(is_alive=True)
    result = orch.wait_for_user_input(monitor, prefill="预填内容")
    assert result == "预填内容"
