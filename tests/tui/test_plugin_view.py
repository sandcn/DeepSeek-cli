"""plugin 命令 / 已加载插件视图测试（用户需求：/plugin 只显示当前已加载的插件）。

覆盖：
  1. ``plugins.view_model`` 纯逻辑：内核 Fiber（当前已加载插件，排除
     DISPOSED）条目构建 + format_plugin_text；
  2. ``PluginViewState`` 跨线程终态协议（try_set_final first-write-wins）；
  3. ``_cmd_plugin`` 命令分支（无 ChatUI 文本回退 + 有 ChatUI 打开/清理协议）；
  4. ``PluginView`` 组件渲染与交互（左列表选择 / l 进入详情 / 右栏滚动 /
     h 返回 / Esc 关闭）；
  5. ``reset_display`` 重置 plugin_view 保留 seq；``FULLSCREEN_VIEWS`` 注册；
  6. ``Kernel(profile=...)`` 记录 Profile。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.ink.fiber import InputHook


# ── 测试辅助 ──────────────────────────────────────────────

class _Recorder:
    """命令输出捕获（替换 _plugin_cmd._out）。"""

    def __init__(self):
        self.calls: list[str] = []

    def write(self, text, level="info", source="cmd"):
        self.calls.append(text)


class _FakeChatUI:
    """最小 ChatUIConsumer 桩（get_model / request_bottom_redraw 协议）。"""

    def __init__(self, model):
        self._model = model

    def get_model(self):
        return self._model

    def request_bottom_redraw(self):
        pass

    def flush_input_router(self, _sec):
        pass


def _render_component(component, model, width=80, fiber=None):
    """在手动 fiber 上下文渲染组件（返回 fiber + 元素树）。"""
    from src.tui.ink import hooks
    from src.tui.ink.fiber import Fiber, TAG_FUNCTION

    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, {"model": model, "width": width})
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = component({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _find_input_handler(fiber):
    """查找 fiber 上注册的活跃 use_input handler。"""
    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _make_ctx(arg: str, ui_adapter=None):
    from src.core.internal.commands._command_core import CommandContext

    return CommandContext(
        messages=[], state={}, arg=arg,
        build_system_prompt=None, get_user_input=None,
        context_manager=None, session=None,
        config_port=None, ui_adapter=ui_adapter,
    )


def _sample_entries():
    """最小已加载插件条目列表（kernel 单类——覆盖分类标题/左右渲染）。"""
    return [
        {
            "name": "config", "kind": "kernel", "kind_label": "内核运行时插件",
            "subtitle": "ACTIVE",
            "fields": [
                ("分类", "内核运行时插件（Fiber）"),
                ("状态", "ACTIVE"),
                ("依赖 (inject)", "config"),
                ("配置", "{}"),
            ],
        },
        {
            "name": "tools", "kind": "kernel", "kind_label": "内核运行时插件",
            "subtitle": "ACTIVE",
            "fields": [
                ("分类", "内核运行时插件（Fiber）"),
                ("状态", "ACTIVE"),
                ("提供服务", "tools"),
                ("配置", "{}"),
            ],
        },
        {
            "name": "renderer", "kind": "kernel", "kind_label": "内核运行时插件",
            "subtitle": "ACTIVE",
            "fields": [
                ("分类", "内核运行时插件（Fiber）"),
                ("状态", "ACTIVE"),
                ("提供服务", "renderer"),
                ("配置", "{}"),
            ],
        },
    ]


# ═══════════════════════════════════════════════════════════
# 1. view_model 纯逻辑
# ═══════════════════════════════════════════════════════════

class TestViewModel:

    def test_kernel_entries_from_fake_kernel(self):
        from src.plugins import view_model as vm

        def _fiber(name, state, inject, provide, children=(), error=None):
            definition = SimpleNamespace(source=f"src.plugins.{name}", provide=tuple(provide),
                                         service_cls=None)
            return SimpleNamespace(
                name=name, state=SimpleNamespace(value=state), definition=definition,
                inject=tuple(inject), missing_dependencies=lambda: ["missing"] if inject else [],
                config={"k": 1}, error=error, _children=list(children),
            )

        child = _fiber("child", "ACTIVE", [], [])
        fake = SimpleNamespace(fibers=lambda: [
            _fiber("tools", "ACTIVE", ["config"], ["tools"], children=[child]),
            _fiber("broken", "FAILED", [], [], error=ValueError("boom")),
        ])
        entries = vm._kernel_entries(fake)
        assert [e["name"] for e in entries] == ["tools", "broken"]
        tools = entries[0]
        assert tools["kind"] == "kernel"
        assert tools["subtitle"] == "ACTIVE"
        fields = dict(tools["fields"])
        assert fields["状态"] == "ACTIVE"
        assert fields["依赖 (inject)"] == "config"
        assert fields["提供服务"] == "tools"
        assert fields["子插件"] == "child"
        assert fields["来源"] == "src.plugins.tools"
        broken_fields = dict(entries[1]["fields"])
        assert "ValueError" in broken_fields["错误"]

    def test_kernel_entries_skips_disposed(self):
        """已卸载（DISPOSED）的历史 Fiber 不属于「当前已加载」。"""
        from src.plugins import view_model as vm

        def _fiber(name, state):
            return SimpleNamespace(
                name=name, state=SimpleNamespace(value=state),
                definition=SimpleNamespace(source="", provide=(), service_cls=None),
                inject=(), missing_dependencies=lambda: [], config={}, error=None,
                _children=[],
            )

        fake = SimpleNamespace(fibers=lambda: [
            _fiber("live", "ACTIVE"),
            _fiber("old", "DISPOSED"),
            _fiber("boot", "PENDING"),
        ])
        entries = vm._kernel_entries(fake)
        assert [e["name"] for e in entries] == ["live", "boot"]

    def test_kernel_entries_without_kernel(self):
        from src.plugins import view_model as vm

        assert vm._kernel_entries(None) == []

    def test_build_plugin_entries_kernel_only(self):
        """build_plugin_entries 只产出内核已加载插件（kernel 单类）。"""
        from src.plugins.view_model import build_plugin_entries

        fake = SimpleNamespace(fibers=lambda: [
            SimpleNamespace(
                name="config", state=SimpleNamespace(value="ACTIVE"),
                definition=SimpleNamespace(source="src.plugins.config", provide=("config",),
                                           service_cls=None),
                inject=(), missing_dependencies=lambda: [], config={}, error=None,
                _children=[],
            ),
        ])
        entries = build_plugin_entries(kernel=fake)
        assert [e["kind"] for e in entries] == ["kernel"]
        assert entries[0]["name"] == "config"
        for e in entries:
            assert {"name", "kind", "kind_label", "subtitle", "fields"} <= set(e)
            assert e["kind_label"] == "内核运行时插件"

    def test_build_plugin_entries_empty_kernel(self):
        """内核无已加载插件 → 空列表（界面显示「无已加载插件」）。"""
        from src.plugins.view_model import build_plugin_entries

        empty = SimpleNamespace(fibers=lambda: [])
        assert build_plugin_entries(kernel=empty) == []

    def test_format_plugin_text(self):
        from src.plugins.view_model import format_plugin_text

        text = format_plugin_text(_sample_entries())
        assert "已加载插件" in text
        assert "内核运行时插件（3）" in text
        assert "config" in text and "renderer" in text
        assert format_plugin_text([]) == "已加载插件\n  (无已加载插件)"


# ═══════════════════════════════════════════════════════════
# 2. PluginViewState 终态协议
# ═══════════════════════════════════════════════════════════

class TestPluginViewState:

    def test_try_set_final_first_write_wins(self):
        from src.tui.app._state_types import PluginViewState

        s = PluginViewState(visible=True)
        assert s.try_set_final("cancel") is True
        assert s.done and s.action == "cancel"
        assert s.try_set_final("timeout") is False
        assert s.action == "cancel"


# ═══════════════════════════════════════════════════════════
# 3. _cmd_plugin 命令分支
# ═══════════════════════════════════════════════════════════

class TestCmdPlugin:

    def test_no_ui_fallback_text(self, monkeypatch):
        from src.core.commands import _plugin_cmd as pc

        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: None)
        rec = _Recorder()
        monkeypatch.setattr(pc, "_out", rec)
        assert pc._cmd_plugin(_make_ctx("")) is True
        joined = "\n".join(rec.calls)
        assert "已加载插件" in joined

    def test_open_plugin_ui_opens_and_cleans(self, monkeypatch):
        from src.core.commands import _plugin_cmd as pc
        from src.tui.app.model import AppModel

        model = AppModel()
        fake = _FakeChatUI(model)
        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: fake)

        def fake_sleep(_sec):
            model.plugin_view.try_set_final("cancel")

        monkeypatch.setattr(pc._time, "sleep", fake_sleep)
        rec = _Recorder()
        monkeypatch.setattr(pc, "_out", rec)
        assert pc._cmd_plugin(_make_ctx("")) is True
        assert model.fullscreen == ""
        assert not model.plugin_view.visible
        assert model.plugin_view.seq == 1
        assert "插件界面已关闭" in "\n".join(rec.calls)

    def test_open_plugin_ui_timeout(self, monkeypatch):
        from src.core.commands import _plugin_cmd as pc
        from src.tui.app.model import AppModel

        model = AppModel()
        fake = _FakeChatUI(model)
        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: fake)
        original_monotonic = pc._time.monotonic

        def fake_sleep(_sec):
            monkeypatch.setattr(pc._time, "monotonic", lambda: original_monotonic() + 700)

        monkeypatch.setattr(pc._time, "sleep", fake_sleep)
        rec = _Recorder()
        monkeypatch.setattr(pc, "_out", rec)
        assert pc._cmd_plugin(_make_ctx("")) is True
        assert model.fullscreen == ""
        assert not model.plugin_view.visible
        assert "超时关闭" in "\n".join(rec.calls)


# ═══════════════════════════════════════════════════════════
# 4. PluginView 组件渲染与交互
# ═══════════════════════════════════════════════════════════

class TestPluginViewComponent:

    def _render(self, model, width=80, fiber=None):
        from src.tui.app.plugin_view import PluginView

        return _render_component(PluginView, model, width=width, fiber=fiber)

    def _visible_model(self, entries=None):
        from src.tui.app.model import AppModel, PluginViewState

        model = AppModel()
        model.plugin_view = PluginViewState(
            visible=True, seq=1, entries=entries if entries is not None else _sample_entries(),
        )
        model.fullscreen = "plugin"
        return model

    def test_invisible_returns_empty(self):
        from src.tui.app.model import AppModel

        model = AppModel()
        _, el = self._render(model)
        assert el.type == "text"

    def test_header_shows_title_and_count(self):
        model = self._visible_model()
        _, el = self._render(model)
        header = el.children[0]
        runs = header.props.get("styled") or []
        plain = "".join(getattr(r, "text", "") or "" for r in runs)
        assert "已加载插件" in plain
        assert "共 3 个" in plain

    def test_esc_closes(self):
        model = self._visible_model()
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler is not None
        assert handler(_ev("escape")) is True
        assert model.plugin_view.done and model.plugin_view.action == "cancel"

    def test_left_select_and_enter_detail_then_back(self):
        model = self._visible_model()
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        # l 进入右栏详情
        assert handler(_ev("char", "l")) is True
        assert model.plugin_view.pane == "detail"
        # h 返回左栏
        assert handler(_ev("char", "h")) is True
        assert model.plugin_view.pane == "list"
        # Enter 也进入详情
        assert handler(_ev("enter")) is True
        assert model.plugin_view.pane == "detail"

    def test_detail_scroll_moves_cursor(self):
        model = self._visible_model()
        model.plugin_view.pane = "detail"
        model.plugin_view.cursor = 0
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("char", "j")) is True
        assert model.plugin_view.cursor == 1
        assert handler(_ev("arrow_down")) is True
        assert model.plugin_view.cursor == 2
        assert handler(_ev("char", "k")) is True
        assert model.plugin_view.cursor == 1
        assert handler(_ev("home")) is True
        assert model.plugin_view.cursor == 0

    def test_left_navigation_delegates(self):
        """左栏焦点时导航键不消费（放行 ListView）。"""
        model = self._visible_model()
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("arrow_down")) is False
        assert handler(_ev("char", "j")) is False

    def test_on_navigate_resets_detail(self):
        model = self._visible_model()
        fiber, _el = self._render(model)
        model.plugin_view.cursor = 5
        model.plugin_view.scroll = 3
        # 直接驱动受控导航回调（ListView 子组件不在此渲染）
        from src.tui.app.plugin_view import _build_display

        _items, specs = _build_display(model.plugin_view.entries)
        # 找到第一个 entry 的显示索引
        entry_idx = next(i for i, s in enumerate(specs) if s[0] == "entry")
        # 从元素树中取 ListView 的 onNavigate prop
        row = _el.children[1]
        ledger = row.children[0]
        ledger.props["onNavigate"](entry_idx)
        assert model.plugin_view.selected == entry_idx
        assert model.plugin_view.cursor == 0
        assert model.plugin_view.scroll == 0


# ═══════════════════════════════════════════════════════════
# 5. 集成：注册表 / reset_display / Kernel profile
# ═══════════════════════════════════════════════════════════

class TestIntegration:

    def test_fullscreen_registered(self):
        from src.tui.app.app import FULLSCREEN_VIEWS
        from src.tui.app.plugin_view import PluginView

        assert FULLSCREEN_VIEWS.get("plugin") is PluginView

    def test_command_registered(self):
        import src.core.commands  # noqa: F401
        from src.core.commands.base import get_plugin_registry

        plugin = get_plugin_registry().get("plugin")
        assert plugin is not None
        assert plugin.meta.name == "plugin"

    def test_handle_command_dispatch(self, monkeypatch):
        """``handle_command("/plugin", ...)`` 经注册表调度到 PluginCommand。"""
        import src.core.commands  # noqa: F401
        from src.core.internal.commands._command_core import handle_command
        from src.core.commands import _plugin_cmd as pc

        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: None)
        rec = _Recorder()
        monkeypatch.setattr(pc, "_out", rec)
        assert handle_command("/plugin", [], {}, None, None) is True
        assert any("已加载插件" in c for c in rec.calls)

    def test_app_renders_fullscreen_without_error(self):
        """整屏渲染 PluginView（经 App + 调和器 + 布局）无异常且内容正确。"""
        from src.tui.ink import h
        from src.tui.ink import components as _components
        from src.tui.ink.reconciler import Reconciler
        from src.tui.app.app import App
        from src.tui.app.model import AppModel, PluginViewState

        model = AppModel()
        model.plugin_view = PluginViewState(visible=True, seq=1, entries=_sample_entries())
        model.fullscreen = "plugin"
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        rec.render(root, h(App, {"model": model, "width": 90}), 90, 24)
        frame = _components.render_frame(root, 90)
        text = "\n".join(
            "".join(getattr(r, "text", "") for r in getattr(line, "runs", line))
            for line in frame.lines
        )
        assert "已加载插件" in text
        assert "内核运行时插件" in text
        assert "renderer" in text

    def test_reset_display_preserves_seq(self):
        from src.tui.app.model import AppModel, PluginViewState

        model = AppModel()
        model.plugin_view = PluginViewState(visible=True, seq=7, entries=_sample_entries())
        model.fullscreen = "plugin"
        model.reset_display()
        assert not model.plugin_view.visible
        assert model.plugin_view.seq == 7
        assert model.fullscreen == ""

    def test_kernel_profile_recorded(self):
        from src.kernel import Kernel

        assert Kernel().profile == ""
        assert Kernel(name="chat", profile="headless").profile == "headless"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
