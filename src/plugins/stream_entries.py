"""流式处理器条目插件 — 清单中每个内置流式处理器一个独立插件条目。

「一切皆插件」：内置流式 chunk 处理器（reasoning/content/tool_calls/speed）不再
由 ``AsyncStreamPipeline.__init__`` 一次性硬编码装配，而是由清单中的独立条目
声明::

    - id: stream_handler_reasoning
      plugin: src.plugins.stream_entries:apply_stream_handler
      config:
        id: reasoning                    # 内置项 id（可被 patch/overlay 定位）
        # handler: my_pkg.MyReasoningHandler   # 可选：替换实现（点分路径）
        # kwargs: {..}                          # 可选：替换实现的构造参数

插件挂载时把该 id 的内置项注册进流式处理器注册表（``factory=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置
处理器随之缺席（``stream`` 聚合插件经 ``managed_stream_handlers`` 抑制默认
装配；管线对缺席角色回退空处理器，保留核心累积、禁用副作用）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[], Any]]:
    ref = config.get("handler")
    if not ref:
        return None
    kwargs = dict(config.get("kwargs") or {})

    def _factory():
        from .tool_plugin import import_attr

        cls = import_attr(ref)
        return cls(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("stream_handler")
def apply_stream_handler(ctx):
    from ..api.stream.registry import register_builtin_stream_handler

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("stream_handler 条目缺少 config.id")
    undo = register_builtin_stream_handler(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_stream_handler"]
