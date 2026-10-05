"""渲染扩展条目插件 — 清单中每个内置 handler / filter 一个独立插件条目。

「一切皆插件」：内置渲染 handler/filter 不再由 ``renderer/extensions.py``
一次性默认装配，而是由清单中的独立条目声明::

    - id: renderer_handler_inline
      plugin: src.plugins.renderer_entries:apply_handler
      config:
        id: inline                       # 内置项 id（可被 patch/overlay 定位）
        # handler: my_pkg.MyHandler      # 可选：替换实现（点分路径）
        # kwargs: {..}                   # 可选：替换实现的构造参数

    - id: renderer_filter_stream_optimizer
      plugin: src.plugins.renderer_entries:apply_filter
      config:
        id: stream_optimizer

插件挂载时把该 id 的内置项注册进渲染扩展注册表（``factory=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置项随之缺席（``renderer_builtin`` 聚合插件经 ``managed_handlers`` /
``managed_filters`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict, key: str) -> Optional[Callable[[], Any]]:
    ref = config.get(key)
    if not ref:
        return None
    kwargs = dict(config.get("kwargs") or {})

    def _factory():
        from .tool_plugin import import_attr

        cls = import_attr(ref)
        return cls(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("renderer_handler", inject=["renderer"])
def apply_handler(ctx):
    from ..renderer.extensions import register_builtin_handler

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("renderer_handler 条目缺少 config.id")
    undo = register_builtin_handler(spec_id, _make_override_factory(ctx.config, "handler"))
    ctx.effect(lambda: undo)


@plugin("renderer_filter", inject=["renderer"])
def apply_filter(ctx):
    from ..renderer.extensions import register_builtin_filter

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("renderer_filter 条目缺少 config.id")
    undo = register_builtin_filter(spec_id, _make_override_factory(ctx.config, "filter"))
    ctx.effect(lambda: undo)


__all__ = ["apply_handler", "apply_filter"]
