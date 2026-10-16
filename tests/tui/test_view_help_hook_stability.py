"""全屏视图帮助面板 hook 稳定性回归（2026-10）。

背景：多个全屏视图此前把 ``use_memo`` 放进 ``if help_open: … else:
use_memo(…)`` 的 else 分支——按 ``?`` 打开帮助时分支切换导致 hook 序列变化，
抛 ``HookStateError``（视图渲染异常，「按 ? 没有帮助面板」）。

本测试在**同一个 reconciler**（fiber 复用，与真实渲染循环一致）中切换
``help_open`` 连续渲染，确保不再抛错且帮助内容可见。
"""

from __future__ import annotations

import importlib

import pytest

#: (视图 id, AppModel 状态属性, 模块, 组件类名)
VIEW_CASES = [
    ("changes", "changes_view", "src.tui.app.changes_view", "ChangesView"),
    ("sandbox", "sandbox_view", "src.tui.app.sandbox_stats_view", "SandboxStatsView"),
    ("sandbox_history", "sandbox_history_view", "src.tui.app.sandbox_history_view", "SandboxHistoryView"),
    ("sandbox_records", "sandbox_records_view", "src.tui.app.sandbox_records_view", "SandboxRecordsView"),
    ("logs", "logs_view", "src.tui.app.logs_view", "LogsView"),
    ("mcp", "mcp_view", "src.tui.app.mcp_view", "McpView"),
    ("notify", "notify_view", "src.tui.app.notify_view", "NotifyView"),
    ("search", "search_view", "src.tui.app.search_view", "SearchView"),
    ("skill", "skill_view", "src.tui.app.skill_view", "SkillView"),
    ("theme", "theme_view", "src.tui.app.theme_view", "ThemeView"),
    ("keymap", "keymap_view", "src.tui.app.keymap_view", "KeymapView"),
    ("plugin", "plugin_view", "src.tui.app.plugin_view", "PluginView"),
    ("config", "config_view", "src.tui.app.config_view", "ConfigView"),
    ("sessions", "sessions_view", "src.tui.app.sessions_view", "SessionsView"),
    ("usage", "usage_view", "src.tui.app.usage_view", "UsageView"),
    ("model", "model_view", "src.tui.app.model_view", "ModelView"),
]


class _PaneHarness:
    """同一 reconciler 连续渲染指定组件的 harness（fiber 复用 → hook 序列校验）。"""

    def __init__(self, component, model, columns: int = 100, rows: int = 32):
        from src.tui.ink import h
        from src.tui.ink.reconciler import Reconciler

        self.recon = Reconciler(schedule_callback=lambda: None)
        self.recon.hook_context.install_headless(columns, rows)
        self.root = Reconciler.create_root()
        self.element = h(component, {"model": model, "width": columns})
        self.columns = columns

    def render(self) -> None:
        self.recon.render(self.root, self.element, self.columns, 0)

    def text(self) -> str:
        from src.tui.ink import h, renderToString
        from src.tui.ink.helpers import strip_ansi

        return strip_ansi(renderToString(self.element, {"columns": self.columns}))


@pytest.mark.parametrize("name,attr,mod_name,cls_name", VIEW_CASES,
                         ids=[c[0] for c in VIEW_CASES])
def test_help_toggle_keeps_hook_order(name, attr, mod_name, cls_name):
    """help_open 关→开→关 连续渲染：hook 序列恒定（不抛 HookStateError）。"""
    from src.tui.app.model import AppModel

    comp = getattr(importlib.import_module(mod_name), cls_name)
    model = AppModel()
    state = getattr(model, attr)
    state.visible = True
    hv = _PaneHarness(comp, model)

    state.help_open = False
    hv.render()
    state.help_open = True
    hv.render()          # ★ 修复前在此抛 HookStateError
    assert state.help_open is True
    text = hv.text()
    assert "帮助" in text, name
    state.help_open = False
    hv.render()


@pytest.mark.parametrize("name,attr,mod_name,cls_name", VIEW_CASES,
                         ids=[c[0] for c in VIEW_CASES])
def test_help_panel_has_key_rows(name, attr, mod_name, cls_name):
    """帮助面板内容非空（键位速查行可见）。"""
    from src.tui.app.model import AppModel

    comp = getattr(importlib.import_module(mod_name), cls_name)
    model = AppModel()
    state = getattr(model, attr)
    state.visible = True
    state.help_open = True
    text = _PaneHarness(comp, model).text()
    assert "Esc" in text or "关闭" in text, name
