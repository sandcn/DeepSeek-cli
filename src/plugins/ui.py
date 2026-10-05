"""UI 插件 — 提供 ``ctx.ui``。

桥接表现层（tui/renderer）运行时能力：活跃 ChatUI、显示事件代理、主题、
diff 渲染、消息展示，并**独占**终端宽度缓存（``TerminalWidthCache``）与
SubAgent 面板控制器（``SubAgentPanelController``）——二者的 ``get_default()``
内核优先返回本服务实例，内核缺失时才回退进程级单例。

TUI 子系统装配（``TuiAssembly``）经 ``ctx.ui.assemble()`` 暴露，终端 UI
构造方经内核服务装配子系统，可按插件替换装配实现。
"""

from __future__ import annotations

from ..core.adapters import ui_runtime as _ui_runtime
from ..kernel import Service, plugin


class UiService(Service):
    """UI 服务 — 占据 ``ctx.ui``。"""

    provide = "ui"
    name = "ui"
    inject = ("config", "events")

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._width_cache = None
        self._subagent_panel = None

    # ── 表现层单例（内核真源） ─────────────────────────

    @property
    def width_cache(self):
        """本服务独占的终端宽度缓存（``TerminalWidthCache.get_default`` 真源）。"""
        if self._width_cache is None:
            from ..tui._screen import TerminalWidthCache

            self._width_cache = TerminalWidthCache()
        return self._width_cache

    @property
    def subagent_panel(self):
        """本服务独占的 SubAgent 面板控制器（``get_default`` 真源）。"""
        if self._subagent_panel is None:
            from ..tui.subagent import SubAgentPanelController

            self._subagent_panel = SubAgentPanelController()
        return self._subagent_panel

    # ── TUI 子系统装配 ─────────────────────────────────

    def assemble(self):
        """装配 TUI 子系统，返回 ``TuiAssemblyResult``（可替换装配实现）。"""
        from ..tui._assembly import TuiAssembly

        return TuiAssembly.assemble()

    # ── 桥接能力 ───────────────────────────────────────

    def active_chat_ui(self):
        return _ui_runtime.get_active_chat_ui()

    def create_chat_ui(self):
        """创建 ChatUIConsumer（终端界面事件消费者）。"""
        return _ui_runtime.create_chat_ui()

    def create_display_proxy(self, source: str = "", max_history: int = 3):
        return _ui_runtime.create_display_proxy(source=source, max_history=max_history)

    def emit_display_event(self, event) -> None:
        _ui_runtime.emit_display_event(event)

    def theme_registry(self):
        return _ui_runtime.get_theme_registry()

    def invalidate_palette_cache(self) -> None:
        _ui_runtime.invalidate_palette_cache()

    def render_diff(self, path: str, old_content: str, new_content: str) -> str:
        return _ui_runtime.render_diff_to_ansi(path, old_content, new_content)

    def display_messages(self, data, agent=None, idx_map=None, speed: int = 0) -> None:
        _ui_runtime.display_messages(data, agent=agent, idx_map=idx_map, speed=speed)


@plugin("ui", inject=["config", "events"], provide=["ui"])
def apply(ctx):
    return UiService(ctx)


__all__ = ["UiService", "apply"]
