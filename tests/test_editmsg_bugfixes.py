"""editmsg / deitmsg bug 修复回归测试（P1-1 ~ P3-5）。

覆盖 2026-08-19 bug 分析报告的全部修复项：
  - P1-1 /deitmsg 先 remap 后删（remap 失败无中间态）
  - P1-2 Esc 误判确认（dismiss 回调按中断标志区分取消/确认）
  - P1-3 弹窗期间 Ctrl+C/中断 → 取消退出（含残留中断标志预清）
  - P2-2 同步 execute 友好降级（不再 raise RuntimeError）
  - P2-3 沙盒恢复失败以 ⚠ 渲染（_restore_feedback）
  - P2-4 组件 _on_select 用传入 item（权威值）构造 result
  - P2-5 prefill 注入拼接窗口期输入（orchestrator 不覆盖）
  - P3-1 矮终端行数下限 1（不再强制 6 溢出）
  - P3-2 消息摘要编号 1 基
  - P3-3 多模态消息编辑警告（EditCommand）
  - P3-5 es 实例被外部替换 → 取消退出
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.app.editmsg_select import EditMsgSelectPopup, _editmsg_item_rows
from src.tui.ink import hooks
from src.tui.ink.fiber import Fiber, TAG_FUNCTION
from src.tui.pipeline import message_editor as me
from src.tui.pipeline.message_editor import (
    EditCommand,
    MessageEditor,
    _content_has_nontext,
    _restore_feedback,
    _user_msg_summary,
)


# ── 测试桩 ────────────────────────────────────────────────

class _FakeSession:
    """最小 ChatSession 桩（messages 直连 agent.messages）。"""

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


class _FakeInput:
    """Input 桩：记录方法调用，interrupted 可控。"""

    def __init__(self):
        self.interrupted = False
        self.set_calls = []
        self.echo_calls = []
        self.buffer = ""
        self.enter_calls = 0
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

    def echo(self, text=""):
        self.echo_calls.append(text)

    def wait_until_ready(self, timeout=None):
        return False

    def set_suppress_enter(self, v):
        self.set_calls.append(("suppress", v))

    def get_dismiss_completion_callback(self):
        return None

    def set_dismiss_completion_callback(self, cb):
        pass

    def flush_stdin_buffer(self, *a, **k):
        pass

    def handle_chars(self, text):
        self.buffer += text

    def set_enter_capture(self, active):
        self.capture_calls.append(active)

    def mark_deferred_enter(self):
        self.deferred_marks += 1

    def consume_deferred_enter(self):
        consumed = self.deferred_marks > 0
        self.deferred_marks = 0
        return consumed

    def _enter(self):
        self.enter_calls += 1
        self.queued = self.buffer
        self.has_queued = True
        self.buffer = ""


class _FakeInkSession:
    def request_bottom_redraw(self):
        pass


class _BB:
    def __init__(self, model, session):
        self._model = model
        self._session = session


class _FakeChatUI:
    """ChatUIConsumer 桩：记录 write_line/clear/display。"""

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

    def display_messages(self, msgs, speed=0):
        self.lines.append(("<display>", len(msgs)))

    def flush(self, timeout=None):
        pass


def _render_editmsg_popup(model, fiber=None):
    """手动 fiber 上下文渲染 EditMsgSelectPopup（对齐 test_user_select_seq）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, EditMsgSelectPopup, {"model": model, "width": 80})
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = EditMsgSelectPopup({"model": model, "width": 80})
    finally:
        hooks._pop_current()
    return fiber, el


# ── P1-1 /deitmsg 先 remap 后删 ───────────────────────────

class _RemapFailSandbox:
    """沙盒桩：restore_to_message 成功，remap_indices 抛异常。"""

    def __init__(self):
        self.remap_called = False

    def restore_to_message(self, idx):
        return {}

    def remap_indices(self, indices):
        self.remap_called = True
        raise RuntimeError("remap boom")


@pytest.mark.asyncio
async def test_deitmsg_remap_failure_keeps_messages(monkeypatch):
    """remap 失败 → 消息未删、无 prefill、显示编辑失败（无中间态）。"""
    from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin

    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "第二条"},
    ]
    session = _FakeSession(messages)
    chat_ui = _FakeChatUI(_FakeInput())

    bad_sb = _RemapFailSandbox()
    import src.tui.pipeline.message_editor as me_mod
    monkeypatch.setattr(me_mod, "_get_sandbox_manager", lambda: bad_sb)

    plugin = DeitmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    ctx = SimpleNamespace(session=session, state=state)

    handled = await plugin.async_execute(ctx)
    assert handled is True
    # remap 被调用且抛异常 → 消息未删（修复前已 del，长度变 2）
    assert bad_sb.remap_called is True
    assert len(messages) == 3
    # prefill 未设置（用户内容不丢失在局部变量里）
    assert state["prefill"] == ""
    # 异常被捕获并显示「编辑失败」
    assert any("编辑失败" in str(l) for l in chat_ui.lines if isinstance(l, str))
    # 未重渲染（消息区未被清空重放）
    assert ("<clear>",) not in chat_ui.lines


@pytest.mark.asyncio
async def test_deitmsg_normal_path_uses_truncate_helper(monkeypatch):
    """正常路径：截断生效 + prefill 预填 + 恢复反馈 ✓ 渲染。"""
    from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin

    messages = [
        {"role": "user", "content": "第一条"},
        {"role": "assistant", "content": "回答"},
        {"role": "user", "content": "第二条"},
    ]
    session = _FakeSession(messages)
    inp = _FakeInput()
    chat_ui = _FakeChatUI(inp)

    class _OkSandbox:
        def restore_to_message(self, idx):
            return {"a.py": True, "b.py": True}

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _OkSandbox())

    plugin = DeitmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    assert len(messages) == 2  # 最后一条 user + 其后被删
    # ★ 提前注入（2026-08-19 修复）：prefill 已注入输入缓冲 + state["prefill"]
    #   清空（已履行注入职责，防 wait_for_user_input 重复注入）。
    assert "第二条" in inp.set_calls
    assert inp.buffer == "第二条"
    assert "第二条" in inp.echo_calls
    assert state["prefill"] == ""
    assert ("<clear>",) in chat_ui.lines
    # 恢复 2 个文件 → ✓ 前缀
    feedback = [l for l in chat_ui.lines if isinstance(l, str) and "已恢复" in l]
    assert feedback and "✓" in feedback[0]


# ── P1-2 dismiss 回调按中断标志区分 ───────────────────────

def test_dismiss_cb_interrupted_marks_cancel():
    """Esc 路径（中断标志已置位）→ dismiss 判定取消（不确认）。"""
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=None, input_=inp)
    inp.interrupted = True
    editor._editmsg_dismiss_cb()
    assert editor._selection_cancelled is True
    assert editor._selection_confirmed is False
    assert editor._selection_ready.is_set()


def test_dismiss_cb_no_interrupt_marks_confirmed():
    """Enter 路径（无中断标志）→ dismiss 判定确认（挂载窗口双击 Enter）。"""
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=None, input_=inp)
    editor._editmsg_dismiss_cb()
    assert editor._selection_confirmed is True
    assert editor._selection_cancelled is False
    assert editor._selection_ready.is_set()


def test_escape_orders_interrupt_before_dispatch(monkeypatch):
    """escape else 分支：_do_interrupt 先于 _dismiss_completion（P1-2 顺序）。"""
    from src.tui import _input_dispatcher as disp_mod

    calls = []
    d = SimpleNamespace(
        _dismiss_completion=lambda: calls.append("dismiss"),
        _do_interrupt=lambda: calls.append("interrupt"),
        _buffer_editor=SimpleNamespace(
            is_search_active=lambda: False,
        ),
        _should_cancel_input=lambda: False,
    )
    # 直接调用 escape 处理段（复刻 _dispatch_byte 的 else 分支）
    d._do_interrupt()
    d._dismiss_completion()
    assert calls == ["interrupt", "dismiss"]

    # 验证源码顺序（防回归）：_do_interrupt 在 _dismiss_completion 之前
    src = open(disp_mod.__file__, encoding="utf-8").read()
    marker = ("self._do_interrupt(kill_background=(kind == \"escape\"))\n"
              "                            self._dismiss_completion()")
    assert marker in src, "escape 分支顺序回退：dismiss 先于 interrupt"


# ── P1-3 / P3-5 轮询取消路径 ─────────────────────────────

def test_select_interrupt_marks_cancel(monkeypatch):
    """选择期间中断标志置位 → 轮询取消退出，不空转超时。"""
    model = AppModel()
    inp = _FakeInput()
    editor = MessageEditor(bottom_bar=_BB(model, _FakeInkSession()), input_=inp)
    monkeypatch.setattr(me.time, "sleep", lambda s: None)

    # 弹窗打开后置中断标志（模拟 Ctrl+C）
    def _sleep_and_interrupt(_s):
        inp.interrupted = True

    monkeypatch.setattr(me.time, "sleep", _sleep_and_interrupt)

    user_msgs = [(0, {"role": "user", "content": "hi"})]
    idx = editor._interactive_message_select(user_msgs, ["1. ● │ hi"])
    assert idx is None  # 取消
    assert model.bottom_view == ""
    assert not model.editmsg_select.visible


def test_select_es_replaced_marks_cancel(monkeypatch):
    """轮询期间 es 实例被外部替换（清屏）→ 取消退出（P3-5）。"""
    model = AppModel()
    editor = MessageEditor(
        bottom_bar=_BB(model, _FakeInkSession()), input_=_FakeInput(),
    )

    def _sleep_and_replace(_s):
        # 模拟 reset_display 外部替换 editmsg_select（新实例，done=False）
        model.editmsg_select = EditMsgSelectState(seq=99)

    monkeypatch.setattr(me.time, "sleep", _sleep_and_replace)
    user_msgs = [(0, {"role": "user", "content": "hi"})]
    idx = editor._interactive_message_select(user_msgs, ["1. ● │ hi"])
    assert idx is None


def test_select_still_confirms_via_component(monkeypatch):
    """正常确认路径不回归：组件写 done=confirmed → 返回选中索引。"""
    model = AppModel()
    editor = MessageEditor(
        bottom_bar=_BB(model, _FakeInkSession()), input_=_FakeInput(),
    )

    def _sleep_confirm(_s):
        es = model.editmsg_select
        if not es.done:
            es.selected = 0
            es.try_set_final("confirmed", [es.options[0]])

    monkeypatch.setattr(me.time, "sleep", _sleep_confirm)
    user_msgs = [(0, {"role": "user", "content": "hi"}), (2, {"role": "user", "content": "yo"})]
    idx = editor._interactive_message_select(user_msgs, ["1. ● │ hi", "2. ● │ yo"])
    assert idx == 0


# ── P2-2 同步 execute 降级 ────────────────────────────────

def test_editmsg_sync_execute_no_raise():
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    assert EditmsgPlugin().execute(SimpleNamespace()) is True


def test_deitmsg_sync_execute_no_raise():
    from src.core.commands.plugins.deitmsg_plugin import DeitmsgPlugin

    assert DeitmsgPlugin().execute(SimpleNamespace()) is True


def test_handle_command_editmsg_sync_path_no_crash():
    """旧命令表同步路径（registry 自动注册）：/editmsg 不再抛 RuntimeError。

    注：显式 import 插件模块触发模块级自注册——生产路径由 app_loop
    ``from ..core.commands.plugins import ...`` 链触发；单测独立运行时
    无该链（修复前依赖文件内其他测试先 import 的顺序副作用）。"""
    import src.core.commands.plugins  # noqa: F401 — 触发 EditmsgPlugin 注册
    from src.core.commands import handle_command

    messages = [{"role": "user", "content": "hi"}]
    state = {}
    handled = handle_command(
        "/editmsg", messages, state,
        lambda: "", lambda prompt="": "",
    )
    # 修复前：EditmsgPlugin.execute raise RuntimeError → 调用方崩溃
    assert handled is True


# ── P2-3 恢复反馈 ────────────────────────────────────────

def test_restore_feedback_branches():
    text, failed = _restore_feedback("已恢复 3 个文件")
    assert failed is False and text == "已恢复 3 个文件"
    text, failed = _restore_feedback("沙盒恢复失败: boom")
    assert failed is True and "boom" in text
    text, failed = _restore_feedback("")
    assert failed is False and text == "沙盒无文件需还原"


@pytest.mark.asyncio
async def test_editmsg_plugin_renders_warning_on_restore_failure(monkeypatch):
    """editmsg：恢复失败文本以 ⚠（黄）渲染而非 ✓（绿）。"""
    from src.core.commands.plugins.editmsg_plugin import EditmsgPlugin

    messages = [{"role": "user", "content": "hi"}]
    session = _FakeSession(messages)
    inp = _FakeInput()
    chat_ui = _FakeChatUI(inp)

    class _FailRestoreSandbox:
        def restore_to_message(self, idx):
            raise RuntimeError("disk broken")

        def remap_indices(self, indices):
            pass

    monkeypatch.setattr(me, "_get_sandbox_manager", lambda: _FailRestoreSandbox())

    # 预填选择路径：绕过交互（直接返回 0——patch _interactive_message_select）
    editor_obj = MessageEditor(bottom_bar=None, input_=inp)

    def _fake_select(self, user_msgs, display_items):
        return user_msgs[0][0]

    monkeypatch.setattr(
        MessageEditor, "_interactive_message_select", _fake_select,
    )

    plugin = EditmsgPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui, _monitor=_FakeMonitor()))
    state = {"model": "", "retry": False, "prefill": ""}
    await plugin.async_execute(SimpleNamespace(session=session, state=state))

    # ★ 提前注入（2026-08-19 修复）：prefill 已注入输入缓冲 + state["prefill"]
    #   清空（已履行注入职责）。
    assert "hi" in inp.set_calls
    assert inp.buffer == "hi"
    assert state["prefill"] == ""
    assert messages == []  # 截断生效
    feedback = [l for l in chat_ui.lines if isinstance(l, str) and "恢复失败" in l]
    assert feedback, chat_ui.lines
    assert "⚠" in feedback[0] and "✓" not in feedback[0]


# ── P2-4 组件 _on_select 用传入 item ─────────────────────

def test_on_select_uses_item_value_even_with_stale_highlight():
    """_on_select result 取传入 item（权威值），不受渲染帧 cur 影响。"""
    model = AppModel()
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=1, title="选择", options=["A", "B", "C"], selected=2,
    )
    fiber, el = _render_editmsg_popup(model)
    control = el.children[1]
    on_select = control.props["onSelect"]

    # 模拟事件期选中第 0 项（渲染帧 cur=2 陈旧）
    on_select({"label": "A", "value": "A"})
    es = model.editmsg_select
    assert es.done is True
    assert es.action == "confirmed"
    assert es.result == ["A"]


# ── P2-5 prefill 拼接窗口期输入 ──────────────────────────

def test_orchestrator_prefill_preserves_buffer():
    """prefill 注入时拼接缓冲中已有字符（窗口期输入不丢）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _FakeInput()
    inp.buffer = "xyz"  # 窗口期用户键入（editmsg_plugin handle_chars 写回）
    orch = TuiInputOrchestrator(inp)
    monitor = SimpleNamespace(is_alive=True)

    result = orch.wait_for_user_input(
        monitor, prefill="旧内容", timeout=0.01, input_=inp,
    )
    assert result == ""  # 超时返回空（无提交）
    assert inp.set_calls and inp.set_calls[0] == "旧内容xyz"
    assert inp.echo_calls and inp.echo_calls[0] == "旧内容xyz"


def test_orchestrator_prefill_plain_when_buffer_empty():
    """缓冲为空 → 行为与修复前一致（仅 prefill）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _FakeInput()
    orch = TuiInputOrchestrator(inp)
    monitor = SimpleNamespace(is_alive=True)
    orch.wait_for_user_input(monitor, prefill="旧内容", timeout=0.01, input_=inp)
    assert inp.set_calls and inp.set_calls[0] == "旧内容"


# ── P3-1 矮终端行数下限 ──────────────────────────────────

def test_editmsg_item_rows_short_terminal_min_one(monkeypatch):
    """矮终端（h=5）→ 行数 ≥1（不再强制 6 溢出）。"""
    from src.tui import _screen as screen_mod

    def _patch_height(h):
        class _Cache:
            def get_height(self):
                return h

        monkeypatch.setattr(
            screen_mod.TerminalWidthCache, "get_default",
            staticmethod(lambda: _Cache()),
        )

    _patch_height(5)
    assert _editmsg_item_rows() == 2  # max(1, 5-3)

    _patch_height(2)
    assert _editmsg_item_rows() == 1  # max(1, 2-3 → 负数钳到 1)


# ── P3-2 摘要编号 1 基 ───────────────────────────────────

def test_user_msg_summary_one_based():
    assert _user_msg_summary({"role": "user", "content": "hi"}, 0) == "1. ● │ hi"
    assert _user_msg_summary({"role": "user", "content": "yo"}, 2) == "3. ● │ yo"


# ── P3-3 多模态编辑警告 ──────────────────────────────────

def test_edit_command_nontext_warning():
    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "x"}},
        ]},
    ]
    agent = SimpleNamespace(messages=messages)
    state = {}
    assert EditCommand(agent, 0).execute(state) is True
    assert state["prefill"] == "看图"
    assert "非文本" in state["_prefill_warning"]


def test_edit_command_plain_no_warning():
    messages = [{"role": "user", "content": "纯文本"}]
    agent = SimpleNamespace(messages=messages)
    state = {}
    EditCommand(agent, 0).execute(state)
    assert "_prefill_warning" not in state
    assert state["prefill"] == "纯文本"


def test_content_has_nontext():
    assert _content_has_nontext([{"type": "text", "text": "a"}]) is False
    assert _content_has_nontext([{"type": "image_url"}]) is True
    assert _content_has_nontext("plain") is False
    assert _content_has_nontext(None) is False


# ── P1-3 前置：进入选择前清残留中断标志 ─────────────────

def test_edit_current_messages_clears_stale_interrupt(monkeypatch):
    """进入选择前清除残留中断标志（上一轮 Esc 残留不误判取消）。"""
    inp = _FakeInput()
    inp.interrupted = True  # 残留
    editor = MessageEditor(bottom_bar=None, input_=inp)

    agent = SimpleNamespace(messages=[{"role": "user", "content": "hi"}])

    # 无 model 环境 → legacy 路径；bb=None → 立即返回 None（无交互）
    result = editor.edit_current_messages(agent, {}, "edit")
    assert result is False
    # 残留中断标志已被清除
    assert inp.interrupted is False
