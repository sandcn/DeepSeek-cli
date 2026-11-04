"""全屏视图视口行预算 — 修复「到顶少一行」的回归测试。

背景（2026-11-04）：共享 ``viewport_rows()`` 默认预留 2 行（头部 1 + 底部
余量 1），各视图又用 ``extra_rows`` 单独扣除状态 / 搜索 / 编辑行 → 内容区
恒少 1 行（列表到顶时底部空一行，最后一项不可见）。plugin / config /
model / command palette 各自复制了同类预算，少 2~3 行。本文件锁定修复后
的不变量：**全屏视图渲染总行数 = 终端高度**（底部动态行存在时同样成立）。

测试用固定终端尺寸（100x30）避免依赖真实终端。
"""

from __future__ import annotations

import pytest

from src.tui._screen import TerminalWidthCache
from src.tui.app import _state_types as st
from src.tui.app.view_registry import fullscreen_views
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi

H, W = 30, 100
VIEWS = fullscreen_views()


@pytest.fixture
def fixed_terminal():
    """固定终端尺寸 100x30（结束后恢复自动探测）。"""
    cache = TerminalWidthCache.get_default()
    cache.set_dimensions(W, H)
    try:
        yield
    finally:
        cache.clear_override()


def _lines(component, model) -> list[str]:
    text = strip_ansi(
        renderToString(h(component, {"model": model, "width": W}), {"columns": W})
    )
    return text.split("\n")


def _model(model_cls, state, attr: str, view_id: str):
    model = model_cls(width=W, height=H)
    state.visible = True
    setattr(model, attr, state)
    model.fullscreen = view_id
    return model


def _sessions_state(**fields) -> st.SessionsViewState:
    state = st.SessionsViewState()
    state.entries = [
        {"id": f"id{i}", "title": f"会话标题 {i}", "model": "deepseek",
         "saved_at": "2026-11-04", "message_count": i,
         "preview_lines": [("用户", f"消息 {i}")]}
        for i in range(50)
    ]
    for key, value in fields.items():
        setattr(state, key, value)
    return state


def test_viewport_reserved_only_header(fixed_terminal):
    """共享视口预算只预留头部 1 行（底部动态行由 extra_rows 扣除）。"""
    from src.tui.app._view_common import VIEWPORT_RESERVED, viewport_rows

    assert VIEWPORT_RESERVED == 1
    assert viewport_rows() == H - 1


def test_sessions_fills_terminal(permissive_model, fixed_terminal):
    """会话浏览器：50 条会话时总行数 = 终端高度，最后一行不是空行。"""
    state = _sessions_state()
    model = _model(permissive_model, state, "sessions_view", "sessions")

    lines = _lines(VIEWS["sessions"], model)

    assert len(lines) == H
    assert "会话标题 0" in lines[1]          # 顶部第一项（到顶不丢行）
    assert "会话标题 28" in lines[-1]        # vh = 29 → 末行是第 29 项
    assert lines[-1].strip()                 # 底部无空行


def test_sessions_fills_terminal_with_bottom_rows(permissive_model, fixed_terminal):
    """状态 / 搜索 / 重命名行同时存在时仍恰好填满终端（不溢出）。"""
    state = _sessions_state(
        search_mode=True, status_message="提示", rename_mode=True,
    )
    model = _model(permissive_model, state, "sessions_view", "sessions")

    lines = _lines(VIEWS["sessions"], model)

    assert len(lines) == H
    assert "重命名:" in lines[-2]
    assert lines[-1].startswith("/")


def test_sessions_fills_terminal_after_search(permissive_model, fixed_terminal):
    """搜索已执行（pattern 保留、输入模式已退出）时状态行仍占一行。"""
    state = _sessions_state(search_pattern="会话")
    model = _model(permissive_model, state, "sessions_view", "sessions")

    lines = _lines(VIEWS["sessions"], model)

    assert len(lines) == H
    assert "/会话" in lines[-1]


def test_config_and_model_fill_terminal(permissive_model, fixed_terminal):
    """config / model 的本地视口预算：浏览态渲染总行数 = 终端高度。"""
    cfg_state = st.ConfigViewState()
    cfg_state.entries = [
        {"key": f"k{i}", "path": f"path.{i}", "value_text": "v", "value": "v",
         "type": "str", "desc": "d", "options": [], "source": "default"}
        for i in range(50)
    ]
    cfg_model = _model(permissive_model, cfg_state, "config_view", "config")
    cfg_lines = _lines(VIEWS["config"], cfg_model)
    assert len(cfg_lines) == H
    assert "↑↓/jk 选择" in cfg_lines[-1]

    mv_state = st.ModelViewState()
    mv_state.entries = [
        {"model": f"m{i}", "name": f"n{i}", "provider": "deepseek",
         "base_url": "", "index": i}
        for i in range(50)
    ]
    mv_model = _model(permissive_model, mv_state, "model_view", "model")
    mv_lines = _lines(VIEWS["model"], mv_model)
    assert len(mv_lines) == H
    assert "↑↓/jk 选择" in mv_lines[-1]


def test_plugin_fills_terminal_with_status_rows(permissive_model, fixed_terminal):
    """plugin 本地预算：搜索 + 状态行存在时总行数仍 = 终端高度。"""
    state = st.PluginViewState()
    state.entries = [
        {"name": f"p{i}", "version": "1.0", "kind": "tool", "description": "d",
         "enabled": True, "source": "builtin"}
        for i in range(50)
    ]
    state.search_mode = True
    state.status_message = "提示"
    model = _model(permissive_model, state, "plugin_view", "plugin")

    lines = _lines(VIEWS["plugin"], model)

    assert len(lines) == H
    assert lines[-1].startswith("/")


def test_plugin_filter_status_line_counts(permissive_model, fixed_terminal):
    """状态过滤标注也占底部状态行（无 status_message 时同样计入预算）。"""
    state = st.PluginViewState()
    state.entries = [
        {"name": f"p{i}", "version": "1.0", "kind": "tool", "description": "d",
         "enabled": True, "source": "builtin"}
        for i in range(50)
    ]
    state.filter_state = "启用"
    model = _model(permissive_model, state, "plugin_view", "plugin")

    lines = _lines(VIEWS["plugin"], model)

    assert len(lines) == H
    assert "状态 启用" in lines[-1]
