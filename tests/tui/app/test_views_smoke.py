"""全屏视图行为烟雾测试 — 打开/关闭/帮助/搜索输入的标准键位。

这些用例守护的是**视图交互契约**（不是像素级外观）：每个视图都必须响应
「Esc 关闭」「? 帮助」「/ 搜索」这一组标准键位。此前 16 个视图零测试覆盖，
任何一次键位改动都只能靠手工按 F11 发现回归。

用真实的调和器（``Reconciler`` + 输入 router）而不是直接调用 ``_handle``，
因为要覆盖 ``use_input`` 的注册/分发链路——视图把 handler 注册错了（比如
把 ``visible`` 传成常量），直接调函数是发现不了的。
"""

from __future__ import annotations

from typing import Any, Callable

import pytest

from src.tui.app import _state_types as state_types
from src.tui.app.view_registry import fullscreen_views
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler

VIEWS = fullscreen_views()


class _Event:
    """最小按键事件（字段与框架 KeyEvent 对齐）。"""

    def __init__(self, kind: str, char: str = "") -> None:
        self.kind = kind
        self.char = char
        self.modifier = 0
        self.kitty_bits = -1
        self.event_type = ""


def _mount(component, model: Any, width: int = 100) -> Callable[..., bool]:
    """挂载视图并返回 ``press(kind, char)``（每次按键后重绘）。"""
    reconciler = Reconciler(None)
    routers: list = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()

    def redraw() -> None:
        reconciler.render(root, h(component, {"model": model, "width": width}), width, 30)

    def press(kind: str, char: str = "") -> bool:
        # router 为 None = 当前没有任何 active 输入 hook（不可见视图即如此）：
        # 视为「事件未被视图消费」，与 InputDispatcher 旧路径语义一致。
        router = routers[-1] if routers else None
        consumed = bool(router(_Event(kind, char))) if router is not None else False
        redraw()
        return consumed

    redraw()
    return press


def _model(permissive_model, attr: str, state, *, visible: bool = True):
    model = permissive_model(width=100)
    state.visible = visible
    setattr(model, attr, state)
    model.fullscreen = attr.replace("_view", "") if visible else ""
    return model


def _sessions_state():
    state = state_types.SessionsViewState()
    state.entries = [
        {"id": "s1", "title": "会话一", "model": "deepseek", "saved_at": "10-01", "message_count": 3},
        {"id": "s2", "title": "会话二", "model": "deepseek", "saved_at": "10-02", "message_count": 5},
        {"id": "s3", "title": "会话三", "model": "deepseek", "saved_at": "10-03", "message_count": 1},
    ]
    return state


def test_sessions_view_help_toggle_and_escape(permissive_model):
    """sessions：``?`` 切换帮助面板，``Esc`` 关闭并写终态。"""
    state = _sessions_state()
    model = _model(permissive_model, "sessions_view", state)
    press = _mount(VIEWS["sessions"], model)

    assert press("char", "?") is True
    assert state.help_open is True
    assert press("char", "?") is True
    assert state.help_open is False

    assert press("escape") is True
    assert state.done is True
    assert state.action == "cancel"


def test_sessions_view_search_mode_entry(permissive_model):
    """sessions：``/`` 进入搜索输入模式，Esc 取消搜索（不关闭视图）。"""
    state = _sessions_state()
    model = _model(permissive_model, "sessions_view", state)
    press = _mount(VIEWS["sessions"], model)

    assert press("char", "/") is True
    assert state.search_mode is True
    assert press("escape") is True
    assert state.search_mode is False
    assert state.done is False


def test_mcp_view_escape_closes(permissive_model):
    """mcp：Esc 关闭并写终态。"""
    state = state_types.McpViewState()
    model = _model(permissive_model, "mcp_view", state)
    press = _mount(VIEWS["mcp"], model)
    assert press("escape") is True
    assert state.done is True


def test_usage_view_escape_closes(permissive_model):
    """usage：Esc 关闭并写终态。"""
    state = state_types.UsageViewState()
    model = _model(permissive_model, "usage_view", state)
    press = _mount(VIEWS["usage"], model)
    assert press("escape") is True
    assert state.done is True


def test_closed_view_does_not_consume_input(permissive_model):
    """不可见视图不得吞键（否则输入区会「看不见地丢字」）。"""
    state = state_types.McpViewState()
    model = _model(permissive_model, "mcp_view", state, visible=False)
    press = _mount(VIEWS["mcp"], model)
    assert press("char", "x") is False
    assert state.done is False


@pytest.mark.parametrize("view_id", ["sessions", "mcp", "usage", "notify", "skill"])
def test_standard_views_render_title_line(view_id: str, permissive_model):
    """标准列表视图：打开态首屏必须有可见文本（防「空屏但没报错」）。"""
    attr = f"{view_id}_view"
    state_cls = {
        "sessions": state_types.SessionsViewState,
        "mcp": state_types.McpViewState,
        "usage": state_types.UsageViewState,
        "notify": state_types.NotifyViewState,
        "skill": state_types.SkillViewState,
    }[view_id]
    state = state_cls()
    state.visible = True
    model = permissive_model(width=100)
    setattr(model, attr, state)
    model.fullscreen = view_id
    text = strip_ansi(
        renderToString(h(VIEWS[view_id], {"model": model, "width": 100}), {"columns": 100})
    )
    assert text.strip(), f"{view_id} 打开态无任何可见文本"
