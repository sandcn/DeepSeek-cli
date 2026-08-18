"""editmsg「很多上文时按回车不能编辑对应消息（1 条消息也复现）」根因修复
回归测试——旧 input router 吞 Enter。

★ 背景（2026-08-19 bug，旧 router 残留窗口）：

模态弹窗（EditMsgSelectPopup）确认后，``message_editor`` 在 finally 中清理
``model.editmsg_select`` + ``model.bottom_view``——但 **input router 由渲染
线程每帧发布**（``reconciler.render → _publish_input_router → dispatcher
set_input_hook_router``）：清理时刻到渲染线程完成下一帧之间存在窗口（10Hz
节流 + 帧耗时，大量上文重放时一帧 100ms~1s+），期间 dispatcher 持有的旧
router 仍含已卸载弹窗的 ``SelectInput`` use_input handler + ``use_modal``
吞噬：

  用户在 prefill 注入后按 Enter（提交编辑）
  → ``_dispatch_key_event(enter)`` → ``_router_consume`` → 旧 router
    SelectInput enter 分支 ``_call(on_select)``（对已 done 的旧 es 无害）
    → **返回 True 消费** → ``_enter()`` 不执行 → prefill 不提交
  → 「按回车没反应，要再按一次」——很多上文时窗口长（大概率复现），
    1 条消息快速连按（确认弹窗 Enter → prefill Enter 间隔 <300ms）也命中。

修复（``InkSession.flush_input_router`` + 渲染帧序号通知）：
  - ``_render_frame`` 末尾 ``_advance_frame_seq``（帧号 +1，唤醒达标 waiter）；
  - ``flush_input_router(timeout)``：注册 waiter（target = 帧号 + 2——调用时
    渲染线程可能正渲染读到清理前模型的旧帧，等两帧保证新 router 不含弹窗
    hooks）+ force 唤醒渲染线程 → 阻塞等待；
  - ``message_editor._interactive_message_select`` finally 清理后调用
    （超时 2s 降级继续，渲染线程挂起不死锁）；
  - ``user_select`` 工具 finally 同样接入（同类窗口预防）。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_parser import InputParser
from src.tui._input_dispatcher import InputDispatcher
from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.app.editmsg_select import EditMsgSelectPopup
from src.tui.ink import h, TEXT
from src.tui.ink.reconciler import Reconciler
from src.tui.ink._hooks_input import set_input_router_callback
from src.tui.ink.session import InkSession


# ── 测试辅助 ──────────────────────────────────────────────

class _SessionStub:
    """轻量 session 桩：借用真实 InkSession 的 flush_input_router /
    _advance_frame_seq 逻辑（render 线程运行中走等待分支，不触同步渲染）。"""

    flush_input_router = InkSession.flush_input_router
    _advance_frame_seq = InkSession._advance_frame_seq

    def __init__(self):
        self._render_running = True
        self._frame_seq = 0
        self._frame_seq_lock = threading.Lock()
        self._frame_flush_waiters = []
        self._bottom_redraw_requested = threading.Event()
        self._dirty = False
        self._cmd_event = threading.Event()

    def request_bottom_redraw(self):
        pass


class _BB:
    def __init__(self, model, session):
        self._model = model
        self._session = session


class _InputStub:
    """message_editor 轮询所需的最小 input 桩（interrupted 可控）。"""

    def __init__(self):
        self.interrupted = False

    def clear_interrupted(self):
        self.interrupted = False


def _make_dispatcher(pipe_r: int):
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


class _RenderLoop:
    """模拟生产渲染架构（输入分发与渲染帧解耦）：

    - 输入线程：每 10ms ``process_events``（对齐渲染循环 INPUT phase——
      分发用**最近发布**的 router）；
    - 渲染线程：每 ``render_interval`` 秒 ``render``（构建树 + 发布新
      router）+ 帧号推进——``render_interval=0.3`` 模拟「很多上文」慢帧
      （router 更新滞后，旧 router 残留窗口长，bug 稳定复现）。
    """

    def __init__(self, dispatcher, rec, root, model, render_interval: float = 0.3):
        self._d = dispatcher
        self._rec = rec
        self._root = root
        self._model = model
        self._render_interval = render_interval
        self._stop = threading.Event()
        self.session = _SessionStub()

        def _Root(props):
            m = props["model"]
            if getattr(m, "bottom_view", "") == "editmsg":
                return h(EditMsgSelectPopup, {
                    "model": m, "width": 80,
                    "key": f"em-{getattr(m.editmsg_select, 'seq', 0)}",
                })
            return h(TEXT, {"children": "normal"})

        self._root_element_fn = _Root
        self._input_thread = threading.Thread(target=self._run_input, daemon=True)
        self._render_thread = threading.Thread(target=self._run_render, daemon=True)

    def _run_input(self):
        while not self._stop.is_set():
            self._d.process_events()
            time.sleep(0.01)

    def _run_render(self):
        while not self._stop.is_set():
            self._rec.render(
                self._root, h(self._root_element_fn, {"model": self._model}),
                80, 24,
            )
            self.session._advance_frame_seq()
            self._stop.wait(self._render_interval)

    def start(self):
        self._input_thread.start()
        self._render_thread.start()

    def stop(self):
        self._stop.set()
        self._input_thread.join(timeout=2.0)
        self._render_thread.join(timeout=2.0)


def _open_popup(model, seq: int, count: int = 3):
    model.editmsg_select = EditMsgSelectState(
        visible=True, seq=seq, title="选择要编辑的消息",
        options=[f"消息{i}" for i in range(1, count + 1)],
        selected=count - 1,
        deadline=time.monotonic() + 10,
    )
    model.bottom_view = "editmsg"


def _wait_frame_frames(loop: _RenderLoop, frames: int, timeout: float = 3.0):
    """等待渲染线程完成指定帧数（router 已随之发布对应帧）。"""
    with loop.session._frame_seq_lock:
        target = loop.session._frame_seq + frames
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with loop.session._frame_seq_lock:
            if loop.session._frame_seq >= target:
                return True
        time.sleep(0.01)
    return False


# ═══════════════════════════════════════════════════════════
# 1. flush_input_router 单元级（等待/唤醒/超时）
# ═══════════════════════════════════════════════════════════

def test_advance_frame_seq_wakes_waiter_at_target():
    """帧号递增：达到 target（+2）才唤醒——一帧后不唤醒、两帧后唤醒。"""
    stub = _SessionStub()
    ev = threading.Event()
    with stub._frame_seq_lock:
        target = stub._frame_seq + 2
        stub._frame_flush_waiters.append((target, ev))

    stub._advance_frame_seq()  # seq+1 < target
    assert ev.is_set() is False

    stub._advance_frame_seq()  # seq+2 == target
    assert ev.is_set() is True
    assert stub._frame_flush_waiters == []


def test_flush_input_router_timeout_removes_waiter():
    """超时返回 False 且移除 waiter（列表不残留）。"""
    stub = _SessionStub()
    ok = stub.flush_input_router(timeout=0.05)
    assert ok is False
    assert stub._frame_flush_waiters == []


def test_flush_input_router_returns_after_two_frames():
    """等两帧语义：渲染线程推进两帧后 flush 返回 True。"""
    stub = _SessionStub()

    result = {}

    def _flusher():
        result["ok"] = stub.flush_input_router(timeout=3.0)

    t = threading.Thread(target=_flusher, daemon=True)
    t.start()
    time.sleep(0.05)
    assert not result  # 仍在等待

    stub._advance_frame_seq()
    time.sleep(0.05)
    assert not result  # 一帧不足（防旧帧早退）

    stub._advance_frame_seq()
    t.join(timeout=2.0)
    assert result.get("ok") is True


def test_flush_input_router_render_stopped_sync_render():
    """render 线程未运行：走 request_bottom_redraw 同步渲染分支（True）。"""
    stub = _SessionStub()
    stub._render_running = False
    rendered = []
    stub.request_bottom_redraw = lambda: rendered.append(True)
    assert stub.flush_input_router(1.0) is True
    assert rendered == [True]


# ═══════════════════════════════════════════════════════════
# 2. 根因级端到端：旧 router 吞 Enter（修复前 vs 修复后）
# ═══════════════════════════════════════════════════════════

def _run_editmsg_round(r_write, dispatcher, be, model, loop, seq,
                       flush_enabled: bool):
    """一轮完整交互：编辑器打开弹窗 → Enter 确认 → （flush）→ Enter 提交。

    Returns:
        第二次 Enter 后的排队提交文本（None=被吞/未提交）。
    """
    from src.tui.pipeline.message_editor import MessageEditor

    session = loop.session
    if not flush_enabled:
        # 模拟修复前：flush 为 no-op（旧 router 残留窗口保持原样）
        session.flush_input_router = lambda timeout=2.0: True

    editor = MessageEditor(bottom_bar=_BB(model, session), input_=_InputStub())
    user_msgs = [(i, {"role": "user", "content": f"m{i}"}) for i in range(3)]
    items = [f"消息{i}" for i in range(1, 4)]

    # 编辑器轮询在后台跑（对齐生产：主协程阻塞轮询，render 线程驱动确认）
    result = {}

    def _select():
        result["idx"] = editor._interactive_message_select(user_msgs, items)

    t = threading.Thread(target=_select, daemon=True)
    t.start()

    # 等弹窗挂载（编辑器已设置 es + bottom_view，渲染线程渲染两帧后
    # router 含弹窗 hooks——之后的 Enter 才会走弹窗确认路径）
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        es = model.editmsg_select
        if getattr(es, "visible", False) and getattr(es, "seq", 0) == seq:
            break
        time.sleep(0.01)
    _wait_frame_frames(loop, 2)

    # 第一次 Enter：确认弹窗（选默认最后一条）
    os.write(r_write, b"\r")

    t.join(timeout=8.0)
    assert not t.is_alive(), "编辑器轮询超时未退出"
    assert result["idx"] == 2  # 默认选中最后一条（原始索引 2）

    # 弹窗已清理 + （修复后）已 flush 新 router——立即第二次 Enter（模拟
    # prefill 注入后快速提交，落在旧 router 残留窗口内）
    be.set_buffer("编辑后的消息")
    os.write(r_write, b"\r")
    # 轮询等待提交（慢渲染 300ms/帧：无 flush 时旧 router 吞掉 Enter——
    # 永不提交；有 flush 时新 router 放行 → _enter() 提交）
    queued = None
    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        queued = be.get_queued_input()
        if queued is not None:
            break
        time.sleep(0.02)
    try:
        return queued
    finally:
        # 清场：丢弃可能的残留字节（下一轮干净开始）
        dispatcher._io.drain_pending()
        be.reset()


def test_old_router_swallows_enter_without_flush():
    """修复前行为固化：无 flush 时第二次 Enter 被旧 router 吞（不提交）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    set_input_router_callback(d.set_input_hook_router)
    model = AppModel()
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    loop = _RenderLoop(d, rec, root, model)
    loop.start()
    try:
        queued = _run_editmsg_round(w, d, be, model, loop, seq=1, flush_enabled=False)
        # 修复前：Enter 被旧 router 消费 → _enter() 未执行 → 无排队提交
        assert queued is None
    finally:
        loop.stop()
        os.close(w)
        os.close(r)


def test_flush_router_restores_enter_after_editmsg():
    """修复后：弹窗清理 flush 新 router → 第二次 Enter 正常提交（不被吞）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    set_input_router_callback(d.set_input_hook_router)
    model = AppModel()
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    loop = _RenderLoop(d, rec, root, model)
    loop.start()
    try:
        queued = _run_editmsg_round(w, d, be, model, loop, seq=1, flush_enabled=True)
        assert queued == "编辑后的消息"
        # 弹窗状态已清理
        assert model.bottom_view == ""
        assert not model.editmsg_select.visible
    finally:
        loop.stop()
        os.close(w)
        os.close(r)


def test_flush_called_in_editor_cleanup(monkeypatch):
    """message_editor finally 清理后调用 session.flush_input_router（接线）。"""
    from src.tui.pipeline.message_editor import MessageEditor
    import src.tui.pipeline.message_editor as me_mod

    model = AppModel()
    calls = []

    class _Session:
        def request_bottom_redraw(self):
            calls.append("redraw")

        def flush_input_router(self, timeout=2.0):
            calls.append(("flush", timeout))
            return True

    editor = MessageEditor(
        bottom_bar=_BB(model, _Session()), input_=_InputStub(),
    )

    def _sleep_confirm(_s):
        es = model.editmsg_select
        if not es.done:
            es.try_set_final("confirmed", [es.options[0]])

    monkeypatch.setattr(me_mod.time, "sleep", _sleep_confirm)
    user_msgs = [(0, {"role": "user", "content": "hi"})]
    idx = editor._interactive_message_select(user_msgs, ["1. ● │ hi"])
    assert idx == 0
    assert ("flush", 2.0) in calls


# ═══════════════════════════════════════════════════════════
# 3. consumer / user_select 接线
# ═══════════════════════════════════════════════════════════

def test_consumer_flush_input_router_delegates():
    """ChatUIConsumer.flush_input_router 委托 engine（user_select 接线用）。"""
    from src.tui._consumer import ChatUIConsumer

    calls = []

    class _Engine:
        def flush_input_router(self, timeout=2.0):
            calls.append(timeout)
            return True

    consumer = SimpleNamespace()
    ChatUIConsumer.flush_input_router.__get__(consumer)(1.5)
    # SimpleNamespace 无 _engine——用真实绑定验证委托路径
    consumer2 = SimpleNamespace(_engine=_Engine())
    assert ChatUIConsumer.flush_input_router.__get__(consumer2)(1.5) is True
    assert calls == [1.5]


def test_consumer_flush_input_router_attribute_error_safe():
    """engine 无该方法（旧桩）时安全返回 False 不抛异常。"""
    from src.tui._consumer import ChatUIConsumer

    consumer = SimpleNamespace(_engine=SimpleNamespace())
    assert ChatUIConsumer.flush_input_router.__get__(consumer)(0.1) is False


# ═══════════════════════════════════════════════════════════
# 4. W4：prefill 注入前的用户 Enter（空提交）自动提交
# ═══════════════════════════════════════════════════════════

class _W4Input:
    """W4 测试桩：可控排队提交 + 缓冲，wait 恒超时。"""

    def __init__(self, queued=None, current=""):
        self._queued = queued
        self._current = current
        self.set_calls = []
        self.echo_calls = []

    def get_queued_input(self):
        v = self._queued
        self._queued = None
        return v

    def get_current_text(self):
        return self._current

    def set_buffer(self, text):
        self.set_calls.append(text)
        self._current = text

    def echo(self, text=""):
        self.echo_calls.append(text)

    def wait_until_ready(self, timeout=None):
        return False

    def drain_all(self):
        return ("", "")


class _Monitor:
    is_alive = True


def test_w4_empty_stale_submit_auto_submits_prefill():
    """W4 修复：stale 为空串（用户在 prefill 注入前按的 Enter，缓冲恒空
    的空提交）→ 注入 prefill 后直接作为已提交文本返回——按一次 Enter 即
    完成编辑（修复前被静默丢弃，用户需再按一次）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _W4Input(queued="")  # 用户 Enter 产生的空提交
    orch = TuiInputOrchestrator(inp)
    ret = orch.wait_for_user_input(_Monitor(), prefill="编辑后的消息", timeout=0.1)
    assert ret == "编辑后的消息"  # 自动提交（不再等待新 Enter）
    # set_buffer 注入了 prefill（随后按提交语义清空）
    assert inp.set_calls == ["编辑后的消息", ""]


def test_w4_nonempty_stale_still_drained():
    """W4 边界：stale 非空（流程内部残留，如 '/editmsg' 重复提交）仍照旧
    丢弃——等待用户新输入（超时返回空，不自动提交）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _W4Input(queued="/editmsg")
    orch = TuiInputOrchestrator(inp)
    ret = orch.wait_for_user_input(_Monitor(), prefill="旧内容", timeout=0.1)
    assert ret == ""
    assert inp.set_calls == ["旧内容"]  # 仅注入（含窗口期字符拼接为 prefill）


def test_w4_no_stale_normal_inject():
    """W4 边界：无排队提交（None）→ 行为不变（注入 prefill 等待 Enter）。"""
    from src.tui._input_orchestrator import TuiInputOrchestrator

    inp = _W4Input(queued=None)
    orch = TuiInputOrchestrator(inp)
    ret = orch.wait_for_user_input(_Monitor(), prefill="旧内容", timeout=0.1)
    assert ret == ""
    assert inp.set_calls == ["旧内容"]


# ═══════════════════════════════════════════════════════════
# 5. W6：es 设置前 dismiss 提前置位 _selection_ready → 清除
# ═══════════════════════════════════════════════════════════

def test_w6_early_dismiss_signal_cleared_before_polling(monkeypatch):
    """W6 修复：Enter 在「dismiss 替换 → es 设置」窗口经 dismiss 路径提前
    置位 _selection_ready → 轮询前清除 → 组件确认为准（导航后的目标
    消息）——修复前轮询第一轮即 break，编辑的是默认最后一条（错误消息）。"""
    import src.tui.pipeline.message_editor as me_mod
    from src.tui.pipeline.message_editor import MessageEditor

    model = AppModel()
    editor = MessageEditor(bottom_bar=_BB(model, _SessionStub()), input_=_InputStub())

    # 模拟：用户按 Ctrl+O 后见无反应又按 Enter（es 尚未设置，
    # visible=False → es_active 守卫不生效 → _selection_confirmed 置位）
    editor._editmsg_dismiss_cb()
    assert editor._selection_ready.is_set() is True

    # 组件在轮询期间确认（用户导航到第 2 条后 Enter）
    def _sleep_confirm(_s):
        es = model.editmsg_select
        if not es.done:
            es.selected = 1
            es.try_set_final("confirmed", [es.options[1]])

    monkeypatch.setattr(me_mod.time, "sleep", _sleep_confirm)
    user_msgs = [(0, {"role": "user", "content": "m0"}),
                 (2, {"role": "user", "content": "m2"}),
                 (4, {"role": "user", "content": "m4"})]
    idx = editor._interactive_message_select(user_msgs, ["1. m0", "2. m2", "3. m4"])
    # 修复前：提前置位 → 第一轮 break → selected=默认最后一条 → idx=4（错）
    assert idx == 2  # 组件确认的第 2 条


def test_w6_no_component_confirm_times_out_not_confirmed(monkeypatch):
    """W6 边界：提前置位被清除且组件从未确认 → 不因残留信号误确认
    （deadline 到 → timeout → 返回 None，不编辑任何消息）。"""
    import src.tui.pipeline.message_editor as me_mod
    from src.tui.pipeline.message_editor import MessageEditor

    model = AppModel()
    editor = MessageEditor(bottom_bar=_BB(model, _SessionStub()), input_=_InputStub())
    editor._editmsg_dismiss_cb()  # 提前置位
    editor._selection_confirmed = True

    model2 = model

    # 缩短 deadline：第一次 sleep 时把 deadline 置为已过（立即超时退出）
    def _sleep_expire(_s):
        es = model2.editmsg_select
        if not es.done:
            es.deadline = 0.0

    monkeypatch.setattr(me_mod.time, "sleep", _sleep_expire)
    user_msgs = [(0, {"role": "user", "content": "m0"}),
                 (2, {"role": "user", "content": "m2"})]
    idx = editor._interactive_message_select(user_msgs, ["1. m0", "2. m2"])
    # 修复前：_selection_ready 提前置位 → break → action=confirmed → idx=2
    # 修复后：信号已清除 + done 未置位 → 超时 → None（取消语义）
    assert idx is None


# ═══════════════════════════════════════════════════════════
# 6. W5 加固：flush_input_router 慢渲染自适应（帧进展续期）
# ═══════════════════════════════════════════════════════════

def test_w5_flush_router_slow_render_progress_extends_wait():
    """W5 加固：慢渲染（0.3s/帧，两帧 0.6s > 软超时 0.5s）帧号持续推进 →
    续期等待 → 两帧完成后返回 True（修复前固定 0.5s 超时误降级）。"""
    stub = _SessionStub()
    stop = threading.Event()

    def _slow_frames():
        while not stop.is_set():
            time.sleep(0.3)
            stub._advance_frame_seq()

    t = threading.Thread(target=_slow_frames, daemon=True)
    t.start()
    try:
        ok = stub.flush_input_router(timeout=0.5)
        assert ok is True  # 帧进展续期 → 两帧（0.6s+）后达标唤醒
    finally:
        stop.set()
        t.join(timeout=2.0)


def test_w5_flush_router_hung_render_times_out_fast():
    """W5 加固：渲染线程挂起（帧号零推进）→ 软超时内返回 False（硬上限
    只在有进展续期时生效——不无限等）。"""
    stub = _SessionStub()  # 无任何帧推进
    t0 = time.monotonic()
    ok = stub.flush_input_router(timeout=0.3)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 2.0  # 无进展不触发 10s 硬上限
    assert stub._frame_flush_waiters == []
