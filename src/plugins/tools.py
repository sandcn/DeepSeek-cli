"""工具插件 — 提供 ``ctx.tools``。

工具注册表（``ToolRegistry``）+ 工具执行管线（``tools/pre-execute`` /
``tools/execute`` / ``tools/post-execute`` waterfall 事件）。工具本身是插件：
内置工具由 ``tools_builtin`` 插件显式注册，外部插件可经 ``ctx.tools.register``
/ ``ctx.tools.define`` 注册自己的工具（与内置工具同构调度）。
"""

from __future__ import annotations

from ..kernel import Service, plugin


class ToolsService(Service):
    """工具服务 — 占据 ``ctx.tools``。"""

    provide = "tools"
    name = "tools"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        from ..tools.registry import ToolRegistry

        # 使用进程级默认注册表（与 ToolScheduler / MCP 动态工具共享同一实例，
        # 避免「内核注册表 ≠ 全局默认注册表」的双注册表割裂：MCP 工具注册到
        # 内核注册表后，ToolScheduler.default() 用默认注册表 dispatch 将找不到）。
        self._registry = ToolRegistry.default()
        self._register_listeners: list = []
        # 卸载时清空注册表（可逆副作用；clear 置 _initialized=False，
        # 无内核时下次访问 lazy 重新自动发现内置工具）
        ctx.effect(lambda: self._on_unload)

    # ── 生命周期 ─────────────────────────────────────────

    def _on_unload(self) -> None:
        self._register_listeners.clear()
        self._registry.clear()

    # ── 注册表访问 ───────────────────────────────────────

    @property
    def registry(self):
        return self._registry

    def names(self) -> list[str]:
        return sorted(self._registry.get_tools())

    def schemas(self) -> list[dict]:
        return list(self._registry.get_schemas())

    def get(self, name: str):
        return self._registry.get_tools().get(name)

    def metadata(self, name: str):
        return self._registry.get_metadata(name)

    def dispatch(self, tool_name: str, arguments: dict, agent=None):
        return self._registry.dispatch(tool_name, arguments, agent)

    # ── 工具注册（插件侧入口） ───────────────────────────

    def register(self, tool_class) -> None:
        """注册一个工具类（Func 子类），并通知注册监听器。"""
        self._registry.register(tool_class)
        name = getattr(tool_class, "name", None)
        for callback in list(self._register_listeners):
            try:
                callback(name, tool_class)
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "工具注册监听器异常: %s", name, exc_info=True
                )

    def define(self, name: str, schema: dict, handler, **metadata):
        """动态定义并注册一个工具（``define_tool`` 的便捷入口）。

        返回创建的 ``Func`` 子类（已注册）。
        """
        from ..tools.dynamic import define_tool

        tool_class = define_tool(name, schema, handler, **metadata)
        self.register(tool_class)
        ctx = self.ctx
        if ctx is not None:
            ctx.effect(lambda: (lambda: self._registry.unregister(name)))
        return tool_class

    def unregister(self, tool_name: str) -> bool:
        return self._registry.unregister(tool_name)

    def on_register(self, callback):
        """订阅「工具被注册」事件，返回 disposer。

        监听器签名 ``(name, tool_class) -> None``；插件可借此绑定工具元数据、
        审计或动态策略。卸载时应调用返回的 disposer（或经 ``ctx.effect``）。
        """
        if not callable(callback):
            raise TypeError(f"注册监听器必须可调用: {callback!r}")
        self._register_listeners.append(callback)
        called = False

        def _remove() -> None:
            nonlocal called
            if called:
                return
            called = True
            try:
                self._register_listeners.remove(callback)
            except ValueError:
                pass

        return _remove

    def mark_initialized(self) -> None:
        """标记注册表已初始化（显式注册路径避免懒发现重复注册）。"""
        self._registry.mark_initialized()

    def build_system_prompt(self) -> list[str]:
        return self._registry.build_system_prompt()


@plugin("tools", inject=["config"], provide=["tools"])
def apply(ctx):
    return ToolsService(ctx)
