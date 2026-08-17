"""模态底部视图通用机制测试 — use_modal + BOTTOM_VIEWS + model.bottom_view。

2026-08-17（用户需求：user_select 独立出底部框为独立界面——弹窗显示时
底部框不显示、弹窗在原来底部框位置独立显示，做成通用化实现）：新增
**通用模态底部视图机制**——复用模态全屏视图（FULLSCREEN_VIEWS +
model.fullscreen）的架构模式，作用范围从「整屏」收窄为「底部区」：

  1. 框架层（ink）：``use_modal`` hook——模态输入接管（与 use_fullscreen
     同一 ``FullscreenHook`` 节点类型，语义泛化为「模态输入接管」）：激活
     期间 input router 在全部 use_input handler 未消费时**吞掉**事件（返回
     True）→ 事件不落入输入缓冲（输入区已不渲染，杜绝看不见的输入）。
  2. 应用层：``AppModel.bottom_view`` 状态 + ``app.BOTTOM_VIEWS`` 注册表
     ——App 按 id 只渲染底部区对应视图（状态栏/输入区不显示——「弹窗打开
     时底部框不显示，弹窗在原来底部框位置独立显示」）；key 约定支持
     ``(组件, key_fn)`` 元组（UserSelectPopup 用 seq 强制重挂载重置内部
     use_state）。
  3. 协议层：user_select 工具 / /editmsg 消息选择 / CommandUiAdapter
     run_bottom_bar_selection 打开时设置 ``bottom_view="user_select"``、
     清理时恢复 ""（与 UserSelectState 同生命周期）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent
from src.tui.app.app import BOTTOM_VIEWS, App
from src.tui.app.input_area import InputArea
from src.tui.app.model import AppModel, UserSelectState
from src.tui.app.status_bar import StatusBar
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import hooks
from src.tui.ink.element import h as h_el
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, FullscreenHook, InputHook
from src.tui.ink.reconciler import Reconciler

# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件（与 test_fullscreen_view 同模式）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def _make_dispatcher() -> tuple[InputDispatcher, InputBufferEditor]:
    """构造测试用 dispatcher（不经真实 stdin，与 test_trace_view 同约定）。"""
    io = InputIO(fd=0)
    be = InputBufferEditor(history_file=Path("unused"))
    parser = InputParser(io=io)
    disp = InputDispatcher(io=io, buffer_editor=be, parser=parser)
    return disp, be


def _make_model_with_blocks() -> AppModel:
    """构造带块数据的 AppModel（底部视图渲染所需）。"""
    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> 你好")])
    m.append_committed("content", [AnsiLine.of("回答内容")])
    return m


def _open_user_select(m: AppModel, options=None, seq: int = 1) -> None:
    """设置 user_select 弹窗 + 激活底部视图（模拟工具/协议打开）。"""
    us = UserSelectState()
    us.visible = True
    us.seq = seq
    us.title = "测试选择"
    us.options = options or ["选项A", "选项B"]
    us.default_options = ["选项A"]
    m.user_select = us
    m.bottom_view = "user_select"


class _StubSession:
    """最小 session 桩（request_bottom_redraw 记录调用时的 bottom_view）。"""

    def __init__(self, model=None):
        self.model = model
        self.calls = []

    def request_bottom_redraw(self):
        self.calls.append("redraw")
        if self.model is not None:
            # 记录重绘时刻的 bottom_view（打开/清理两阶段均可断言）
            self.views = getattr(self, "views", [])
            self.views.append(getattr(self.model, "bottom_view", None))


def _bottom_children(el) -> list:
    """App 元素 → 底部区（第二个 Column）的 children 列表。"""
    return list(el.children[1].children)


# ═══════════════════════════════════════════════════════════
# 1. 框架层：use_modal hook
# ═══════════════════════════════════════════════════════════

def _DemoModal(props):
    """测试组件：声明模态底部视图（is_active 由 props 控制）。"""
    from src.tui.ink import use_modal
    use_modal(props.get("active", True))
    return None


def test_use_modal_registers_hook():
    """use_modal 注册 FullscreenHook（与 use_fullscreen 同节点类型）且 is_active 正确。"""
    el, fiber = _render(_DemoModal, {"active": True})
    assert el is None
    fh = next(h for h in fiber.hooks if isinstance(h, FullscreenHook))
    assert fh.is_active is True


def test_use_modal_inactive():
    """use_modal(False) → is_active=False（不参与路由吞掉）。"""
    el, fiber = _render(_DemoModal, {"active": False})
    fh = next(h for h in fiber.hooks if isinstance(h, FullscreenHook))
    assert fh.is_active is False


def test_use_modal_fullscreen_same_node_type():
    """use_modal 与 use_fullscreen 同一 hook 节点类型（fiber 复用兼容）。"""
    from src.tui.ink import use_fullscreen, use_modal

    def _Mixed(props):
        if props.get("mode") == "fullscreen":
            use_fullscreen(True)
        else:
            use_modal(True)
        return None

    # 同一 fiber 先 use_fullscreen 再 use_modal（hook 位类型一致 → 复用不抛错）
    el, fiber = _render(_Mixed, {"mode": "fullscreen"})
    assert isinstance(fiber.hooks[0], FullscreenHook)
    # ★ review 修复：复用同一 fiber 二次渲染前须 reset_hooks()（hook_index
    #   清零才真正复用 hooks[0] 而非追加新 hook）——修复前未复位，二次渲染
    #   _next_hook 从 idx=1 创建新节点，断言恒通过未覆盖「同 hook 位复用」。
    fiber.reset_hooks()
    el2, fiber2 = _render(_Mixed, {"mode": "modal"}, fiber)
    assert len(fiber2.hooks) == 1, "同 hook 位复用而非新增"
    assert isinstance(fiber2.hooks[0], FullscreenHook)
    assert fiber2.hooks[0].is_active is True


# ═══════════════════════════════════════════════════════════
# 2. 框架层：router 吞掉未消费事件（模态独占键盘——底部视图语义）
# ═══════════════════════════════════════════════════════════

def test_modal_router_swallows_unconsumed():
    """模态激活：全部 use_input 未消费的事件被吞掉（返回 True）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert router(KeyEvent(kind="backspace", raw=b"\x7f")) is True


def test_modal_router_passthrough_when_inactive():
    """模态未激活：未消费事件放行（False，走旧路径——零行为变化）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)
    fh = FullscreenHook(is_active=False)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is False
    assert router(KeyEvent(kind="enter", raw=b"\r")) is False


def test_modal_router_input_hook_still_priority():
    """模态激活时 use_input handler 仍优先消费（导航/确认/取消等按键）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: ev.kind == "enter", is_active=True)
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True  # handler 消费
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True  # 吞掉


# ═══════════════════════════════════════════════════════════
# 3. 模型层：model.bottom_view
# ═══════════════════════════════════════════════════════════

def test_model_bottom_view_default():
    """bottom_view 默认空（正常底部区）。"""
    m = AppModel()
    assert m.bottom_view == ""


def test_model_bottom_view_reset_display():
    """reset_display（Ctrl+L 清屏）同时退出模态底部视图。"""
    m = AppModel()
    m.bottom_view = "user_select"
    m.reset_display()
    assert m.bottom_view == ""


def test_model_bottom_view_independent_of_fullscreen():
    """bottom_view 与 fullscreen 互不污染（全屏优先，二者可独立读写）。"""
    m = AppModel()
    m.bottom_view = "user_select"
    assert m.fullscreen == ""
    m.fullscreen = "trace"
    assert m.bottom_view == "user_select"


# ═══════════════════════════════════════════════════════════
# 4. 应用层：App 底部视图注册表 + 渲染
# ═══════════════════════════════════════════════════════════

def test_app_bottom_views_registry_contains_user_select():
    """注册表含 user_select（组件 + key 工厂元组）→ UserSelectPopup。"""
    entry = BOTTOM_VIEWS["user_select"]
    assert isinstance(entry, tuple)
    view, key_fn = entry
    assert view is UserSelectPopup
    assert callable(key_fn)


def test_app_bottom_view_key_from_seq():
    """key 工厂：seq 不同 → key 不同（强制重挂载重置内部 state）。"""
    entry = BOTTOM_VIEWS["user_select"]
    _view, key_fn = entry
    m1 = _make_model_with_blocks()
    m1.user_select.seq = 1
    m2 = _make_model_with_blocks()
    m2.user_select.seq = 2
    assert key_fn(m1) != key_fn(m2)
    assert key_fn(m1) == "us-1"


def test_app_bottom_view_renders_only_popup():
    """bottom_view="user_select" → 底部区只渲染 UserSelectPopup——
    状态栏/输入区不显示（「弹窗打开时底部框不显示，弹窗在原来底部框位置
    独立显示」）。"""
    m = _make_model_with_blocks()
    _open_user_select(m)
    el, _ = _render(App, {"model": m, "width": 100})
    children = _bottom_children(el)
    # 底部区仅一个元素：UserSelectPopup
    assert len(children) == 1, f"底部区应只有弹窗: {[type(c).__name__ for c in children]}"
    assert children[0].type is UserSelectPopup
    # 状态栏/输入区不在树中
    types = [str(getattr(c, "type", "")) for c in children]
    assert all("StatusBar" not in str(t) and "InputArea" not in str(t) for t in types)


def test_app_bottom_view_empty_renders_normal():
    """bottom_view="" → 底部区渲染正常状态栏 + 输入区。"""
    m = _make_model_with_blocks()
    el, _ = _render(App, {"model": m, "width": 100})
    children = _bottom_children(el)
    assert len(children) == 2, f"底部区应为状态栏+输入区: {[type(c).__name__ for c in children]}"
    assert children[0].type is StatusBar
    assert children[1].type is InputArea


def test_app_bottom_view_unknown_id_falls_back():
    """未知底部视图 id → 防御回退正常底部区（不崩溃）。"""
    m = _make_model_with_blocks()
    m.bottom_view = "ghost"
    el, _ = _render(App, {"model": m, "width": 100})
    children = _bottom_children(el)
    assert len(children) == 2
    assert children[0].type is StatusBar
    assert children[1].type is InputArea


# ═══════════════════════════════════════════════════════════
# 5. 端到端：弹窗打开时输入不落入缓冲；关闭后恢复
# ═══════════════════════════════════════════════════════════

def test_e2e_user_select_open_input_not_into_buffer():
    """user_select 弹窗打开（模态底部视图）：字符/Enter 被 router 吞掉 →
    输入缓冲零变化；关闭后恢复正常输入（字符落入缓冲）。"""
    disp, be = _make_dispatcher()
    m = _make_model_with_blocks()
    _open_user_select(m)
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router is not None, "弹窗激活应构建 router（模态吞掉未消费事件）"
    disp.set_input_hook_router(router)
    # 字符 → 吞掉（不落入缓冲）
    disp._dispatch_key_event(KeyEvent(kind="char", char="a", raw=b"a"))
    assert be.get_current_text() == ""
    # Backspace → 吞掉（不删缓冲）
    disp._dispatch_key_event(KeyEvent(kind="backspace", raw=b"\x7f"))
    assert be.get_current_text() == ""
    # 关闭弹窗（清理 bottom_view）→ 正常底部区 → 字符落入缓冲
    m.bottom_view = ""
    m.user_select = UserSelectState()
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    router2 = rec._build_input_router(root)
    disp.set_input_hook_router(router2)
    disp._dispatch_key_event(KeyEvent(kind="char", char="b", raw=b"b"))
    assert be.get_current_text() == "b"


def test_e2e_user_select_open_no_input_fiber():
    """弹窗打开（输入区不渲染）→ find_input_fiber 返回 None（光标自动隐藏）。"""
    from src.tui.ink._cursor import find_input_fiber
    m = _make_model_with_blocks()
    _open_user_select(m)
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    assert find_input_fiber(root) is None


# ═══════════════════════════════════════════════════════════
# 6. 协议层：user_select 工具 / /editmsg / CommandUiAdapter
# ═══════════════════════════════════════════════════════════

def test_user_select_tool_activates_bottom_view(monkeypatch):
    """user_select 工具打开时设置 bottom_view="user_select"，清理后恢复 ""。"""
    import asyncio
    import os
    import sys
    from src.tools.user_select import UserSelectFunc

    m = AppModel()
    views_seen: list = []

    class _FakeStdin:
        """pytest 环境 stdin 非 tty：伪造 fileno 配合 os.isatty patch。"""

        def fileno(self):
            return 0

    class _FakeInput:
        def flush_stdin_buffer(self):
            pass

    class _FakeChatUI:
        @property
        def bottom_bar(self):
            return None

        def get_model(self):
            return m

        def request_bottom_redraw(self):
            views_seen.append(getattr(m, "bottom_view", None))

        def get_input_component(self):
            return _FakeInput()

    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: _FakeChatUI())
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    func = UserSelectFunc("测试", ["A", "B"], default_options=["A"])

    async def _run():
        task = asyncio.create_task(func._execute_terminal_async())
        await asyncio.sleep(0.15)
        m.user_select.done = True
        m.user_select.action = "confirmed"
        m.user_select.result = ["A"]
        return await task

    result = asyncio.run(_run())
    assert '"A"' in result
    assert "user_select" in views_seen, "打开时应激活底部视图"
    assert m.bottom_view == "", "清理后应恢复正常底部区"


def test_ui_adapter_sets_bottom_view(monkeypatch):
    """CommandUiAdapter.run_bottom_bar_selection 标准协议打开/清理 bottom_view。"""
    from src.core.commands._ui_adapter import CommandUiAdapter

    m = AppModel()
    session = _StubSession(model=m)

    class _FakeChatUI:
        def __init__(self, model, sess):
            self._model = model
            self._sess = sess

        def get_model(self):
            return self._model

        def request_bottom_redraw(self):
            self._sess.request_bottom_redraw()

    monkeypatch.setattr(
        CommandUiAdapter, "_get_active_chat_ui",
        staticmethod(lambda: _FakeChatUI(m, session)),
    )

    def _set_done():
        time.sleep(0.1)
        m.user_select.done = True
        m.user_select.action = "confirmed"
        m.user_select.selected = 0

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    adapter = CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(["A", "B"], ["A", "B"])
    assert result["action"] == "confirmed"
    assert "user_select" in session.views, "打开时应激活底部视图"
    assert m.bottom_view == "", "清理后应恢复正常底部区"


def test_message_editor_sets_bottom_view():
    """/editmsg 消息选择（MessageEditor._interactive_message_select）打开/清理
    bottom_view（与 user_select 工具同协议）。"""
    from src.tui.pipeline.message_editor import MessageEditor

    m = AppModel()
    session = _StubSession(model=m)

    class _FakeBottomBar:
        _model = m
        _session = session

    editor = MessageEditor(bottom_bar=_FakeBottomBar())

    def _set_done():
        time.sleep(0.1)
        m.user_select.done = True
        m.user_select.action = "confirmed"
        m.user_select.selected = 0

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    idx = editor._interactive_message_select(
        [(0, {"role": "user", "content": "hi"})], ["hi"],
    )
    assert idx == 0
    assert "user_select" in session.views, "打开时应激活底部视图"
    assert m.bottom_view == "", "清理后应恢复正常底部区"


def test_app_bottom_view_invisible_popup_renders_empty():
    """bottom_view="user_select" 但弹窗不可见（done/异常状态残留）→ 底部区
    渲染 UserSelectPopup 组件（visible=False 返回空 TEXT 零高度），不崩溃
    （防御：工具异常路径残留 bottom_view 时输入区消失但界面不崩溃）。"""
    m = _make_model_with_blocks()
    m.user_select = UserSelectState()  # visible=False（默认）
    m.bottom_view = "user_select"
    el, _ = _render(App, {"model": m, "width": 100})
    children = _bottom_children(el)
    assert len(children) == 1
    assert children[0].type is UserSelectPopup  # 组件仍挂载（渲染空 TEXT）


def test_app_fullscreen_priority_over_bottom_view():
    """fullscreen 优先于 bottom_view：二者同时设置 → App 整屏渲染全屏视图
    （底部视图不参与渲染）。"""
    from src.tui.app.trace_view import TraceView
    m = _make_model_with_blocks()
    _open_user_select(m)
    m.fullscreen = "trace"
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is TraceView


def test_modal_router_only_modal_hook():
    """仅 use_modal（无 use_input handler）→ 仍构建 router 并吞掉一切。"""
    rec = Reconciler()
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([fh], [])
    assert router is not None
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True


def test_popup_item_rows_floor_and_budget(monkeypatch):
    """_popup_item_rows 高度预算：底部视图模式 h-3（不再预留状态栏/输入区）；
    小终端保底 6（与补全弹窗同保底语义——超屏防护下限）。"""
    from src.tui.app.user_select import _popup_item_rows
    from src.tui._screen import TerminalWidthCache
    cache = TerminalWidthCache.get_default()
    monkeypatch.setattr(cache, "get_height", lambda: 24)
    assert _popup_item_rows() == 21  # 24-3：状态栏/输入区不渲染，预算放宽
    monkeypatch.setattr(cache, "get_height", lambda: 6)
    assert _popup_item_rows() == 6  # 保底下限
    monkeypatch.setattr(cache, "get_height", lambda: 4)
    assert _popup_item_rows() == 6  # 保底下限（负数钳制）


def test_ui_adapter_cleanup_on_bad_selected(monkeypatch):
    """ui_adapter 轮询结束 selected 为 None（异常数据）→ int() 归一化回退
    initial_idx 不崩溃；finally 清理 user_select + bottom_view（不泄漏底部
    视图——修复前 int(None) 抛 TypeError 跳过清理，App 持续只渲染弹窗、
    输入区消失）。"""
    import pytest
    from src.core.commands._ui_adapter import CommandUiAdapter

    m = AppModel()
    session = _StubSession(model=m)

    class _FakeChatUI:
        def __init__(self, model, sess):
            self._model = model
            self._sess = sess

        def get_model(self):
            return self._model

        def request_bottom_redraw(self):
            self._sess.request_bottom_redraw()

    monkeypatch.setattr(
        CommandUiAdapter, "_get_active_chat_ui",
        staticmethod(lambda: _FakeChatUI(m, session)),
    )

    def _set_done():
        time.sleep(0.1)
        m.user_select.done = True
        m.user_select.action = "confirmed"
        m.user_select.selected = None  # 异常数据：int(None) 触发归一化路径

    t = threading.Thread(target=_set_done, daemon=True)
    t.start()
    adapter = CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(["A", "B"], ["A", "B"])
    assert result["action"] == "confirmed"
    assert result["index"] == 0  # 归一化失败回退 initial_idx
    assert m.bottom_view == "", "finally 应清理底部视图（不泄漏）"
    assert m.user_select.visible is False


def test_message_editor_cleanup_on_poll_exception(monkeypatch):
    """/editmsg 轮询异常（time.sleep 抛错）→ finally 清理 user_select +
    bottom_view（不泄漏底部视图——修复前清理段顺序执行，异常直接跳出函数
    泄漏 bottom_view → App 持续只渲染弹窗、输入区消失）。"""
    import pytest
    from src.tui.pipeline.message_editor import MessageEditor

    m = AppModel()
    session = _StubSession(model=m)

    class _FakeBottomBar:
        _model = m
        _session = session

    editor = MessageEditor(bottom_bar=_FakeBottomBar())

    def _boom(*_a, **_k):
        raise RuntimeError("poll interrupted")

    monkeypatch.setattr("src.tui.pipeline.message_editor.time.sleep", _boom)
    with pytest.raises(RuntimeError):
        editor._interactive_message_select(
            [(0, {"role": "user", "content": "hi"})], ["hi"],
        )
    assert m.bottom_view == "", "finally 应清理底部视图（不泄漏）"
    assert m.user_select.visible is False


__all__ = [
    "_DemoModal",
    "_render",
    "_make_dispatcher",
    "_make_model_with_blocks",
    "_open_user_select",
    "_StubSession",
]
