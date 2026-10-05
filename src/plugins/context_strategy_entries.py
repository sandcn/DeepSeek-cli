"""上下文压缩策略条目插件 — 清单中每个内置压缩策略一个独立插件条目。

「一切皆插件」：上下文压缩策略（``summarize`` / ``drop``）不再由
``ctx.context`` 一次性默认装配，而是由清单中的独立条目声明::

    - id: context_strategy_summarize
      plugin: src.plugins.context_strategy_entries:apply_context_strategy
      config:
        name: summarize                   # 内置策略名（可被 patch/overlay 定位）
        # strategy: my_pkg.MyStrategy      # 可选：替换实现（点分路径）

插件挂载时把该策略注册进 ``ctx.context``（``strategy=None`` 用默认实现）；卸载
时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置策略随之缺席
（``context`` 聚合插件经 ``managed_strategies`` 抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin


def _default_factory(name: str):
    if name == "summarize":
        from ..core.compression import SummarizeStrategy

        return lambda: SummarizeStrategy()
    if name == "drop":
        from ..core.compression import DropStrategy

        return lambda: DropStrategy()
    raise ValueError(f"未知内置压缩策略: {name!r}（可用: summarize/drop）")


@plugin("context_strategy", inject=["context"])
def apply_context_strategy(ctx):
    name = ctx.config.get("name")
    if not name:
        raise ValueError("context_strategy 条目缺少 config.name")
    ref = ctx.config.get("strategy")
    if ref:
        from .tool_plugin import import_attr

        factory = lambda: import_attr(ref)()  # noqa: E731
    else:
        factory = _default_factory(name)
    ctx.context.register_strategy(name, factory)


__all__ = ["apply_context_strategy"]
