"""UI 插件 — 提供 ``ctx.ui``。

桥接表现层（tui/renderer）运行时能力：活跃 ChatUI、显示事件代理、主题、
diff 渲染、消息展示。经 ``core.adapters.ui_runtime`` 单一入口访问。
"""

from __future__ import annotations

from ..core.adapters import ui_runtime as _ui_runtime
from ..kernel import Service, plugin


class UiService(Service):
    """UI 服务 — 占据 ``ctx.ui``。"""

    provide = "ui"
    name = "ui"
    inject = ("config", "events")

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
