"""Agent 中间件条目插件 — 清单中每个内置中间件一个独立插件条目。

「一切皆插件」：Agent 主循环中间件不再由 ``core/middleware/registry.py``
一次性默认装配，而是由清单中的独立条目声明::

    - id: middleware_audit
      plugin: src.plugins.middleware_entries:apply_middleware
      config:
        id: audit                        # 内置项 id（可被 patch/overlay 定位）
        # middleware: my_pkg.MyMiddleware  # 可选：替换实现（点分路径）
        # kwargs: {..}                     # 可选：替换实现的构造参数

插件挂载时把该 id 的内置中间件注册进中间件注册表（``factory=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置中间件随之缺席（``agent_middleware`` 聚合插件经 ``managed_middlewares``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[], Any]]:
    ref = config.get("middleware")
    if not ref:
        return None
    kwargs = dict(config.get("kwargs") or {})

    def _factory():
        from .tool_plugin import import_attr

        cls = import_attr(ref)
        return cls(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("middleware")
def apply_middleware(ctx):
    from ..core.middleware.registry import register_builtin_middleware

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("middleware 条目缺少 config.id")
    undo = register_builtin_middleware(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_middleware"]
