"""事件消费者条目插件 — 清单中每个内置消费者一个独立插件条目。

「一切皆插件」：内置事件消费者（output/chat_ui/error_handler）不再硬编码在
``src.plugins.consumers`` 里，而是由清单中的独立条目声明::

    - id: consumer_output
      plugin: src.plugins.consumer_entries:apply_consumer
      config:
        id: output                        # 内置消费者 id（可被 patch/overlay 定位）
        # consumer: my_pkg.MyConsumer      # 可选：替换实现（点分路径）

插件挂载时把该消费者注册进消费者注册表（``factory=None`` 用默认实现）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置消费者随之缺席
（``consumers`` 聚合插件经 ``managed_consumers`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[..., Any]]:
    ref = config.get("consumer")
    if not ref:
        return None

    def _factory(**kwargs):
        from .tool_plugin import import_attr

        cls = import_attr(ref)
        return cls(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("consumer")
def apply_consumer(ctx):
    from ..tui.events.consumer_registry import register_builtin_consumer

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("consumer 条目缺少 config.id")
    undo = register_builtin_consumer(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_consumer"]
