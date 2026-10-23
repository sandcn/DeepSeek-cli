"""视图注册表契约测试 — 每个声明的视图都必须「可解析、可渲染、可关闭」。

``src/tui/app/view_registry.py`` 用**点分字符串**声明视图组件
（``"src.tui.app.trace_view.TraceView"``），由 ``importlib`` 在 ``_refresh()``
时解析。这种装配方式让静态分析、IDE 跳转、死代码检测全部失效——视图被改名/
移动后，唯一的失败现场是用户按下 F11 时的一次 ``ImportError``（``_refresh``
里被 ``except Exception: continue`` 吞掉，视图直接从注册表消失，表现为「按键
无反应」）。

本文件把该契约变成可执行断言：
  - 声明的每个 id 都能解析出组件，且进入 fullscreen/bottom 字典；
  - 组件可渲染（未打开态 + 打开态都不得抛异常）；
  - 「空闲静态 / 定时刷新」声明确实挂到了组件对象上（``_refresh`` 的副作用）。
"""

from __future__ import annotations

import pytest

from src.tui.app import _state_types as state_types
from src.tui.app.view_registry import (
    bottom_views,
    builtin_view_ids,
    describe,
    fullscreen_views,
)
from src.tui.ink import h
from src.tui.ink._render_api import renderToString
from src.tui.ink.helpers import strip_ansi

#: 视图 id → (model 上的状态属性名, 状态类型)。未列出的视图为「无状态视图」
#: （help / trace_tools / palette 等自管理或只读 model），只做未打开态渲染。
_STATE_MAP = {
    "config": ("config_view", state_types.ConfigViewState),
    "plugin": ("plugin_view", state_types.PluginViewState),
    "model": ("model_view", state_types.ModelViewState),
    "sessions": ("sessions_view", state_types.SessionsViewState),
    "changes": ("changes_view", state_types.ChangesViewState),
    "sandbox": ("sandbox_view", state_types.SandboxStatsViewState),
    "sandbox_history": ("sandbox_history_view", state_types.SandboxHistoryViewState),
    "sandbox_records": ("sandbox_records_view", state_types.SandboxRecordsViewState),
    "theme": ("theme_view", state_types.ThemeViewState),
    "skill": ("skill_view", state_types.SkillViewState),
    "mcp": ("mcp_view", state_types.McpViewState),
    "usage": ("usage_view", state_types.UsageViewState),
    "search": ("search_view", state_types.SearchViewState),
    "keymap": ("keymap_view", state_types.KeymapViewState),
    "notify": ("notify_view", state_types.NotifyViewState),
    "export": ("export_view", state_types.ExportViewState),
    "logs": ("logs_view", state_types.LogsViewState),
}
#: 底部模态视图（user_select / editmsg）的状态类型来自核心层且可见性由协议
#: 序列驱动，打开态渲染由 ``tests/tui/app/test_user_select_*.py`` 专测覆盖。


def _all_views() -> dict:
    views = dict(fullscreen_views())
    views.update(bottom_views())
    return views


def _component(entry):
    return entry[0] if isinstance(entry, tuple) else entry


def test_every_builtin_view_is_registered():
    """``builtin_view_ids()`` 声明的每个 id 都进入其中一个视图字典。"""
    views = _all_views()
    missing = [view_id for view_id in builtin_view_ids() if view_id not in views]
    assert not missing, f"声明但未注册（点分引用解析失败）：{missing}"


def test_registry_entries_are_callable():
    """注册表里的组件必须可调用（函数组件 / 类组件）。"""
    for view_id, entry in _all_views().items():
        component = _component(entry)
        assert callable(component), f"{view_id} 的组件不可调用：{component!r}"


def test_describe_covers_active_views():
    """``describe()`` 与视图字典口径一致（插件自省用）。"""
    described = {item["id"] for item in describe()}
    assert described == set(_all_views())


def test_idle_static_and_refresh_declarations_are_attached():
    """``_refresh()`` 必须把 idle_static / refresh_interval 挂到组件对象上。"""
    for view_id, entry in _all_views().items():
        component = _component(entry)
        assert hasattr(component, "_ink_idle_static"), f"{view_id} 缺 idle_static 声明"
        assert hasattr(component, "_ink_refresh_interval"), f"{view_id} 缺 refresh_interval 声明"


@pytest.mark.parametrize("view_id", sorted(_all_views()))
def test_view_renders_when_closed(view_id: str, permissive_model):
    """未打开态：任何视图都必须渲染成功（返回元素，不得抛异常）。"""
    component = _component(_all_views()[view_id])
    model = permissive_model(width=80)
    text = renderToString(h(component, {"model": model, "width": 80}), {"columns": 80})
    assert isinstance(text, str)


@pytest.mark.parametrize("view_id", sorted(_STATE_MAP))
def test_view_renders_when_open(view_id: str, permissive_model):
    """打开态：挂真实状态类型后必须渲染出内容（且不越界）。"""
    if view_id not in _all_views():
        pytest.skip(f"{view_id} 未注册（可能被清单禁用）")
    attr, state_cls = _STATE_MAP[view_id]
    state = state_cls()
    state.visible = True
    model = permissive_model(width=100, **{attr: state})
    model.fullscreen = view_id
    model.bottom_view = view_id
    component = _component(_all_views()[view_id])
    text = renderToString(h(component, {"model": model, "width": 100}), {"columns": 100})
    plain = strip_ansi(text)
    lines = plain.splitlines()
    assert [line for line in lines if line.strip()], f"{view_id} 打开态渲染为空"
    too_wide = [line for line in lines if len(line) > 100]
    assert not too_wide, f"{view_id} 渲染行超宽：{too_wide[:2]}"
