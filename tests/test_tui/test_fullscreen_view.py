"""模态全屏视图通用机制测试 — use_fullscreen + FULLSCREEN_VIEWS + model.fullscreen。

2026-08-17（用户需求：轨迹 Trace 打开时别的界面不能接收键盘输入，要做成
通用的）：新增**通用模态全屏视图机制**——

  1. 框架层（ink）：``use_fullscreen`` hook——激活期间 input router 在全部
     use_input handler 未消费时**吞掉**事件（返回 True）→ InputDispatcher
     跳过旧路径（输入缓冲），实现「打开时独占键盘输入」（杜绝看不见的输入）。
  2. 应用层：``AppModel.fullscreen`` 状态 + ``app.FULLSCREEN_VIEWS`` 注册表
     ——App 按 id 整屏渲染对应组件；``trace_open`` 为兼容别名（property）。
  3. 装配层：``_make_fullscreen_toggle_cb`` 通用开关回调工厂（view_id 参数化，
     任意全屏视图可绑定快捷键复用）；``_make_trace_toggle_cb`` 委托其实现。
"""

from __future__ import annotations

from pathlib import Path

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent
from src.tui.app.app import FULLSCREEN_VIEWS, App
from src.tui.app.model import AppModel
from src.tui.app.trace_view import TraceView
from src.tui.ink import hooks
from src.tui.ink.element import h as h_el
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, FullscreenHook, InputHook
from src.tui.ink.reconciler import Reconciler

# ═══════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════

def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件（与 test_trace_view 同模式）。"""
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
    """构造带块数据的 AppModel（TraceView 台账渲染所需）。"""
    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> 你好")])
    m.append_committed("content", [AnsiLine.of("回答内容")])
    return m


class _StubSession:
    """最小 session 桩（request_bottom_redraw 记录调用）。"""

    def __init__(self):
        self.calls = []

    def request_bottom_redraw(self):
        self.calls.append("redraw")


# ═══════════════════════════════════════════════════════════
# 1. 框架层：use_fullscreen hook
# ═══════════════════════════════════════════════════════════

def _DemoFullscreen(props):
    """测试组件：声明模态全屏（is_active 由 props 控制）。"""
    from src.tui.ink import use_fullscreen
    use_fullscreen(props.get("active", True))
    return None


def test_use_fullscreen_registers_hook():
    """use_fullscreen 注册 FullscreenHook 且 is_active 正确。"""
    el, fiber = _render(_DemoFullscreen, {"active": True})
    assert el is None
    fh = next(h for h in fiber.hooks if isinstance(h, FullscreenHook))
    assert fh.is_active is True


def test_use_fullscreen_inactive():
    """use_fullscreen(False) → is_active=False（不参与路由吞掉）。"""
    el, fiber = _render(_DemoFullscreen, {"active": False})
    fh = next(h for h in fiber.hooks if isinstance(h, FullscreenHook))
    assert fh.is_active is False


# ═══════════════════════════════════════════════════════════
# 2. 框架层：router 吞掉未消费事件（模态独占键盘）
# ═══════════════════════════════════════════════════════════

def test_fullscreen_router_swallows_unconsumed():
    """全屏激活：全部 use_input 未消费的事件被吞掉（返回 True）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert router(KeyEvent(kind="backspace", raw=b"\x7f")) is True


def test_fullscreen_router_passthrough_when_inactive():
    """全屏未激活：未消费事件放行（False，走旧路径——零行为变化）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)
    fh = FullscreenHook(is_active=False)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is False
    assert router(KeyEvent(kind="enter", raw=b"\r")) is False


def test_fullscreen_router_input_hook_still_priority():
    """全屏激活时 use_input handler 仍优先消费（导航/关闭等）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: ev.kind == "escape", is_active=True)
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router(KeyEvent(kind="escape", raw=b"\x1b")) is True  # handler 消费
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True  # 吞掉


def test_fullscreen_router_cache_rebuild_on_active_change():
    """全屏激活状态变化 → router 签名变化 → 重建（不复用过期闭包）。"""
    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)
    fh = FullscreenHook(is_active=False)
    router1 = rec._build_input_router_from_hooks([ih, fh], [])
    assert router1(KeyEvent(kind="char", char="x", raw=b"x")) is False
    fh.is_active = True  # 状态变化（打开全屏）
    router2 = rec._build_input_router_from_hooks([ih, fh], [])
    assert router2 is not router1
    assert router2(KeyEvent(kind="char", char="x", raw=b"x")) is True


def test_fullscreen_router_empty_hooks_none():
    """无任何 hook（含 fullscreen）→ router None（旧路径，零行为变化）。"""
    rec = Reconciler()
    assert rec._build_input_router_from_hooks([], []) is None


def test_fullscreen_router_only_fullscreen_hook():
    """仅 FullscreenHook（无 use_input handler）→ 仍构建 router 并吞掉一切。"""
    rec = Reconciler()
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([fh], [])
    assert router is not None
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True


# ═══════════════════════════════════════════════════════════
# 3. 应用层：model.fullscreen ↔ trace_open 兼容别名
# ═══════════════════════════════════════════════════════════

def test_model_fullscreen_trace_open_alias():
    """trace_open 为 fullscreen=="trace" 的兼容别名（双向读写一致）。"""
    m = AppModel()
    assert m.fullscreen == ""
    assert m.trace_open is False
    m.trace_open = True
    assert m.fullscreen == "trace"
    assert m.trace_open is True
    m.trace_open = False
    assert m.fullscreen == ""
    assert m.trace_open is False


def test_model_fullscreen_other_view_not_trace():
    """fullscreen 为其他视图 id 时 trace_open 为 False（互不污染）。"""
    m = AppModel()
    m.fullscreen = "help"
    assert m.trace_open is False
    m.fullscreen = ""
    assert m.trace_open is False


# ═══════════════════════════════════════════════════════════
# 4. 应用层：App 全屏视图注册表
# ═══════════════════════════════════════════════════════════

def test_app_fullscreen_registry_contains_trace():
    """注册表含 trace → TraceView 组件（未来全屏视图在注册表加条目）。"""
    assert FULLSCREEN_VIEWS["trace"] is TraceView


def test_app_fullscreen_renders_trace_view():
    """fullscreen="trace" → App 整屏渲染 TraceView（其他 TUI 不渲染）。"""
    m = _make_model_with_blocks()
    m.fullscreen = "trace"
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is TraceView


def test_app_fullscreen_empty_renders_normal_tree():
    """fullscreen="" → App 渲染正常界面（根为 APP 容器，非 TraceView）。"""
    m = _make_model_with_blocks()
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is not TraceView
    from src.tui.ink import APP
    assert el.type is APP


def test_app_fullscreen_unknown_id_falls_back():
    """未知全屏视图 id → 防御回退正常界面（不崩溃）。"""
    m = _make_model_with_blocks()
    m.fullscreen = "ghost"
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is not TraceView


# ═══════════════════════════════════════════════════════════
# 5. 装配层：通用开关回调工厂
# ═══════════════════════════════════════════════════════════

def test_fullscreen_toggle_cb_generic():
    """_make_fullscreen_toggle_cb：翻转到指定 view_id / 关闭 + 请求重绘。"""
    from src.tui._assembly_steps import _make_fullscreen_toggle_cb
    m = AppModel()
    session = _StubSession()
    cb = _make_fullscreen_toggle_cb(m, session, "trace")
    cb()
    assert m.fullscreen == "trace"
    assert session.calls == ["redraw"]
    cb()
    assert m.fullscreen == ""
    # 其他 view_id（未来全屏视图复用同一工厂）
    cb2 = _make_fullscreen_toggle_cb(m, session, "help")
    cb2()
    assert m.fullscreen == "help"
    cb2()
    assert m.fullscreen == ""


def test_trace_toggle_cb_delegates_generic():
    """_make_trace_toggle_cb 委托通用工厂（view_id="trace"，兼容入口）。"""
    from src.tui._assembly_steps import _make_trace_toggle_cb
    m = AppModel()
    session = _StubSession()
    cb = _make_trace_toggle_cb(m, session)
    cb()
    assert m.fullscreen == "trace"
    assert m.trace_open is True
    cb()
    assert m.fullscreen == ""
    assert m.trace_open is False


def test_user_select_popup_consumes_ctrl_h():
    """user_select 弹窗可见时 Ctrl+H 被弹窗消费（不打开 Trace）——弹窗模态
    优先于全局全屏 toggle（防回归：SelectInput consumeAll 语义变化会意外
    打断用户选择流程）。

    ★ 模态底部视图（2026-08-17）：弹窗不再作为底部区常规成员——激活须
    ``model.bottom_view = "user_select"``（App 底部区只渲染弹窗，状态栏/
    输入区不显示）。"""
    from src.tui.app.model import UserSelectState
    m = _make_model_with_blocks()
    us = UserSelectState()
    us.visible = True
    us.options = ["选项A", "选项B"]
    us.title = "测试选择"
    m.user_select = us
    m.bottom_view = "user_select"  # 模态底部视图激活（弹窗独立界面）
    m.fullscreen = ""
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="ctrl_key", char="\x08", raw=b"\x08")) is True
    assert m.fullscreen == "", "弹窗消费 Ctrl+H，不应打开 Trace"


def test_e2e_user_select_popup_consumes_ctrl_h_via_dispatcher():
    """端到端（review 方向 P3-10 补强）：弹窗可见时 dispatcher 收到 Ctrl+H
    字节 → router 消费（弹窗）→ 跳过 toggle 回调（不打开 Trace）——
    验证 InputDispatcher ↔ router 接线后的完整路径。"""
    from src.tui._assembly_steps import _make_trace_toggle_cb
    from src.tui.app.model import UserSelectState
    disp, be = _make_dispatcher()
    m = _make_model_with_blocks()
    us = UserSelectState()
    us.visible = True
    us.options = ["选项A", "选项B"]
    us.title = "测试选择"
    m.user_select = us
    m.bottom_view = "user_select"  # 模态底部视图激活（弹窗独立界面）
    # 注入 toggle 回调（若被调用会打开 Trace）
    disp.set_trace_toggle_callback(_make_trace_toggle_cb(m, _StubSession()))
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    disp.set_input_hook_router(router)
    # Ctrl+H 字节（0x08）→ 弹窗消费 → toggle 不触发
    assert disp._dispatch_byte(0x08) is True
    assert m.fullscreen == "", "弹窗消费 Ctrl+H，toggle 不应打开 Trace"


# ═══════════════════════════════════════════════════════════
# 6. 端到端：Trace 打开时输入不落入缓冲；关闭后恢复
# ═══════════════════════════════════════════════════════════

def test_e2e_trace_open_input_not_into_buffer():
    """Trace 打开（模态）：字符/Enter 被 router 吞掉 → 输入缓冲零变化；
    关闭后恢复正常输入（字符落入缓冲）。"""
    disp, be = _make_dispatcher()
    m = _make_model_with_blocks()
    m.trace_open = True
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router is not None
    disp.set_input_hook_router(router)
    # 字符 → 吞掉（不落入缓冲）
    disp._dispatch_key_event(KeyEvent(kind="char", char="a", raw=b"a"))
    assert be.get_current_text() == ""
    # Enter → 吞掉（不提交）
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\r"))
    assert be.get_current_text() == ""
    # Backspace → 吞掉（不删缓冲）
    disp._dispatch_key_event(KeyEvent(kind="backspace", raw=b"\x7f"))
    assert be.get_current_text() == ""
    # 关闭 trace → 正常界面 → 字符落入缓冲
    m.trace_open = False
    rec.render(root, h_el(App, {"model": m, "width": 100}), 100, 24)
    router2 = rec._build_input_router(root)
    disp.set_input_hook_router(router2)
    disp._dispatch_key_event(KeyEvent(kind="char", char="b", raw=b"b"))
    assert be.get_current_text() == "b"
