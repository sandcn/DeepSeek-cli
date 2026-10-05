"""补全提供者条目插件 — 清单中每个内置提供者一个独立插件条目。

「一切皆插件」：``CompletionEngine`` 的补全提供者（command / path / param）
不再硬编码在 ``complete`` 分支里，而是由清单中的独立条目声明::

    - id: completion_provider_command
      plugin: src.plugins.completion_provider_entries:apply_completion_provider
      config:
        id: command                         # 内置提供者 id（可被 patch/overlay 定位）
        # order: 0                          # 可选：覆盖解析顺序
        # handler: my_pkg:my_provider       # 可选：替换实现（点分引用）

插件挂载时把该 id 的内置提供者注册进注册表（``spec=None`` 用默认规格）；卸载
时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置提供者随之
缺席（``completion_providers`` 聚合插件经 ``managed_completion_providers``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: completion_provider 条目可由 config 覆盖的字段
_SPEC_FIELDS = ("order", "handler")


def _spec_from_config(spec_id: str, config: dict):
    from ..tui._completion_providers import CompletionProvider, default_provider

    if not any(field in config for field in _SPEC_FIELDS):
        return None
    base = default_provider(spec_id)
    return CompletionProvider(
        id=spec_id,
        order=int(config.get("order", base.order)),
        handler=str(config.get("handler", base.handler)),
    )


@plugin("completion_provider")
def apply_completion_provider(ctx):
    from ..tui._completion_providers import register_builtin_provider

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("completion_provider 条目缺少 config.id")
    undo = register_builtin_provider(spec_id, _spec_from_config(spec_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_completion_provider"]
