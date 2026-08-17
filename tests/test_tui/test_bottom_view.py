"""模态底部视图通用机制测试 — BOTTOM_VIEWS + model.bottom_view + App 独占底部区。

2026-08-17（用户需求：user_select 独立出底部框且是独立界面——显示时底部框
不能显示、user_select 在原来的底部框位置显示，做成通用化）：新增**通用模态
底部视图机制**——与模态全屏视图（``FULLSCREEN_VIEWS`` + ``model.fullscreen``）
对称：

  1. 应用层：``AppModel.bottom_view`` 状态 + ``app.BOTTOM_VIEWS`` 注册表——
     App 按 id **独占渲染底部区**（状态栏 + 输入区不显示——「底部框不显示」），
     视图渲染在**原底部框位置**（屏幕底部区域）；消息区保持正常显示。
  2. user_select 弹窗（UserSelectPopup）从底部框内嵌组件（原与状态栏/输入区
     同层渲染）独立为该机制第一个底部视图——显示时底部框隐藏、独占键盘
     （组件经 ``use_fullscreen`` 声明模态，未消费按键不落入输入缓冲）。
  3. 调用方联动：user_select 工具 / message_editor / ui_adapter 设置弹窗状态
     （``model.user_select`` visible）时同步 ``model.bottom_view = "user_select"``，
     清理时恢复 ``""``（App 恢复状态栏 + 输入区正常底部框）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent
from src.tui.app.app import BOTTOM_VIEWS, App
from src.tui.app.model import AppModel, UserSelectState
from src.tui.app.status_bar import StatusBar
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import APP, h
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, FullscreenHook
from src.tui.ink.reconciler import Reconciler


def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件（与 test_fullscreen_view 同模式）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def _model_with_user_select(visible: bool = True, seq: int = 1) -> AppModel:
    """构造带 user_select 状态（可见/选项）的 AppModel。"""
    m = AppModel()
    if visible:
        m.user_select = UserSelectState(
            visible=True, seq=seq, title="测试选择",
            options=["A", "B"], default_options=["A"],
        )
    return m


# ═══════════════════════════════════════════════════════════
# 1. 应用层：model.bottom_view 状态 + 通用 API
# ═══════════════════════════════════════════════════════════

def test_model_bottom_view_default_empty():
    """bottom_view 默认 ""（正常底部框：状态栏+输入区）。"""
    m = AppModel()
    assert m.bottom_view == ""


def test_model_open_close_bottom_view():
    """open_bottom_view(view_id) / close_bottom_view() 通用开关。"""
    m = AppModel()
    m.open_bottom_view("user_select")
    assert m.bottom_view == "user_select"
    m.open_bottom_view("other_view")
    assert m.bottom_view == "other_view"
    m.close_bottom_view()
    assert m.bottom_view == ""
    # 空 id 等价关闭（防御）
    m.open_bottom_view("")
    assert m.bottom_view == ""


def test_model_reset_display_clears_bottom_view():
    """reset_display（Ctrl+L 清屏）同步退出模态底部视图（与 fullscreen 同语义）。"""
    m = _model_with_user_select()
    m.bottom_view = "user_select"
    m.reset_display()
    assert m.bottom_view == ""
    assert m.fullscreen == ""


# ═══════════════════════════════════════════════════════════
# 2. 注册表：BOTTOM_VIEWS
# ═══════════════════════════════════════════════════════════

def test_bottom_views_registry_contains_user_select():
    """注册表含 user_select → (UserSelectPopup, key 生成函数)。"""
    assert "user_select" in BOTTOM_VIEWS
    view, key_fn = BOTTOM_VIEWS["user_select"]
    assert view is UserSelectPopup
    assert callable(key_fn)


def test_bottom_views_user_select_key_fn():
    """user_select key 生成函数：``us-{seq}``（seq 变化强制重挂载）。"""
    _, key_fn = BOTTOM_VIEWS["user_select"]
    m = _model_with_user_select(seq=2)
    assert key_fn(m) == "us-2"
    m.user_select.seq = 5
    assert key_fn(m) == "us-5"
    # model 无 user_select（异常/桩）防御
    assert key_fn(SimpleNamespace()) == "us-0"


# ═══════════════════════════════════════════════════════════
# 3. App 渲染分支：独占底部区（底部框隐藏）↔ 正常底部框
# ═══════════════════════════════════════════════════════════

def test_app_bottom_view_renders_user_select_exclusive():
    """bottom_view="user_select" → 底部区独占渲染 UserSelectPopup。

    StatusBar / InputArea（底部框）不渲染——「显示时底部框不显示」；
    视图渲染在**原底部框位置**（App 根第二个子元素）。
    """
    m = _model_with_user_select()
    m.bottom_view = "user_select"
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is APP
    assert len(el.children) == 2, "消息区 + 底部视图"
    # 底部区 = UserSelectPopup（独占；无 StatusBar/InputArea）
    bottom = el.children[1]
    assert bottom.type is UserSelectPopup
    # key=seq 传递（每次打开强制重挂载）
    assert bottom.props.get("key") == "us-1"
    # 消息区保持正常（第一个子元素为 Column 容器）
    assert el.children[0].type is not UserSelectPopup


def test_app_bottom_view_normal_renders_status_and_input():
    """bottom_view="" 且 user_select 不可见 → 正常底部框：StatusBar + InputArea
    （无 UserSelectPopup 直接子元素——弹窗已独立为底部视图，不在底部框内）。"""
    from src.tui.app.input_area import InputArea
    m = _model_with_user_select(visible=False)
    m.bottom_view = ""
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is APP
    assert len(el.children) == 2
    bottom = el.children[1]
    assert bottom.type is not UserSelectPopup, "底部视图未激活时弹窗不在底部区"
    types = [c.type for c in bottom.children]
    assert StatusBar in types, f"状态栏应渲染: {types}"
    assert InputArea in types, f"输入区应渲染: {types}"
    assert UserSelectPopup not in types, "user_select 弹窗已独立出底部框（不在底部框内）"


def test_app_bottom_view_fallback_user_select_visible():
    """兼容回退：仅 user_select.visible（旧调用方未写 bottom_view）→ 独占底部区。"""
    m = _model_with_user_select()
    m.bottom_view = ""
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.children[1].type is UserSelectPopup, "兼容回退应独占渲染弹窗"


def test_app_bottom_view_user_select_not_visible_normal():
    """user_select 不可见（visible=False）且 bottom_view="" → 正常底部框。"""
    m = _model_with_user_select(visible=False)
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.children[1].type is not UserSelectPopup


def test_app_bottom_view_user_select_done_not_visible():
    """user_select done（交互结束未清理间隙）→ 不独占（兼容判定含 done）。"""
    m = _model_with_user_select()
    m.user_select.done = True
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.children[1].type is not UserSelectPopup


def test_app_bottom_view_unknown_id_falls_back():
    """未知底部视图 id → 防御回退正常底部框（不崩溃）。"""
    m = _model_with_user_select()
    m.bottom_view = "ghost"
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.type is APP
    assert el.children[1].type is not UserSelectPopup


# ═══════════════════════════════════════════════════════════
# 4. UserSelectPopup 模态声明（use_fullscreen）
# ═══════════════════════════════════════════════════════════

def test_user_select_popup_registers_fullscreen_hook_active():
    """visible=True 渲染 → FullscreenHook is_active=True（模态独占键盘）。"""
    m = _model_with_user_select()
    el, fiber = _render(UserSelectPopup, {"model": m, "width": 80})
    assert el is not None
    fh = next(h_ for h_ in fiber.hooks if isinstance(h_, FullscreenHook))
    assert fh.is_active is True


def test_user_select_popup_registers_fullscreen_hook_inactive():
    """visible=False 渲染 → FullscreenHook is_active=False（零影响）。"""
    m = _model_with_user_select(visible=False)
    _, fiber = _render(UserSelectPopup, {"model": m, "width": 80})
    fh = next(h_ for h_ in fiber.hooks if isinstance(h_, FullscreenHook))
    assert fh.is_active is False


# ═══════════════════════════════════════════════════════════
# 5. 输入路由：底部视图激活时未消费按键被吞掉（不落入输入缓冲）
# ═══════════════════════════════════════════════════════════

def test_bottom_view_router_swallows_unconsumed():
    """底部视图激活：按键全部被 router 消费（控件 consumeAll 优先消费导航/
    确认/取消；未消费事件由 FullscreenHook 模态兜底吞掉）——不落入输入缓冲。"""
    m = _model_with_user_select()
    m.bottom_view = "user_select"
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h(App, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router is not None
    # F5（控件 consumeAll 消费——弹窗模式阻断一切非导航键）
    assert router(KeyEvent(kind="f5", raw=b"\x1b[15~")) is True
    # 导航/确认/取消仍由控件消费
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert router(KeyEvent(kind="escape", raw=b"\x1b")) is True
    assert router(KeyEvent(kind="char", char="j", raw=b"j")) is True


def test_bottom_view_normal_no_swallow():
    """正常底部框（无底部视图）→ 无模态吞掉（字符走旧路径）。"""
    m = _model_with_user_select(visible=False)
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h(App, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router is None or router(KeyEvent(kind="char", char="x", raw=b"x")) is False


# ═══════════════════════════════════════════════════════════
# 6. 调用方联动：设置/清理 bottom_view
# ═══════════════════════════════════════════════════════════

class _ChatUIStub:
    """ChatUIConsumer 桩：request_bottom_redraw 时立即完成 user_select 交互。

    auto_complete=True（默认）：首次重绘即置 done（confirmed）——主路径测试；
    auto_complete=False：不置 done——配合小 timeout 触发工具/editor 超时分支
    （真实超时路径，验证 finally 清理）。
    """

    def __init__(self, model, auto_complete: bool = True):
        self._model = model
        self.auto_complete = auto_complete
        self.redraws = []          # request_bottom_redraw 时的 bottom_view 快照
        self.flushes = 0
        self.bottom_bar = self._BottomBarStub()

    class _BottomBarStub:
        is_completion_visible = False
        _last_text = ""

        def hide_completions(self):
            pass

    def get_model(self):
        return self._model

    def request_bottom_redraw(self):
        self.redraws.append(self._model.bottom_view)
        us = self._model.user_select
        if self.auto_complete and us.visible and not us.done:
            us.done = True
            us.action = "confirmed"
            us.result = ["A"]

    def get_input_component(self):
        return self

    def flush_stdin_buffer(self):
        self.flushes += 1


def test_user_select_tool_sets_and_clears_bottom_view(monkeypatch):
    """user_select 工具：打开时 bottom_view="user_select"（底部框隐藏），
    交互完成后恢复 ""（底部框恢复）。"""
    from src.tools.user_select import UserSelectFunc

    model = AppModel()
    chat_ui = _ChatUIStub(model)
    # xdist worker 下 stdin 非 tty（fileno 可能异常）——mock stdin 确保
    # 走终端交互路径（os.isatty 返回 True）
    monkeypatch.setattr(
        "src.tools.user_select.sys.stdin", SimpleNamespace(fileno=lambda: 0),
    )
    monkeypatch.setattr("src.tools.user_select.os.isatty", lambda fd: True)
    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: chat_ui)

    tool = UserSelectFunc("选择", ["A", "B"], default_options=["A"])
    result = asyncio.run(tool.execute())

    data = json.loads(result)
    assert data["action"] == "confirmed"
    assert data["selected"] == ["A"]
    # 打开时（第一次重绘）bottom_view 已置位 → App 独占底部区渲染弹窗
    assert chat_ui.redraws and chat_ui.redraws[0] == "user_select"
    # 清理后：弹窗状态复位 + bottom_view 恢复 ""（底部框恢复显示）
    assert model.user_select.visible is False
    assert model.bottom_view == ""
    assert chat_ui.flushes >= 1, "stdin 残留应被 flush"


def test_user_select_tool_timeout_clears_bottom_view(monkeypatch):
    """user_select 工具真实超时路径：timeout 到达 → 超时回退 default_options，
    且 finally 仍清理 bottom_view（恢复底部框）。"""
    from src.tools.user_select import UserSelectFunc

    model = AppModel()
    # auto_complete=False：不置 done → 轮询走超时分支（deadline 极小）
    chat_ui = _ChatUIStub(model, auto_complete=False)
    monkeypatch.setattr(
        "src.tools.user_select.sys.stdin", SimpleNamespace(fileno=lambda: 0),
    )
    monkeypatch.setattr("src.tools.user_select.os.isatty", lambda fd: True)
    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: chat_ui)

    tool = UserSelectFunc("选择", ["A", "B"], default_options=["A"], timeout=0.05)
    result = asyncio.run(tool.execute())
    data = json.loads(result)
    assert data["action"] == "timeout", f"应走超时分支: {data}"
    assert data["selected"] == ["A"], "超时回退 default_options"
    # 打开时 bottom_view 已置位
    assert chat_ui.redraws and chat_ui.redraws[0] == "user_select"
    # finally 清理：弹窗复位 + bottom_view 恢复 ""
    assert model.user_select.visible is False
    assert model.bottom_view == ""


def test_message_editor_interactive_select_sets_and_clears_bottom_view():
    """/editmsg 消息选择（UserSelectPopup 协议）：设置/清理 bottom_view 联动。"""
    from src.tui.pipeline.message_editor import MessageEditor

    model = AppModel()
    session = _ChatUIStub(model)
    bb = SimpleNamespace(_model=model, _session=session)
    editor = MessageEditor(bottom_bar=bb)

    sel = editor._interactive_message_select(
        [(0, {"role": "user", "content": "hi"}), (1, {"role": "user", "content": "yo"})],
        ["hi", "yo"],
    )
    assert sel == 1, "默认选中最后一条（selected=len-1=1）"
    # 打开时 bottom_view 已置位
    assert session.redraws and session.redraws[0] == "user_select"
    # 清理后恢复
    assert model.user_select.visible is False
    assert model.bottom_view == ""


def test_ui_adapter_run_bottom_bar_selection_sets_and_clears_bottom_view(monkeypatch):
    """CommandUiAdapter.run_bottom_bar_selection（标准协议路径）：联动 bottom_view。"""
    from src.core.commands._ui_adapter import CommandUiAdapter

    model = AppModel()
    chat_ui = _ChatUIStub(model)
    monkeypatch.setattr(
        "src.core.commands._ui_adapter.CommandUiAdapter._get_active_chat_ui",
        lambda self: chat_ui,
    )
    adapter = CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(["A", "B"], ["A", "B"], initial_idx=0)
    assert result["action"] == "confirmed"
    assert chat_ui.redraws and chat_ui.redraws[0] == "user_select"
    assert model.bottom_view == ""
    assert model.user_select.visible is False


def test_ui_adapter_run_bottom_bar_selection_legacy_no_bottom_view():
    """run_bottom_bar_selection 无 ChatUI（legacy 路径）→ 不触碰 bottom_view。"""
    from src.core.commands._ui_adapter import CommandUiAdapter

    adapter = CommandUiAdapter()
    # 无 chat_ui、无 bottom_bar → error 路径（不抛异常）
    result = adapter.run_bottom_bar_selection(["A"], ["A"], bottom_bar=None)
    assert result["action"] == "error"


# ═══════════════════════════════════════════════════════════
# 7. 异常路径 finally 清理（review 修复：防模态卡死）
# ═══════════════════════════════════════════════════════════

def test_ui_adapter_finally_cleans_bottom_view_on_exception(monkeypatch):
    """run_bottom_bar_selection 轮询段被中断（KeyboardInterrupt）→ finally 仍
    清理弹窗 + bottom_view（防 bottom_view 残留 → App 永久独占底部区 + 模态
    吞输入卡死）。KeyboardInterrupt 为 BaseException（不被 except Exception
    吞掉），轮询 time.sleep 处抛出后 finally 兜底。"""
    from src.core.commands._ui_adapter import CommandUiAdapter

    model = AppModel()
    chat_ui = _ChatUIStub(model, auto_complete=False)  # 不置 done → 进入轮询
    monkeypatch.setattr(
        "src.core.commands._ui_adapter.CommandUiAdapter._get_active_chat_ui",
        lambda self: chat_ui,
    )
    # 打开时重绘正常（不置 done）；轮询 time.sleep 抛 KeyboardInterrupt
    def _boom_sleep(_s):
        raise KeyboardInterrupt()

    monkeypatch.setattr("src.core.commands._ui_adapter.time.sleep", _boom_sleep)
    adapter = CommandUiAdapter()
    with pytest.raises(KeyboardInterrupt):
        adapter.run_bottom_bar_selection(["A", "B"], ["A", "B"])
    assert model.bottom_view == "", "finally 应清理 bottom_view（恢复底部框）"
    assert model.user_select.visible is False, "finally 应复位弹窗状态"


def test_message_editor_finally_cleans_bottom_view_on_exception(monkeypatch):
    """/editmsg 选择轮询段被中断（KeyboardInterrupt）→ finally 清理 bottom_view
    （防模态卡死）。"""
    from src.tui.pipeline.message_editor import MessageEditor

    model = AppModel()
    session = _ChatUIStub(model, auto_complete=False)  # 不置 done → 进入轮询
    bb = SimpleNamespace(_model=model, _session=session)
    editor = MessageEditor(bottom_bar=bb)
    # 打开时重绘正常；轮询 time.sleep 抛 KeyboardInterrupt（BaseException
    # 不被 except Exception 吞掉 → 传播 → finally 兜底清理）
    def _boom_sleep(_s):
        raise KeyboardInterrupt()

    monkeypatch.setattr("src.tui.pipeline.message_editor.time.sleep", _boom_sleep)
    with pytest.raises(KeyboardInterrupt):
        editor._interactive_message_select(
            [(0, {"role": "user", "content": "hi"})], ["hi"],
        )
    assert model.bottom_view == "", "finally 应清理 bottom_view（恢复底部框）"
    assert model.user_select.visible is False, "finally 应复位弹窗状态"


def test_message_editor_timeout_cleans_bottom_view():
    """/editmsg 选择超时（无交互完成，_selection_ready 信号退出轮询）→ 返回
    None + finally 清理 bottom_view。"""
    from src.tui.pipeline.message_editor import MessageEditor

    model = AppModel()
    session = _ChatUIStub(model, auto_complete=False)
    bb = SimpleNamespace(_model=model, _session=session)
    editor = MessageEditor(bottom_bar=bb)
    # P2-7 双信号：_selection_ready 置位 → 轮询立即退出（无需等 120s deadline）
    editor._selection_ready.set()
    sel = editor._interactive_message_select(
        [(0, {"role": "user", "content": "hi"})], ["hi"],
    )
    assert sel is None, "无确认 → None"
    assert model.bottom_view == "", "finally 应清理 bottom_view"
    assert model.user_select.visible is False


# ═══════════════════════════════════════════════════════════
# 8. FullscreenHook 模态兜底（review 补强：SelectInput 未消费的键被吞掉）
# ═══════════════════════════════════════════════════════════

def test_bottom_view_fullscreen_hook_swallows_unconsumed_direct():
    """FullscreenHook 直接断言：use_input 未消费的事件被模态吞掉（返回 True）——
    与 use_fullscreen 模态声明语义一致（底部视图不可见输入不落入缓冲）。"""
    from src.tui.ink.fiber import InputHook, FullscreenHook

    rec = Reconciler()
    ih = InputHook(handler=lambda ev: False, is_active=True)  # 未消费任何键
    fh = FullscreenHook(is_active=True)
    router = rec._build_input_router_from_hooks([ih, fh], [])
    assert router is not None
    assert router(KeyEvent(kind="char", char="x", raw=b"x")) is True
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert router(KeyEvent(kind="backspace", raw=b"\x7f")) is True
    # 未激活 → 放行（False，走旧路径——零行为变化）
    fh.is_active = False
    router2 = rec._build_input_router_from_hooks([ih, fh], [])
    assert router2 is not None
    assert router2(KeyEvent(kind="char", char="x", raw=b"x")) is False


# ═══════════════════════════════════════════════════════════
# 9. 兼容回退边界：options 空 / reset_display 重激活
# ═══════════════════════════════════════════════════════════

def test_app_bottom_view_fallback_empty_options_auto_done():
    """兼容回退 + options 空：App 渲染期与 UserSelectPopup auto-done 同语义
    置 done（防 deadline=0 调用方轮询 us.done 永久挂起），不激活底部视图。"""
    m = _model_with_user_select()
    m.user_select.options = []
    m.user_select.default_options = ["def"]
    el, _ = _render(App, {"model": m, "width": 100})
    assert m.user_select.done is True, "options 空应自动置 done"
    assert m.user_select.action == "confirmed"
    assert m.user_select.result == ["def"]
    # done 已置位 → 不激活底部视图（正常底部框）
    assert el.children[1].type is not UserSelectPopup


def test_app_bottom_view_fallback_empty_options_done_preserved():
    """兼容回退 + options 空 + done 已置位（first-write-wins）：不覆盖已有结果。"""
    m = _model_with_user_select()
    m.user_select.options = []
    m.user_select.done = True
    m.user_select.action = "timeout"
    m.user_select.result = ["timeout-result"]
    _, _ = _render(App, {"model": m, "width": 100})
    assert m.user_select.action == "timeout", "done 已置位不覆盖"
    assert m.user_select.result == ["timeout-result"]


def test_reset_display_keeps_user_select_retriggers_bottom_view():
    """reset_display 保留 user_select 弹窗状态（工具协程轮询依赖）——兼容回退
    继续以底部视图显示弹窗（清屏不打断用户选择流程）。"""
    m = _model_with_user_select()
    m.bottom_view = "user_select"
    m.reset_display()
    # reset 后 bottom_view=""（防残留 id），user_select 仍激活
    assert m.bottom_view == ""
    assert m.user_select.visible is True
    # App 渲染：兼容回退重新激活底部视图（弹窗继续显示）
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.children[1].type is UserSelectPopup


def test_reset_display_cleared_user_select_normal_bottom():
    """reset_display 后弹窗已清理（visible=False）→ 正常底部框。"""
    m = _model_with_user_select(visible=False)
    m.bottom_view = "user_select"
    m.reset_display()
    el, _ = _render(App, {"model": m, "width": 100})
    assert el.children[1].type is not UserSelectPopup
