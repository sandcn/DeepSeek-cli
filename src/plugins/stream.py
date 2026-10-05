"""流式管线插件 — 提供 ``ctx.stream``。

「一切皆插件」：流式处理管线（``AsyncStreamPipeline``）与四个内置处理器
（reasoning / content / tool_calls / speed）从 ``api`` 层硬编码上移为内核服务：

- ``ctx.stream.create_pipeline()``：构造流式管线（可被 config ``pipeline``
  替换为自定义实现）；
- ``ctx.stream.resolve(id)`` / ``handlers()``：自省当前生效的处理器；
- ``ctx.stream.register_handler(id, factory)``：注册/覆盖处理器（注册即副作用）；
- ``ctx.stream.disable(ids)`` / ``managed``：清单接管与显式禁用。

``src/api/stream/pipeline_async.py`` 经内核 ``ctx.stream`` 解析管线
（内核缺失时回退内置 ``AsyncStreamPipeline``）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


def _import_attr(dotted: str):
    from .tool_plugin import import_attr

    return import_attr(dotted)


class StreamService(Service):
    """流式管线服务 — 占据 ``ctx.stream``。"""

    provide = "stream"
    name = "stream"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        managed = cfg.get("managed_stream_handlers") or ()
        if managed:
            from ..api.stream.registry import set_managed_builtin_stream_handlers

            undo_managed = set_managed_builtin_stream_handlers(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_stream_handlers") or ()
        if disabled:
            from ..api.stream.registry import disable_builtin_stream_handlers

            undo_disabled = disable_builtin_stream_handlers(disabled)
            ctx.effect(lambda: undo_disabled)

    # ── 管线 ─────────────────────────────────────────────

    def create_pipeline(self):
        """构造流式管线（config ``pipeline`` 可替换实现）。"""
        ref = (self.config or getattr(self.ctx, "config", None) or {}).get("pipeline")
        if ref:
            return _import_attr(ref)()
        from ..api.stream.pipeline_async import AsyncStreamPipeline

        return AsyncStreamPipeline()

    # ── 处理器注册表 ─────────────────────────────────────

    def ids(self) -> List[str]:
        from ..api.stream.registry import builtin_stream_handler_ids

        return builtin_stream_handler_ids()

    def handlers(self) -> Dict[str, Any]:
        """当前生效的内置处理器 id 集合（含被清单接管的判定结果）。"""
        from ..api.stream.registry import builtin_stream_handler_factories

        return sorted(builtin_stream_handler_factories())

    def resolve(self, spec_id: str):
        """实例化某角色处理器（缺席时回退空处理器）。"""
        from ..api.stream.registry import resolved_stream_handler

        return resolved_stream_handler(spec_id)

    def register_handler(self, spec_id: str, factory: Callable[[], Any]):
        """注册/覆盖一个处理器（注册即副作用，卸载时自动撤销）。"""
        from ..api.stream.registry import register_stream_handler

        undo = register_stream_handler(spec_id, factory)
        self.ctx.effect(lambda: undo)
        return undo


@plugin("stream", inject=["config"], provide=["stream"])
def apply(ctx):
    return StreamService(ctx, ctx.config)


__all__ = ["StreamService", "apply"]
