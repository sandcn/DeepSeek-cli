"""工具执行引擎条目插件 — 清单中每个内置执行引擎一个独立插件条目。

「一切皆插件」：工具批次的执行策略（dag/serial/parallel）不再硬编码在
``ToolScheduler.schedule`` 里，而是由清单中的独立条目声明::

    - id: tool_engine_dag
      plugin: src.plugins.tool_engine_entries:apply_tool_engine
      config:
        id: dag                          # 内置引擎 id（可被 patch/overlay 定位）
        # engine: my_pkg.MyEngine         # 可选：替换实现（点分路径）

插件挂载时把该引擎注册进工具执行引擎注册表（``factory=None`` 用默认实现）；卸载
时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置引擎随之缺席
（``tool_scheduler`` 聚合插件经 ``managed_tool_engines`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[], Any]]:
    ref = config.get("engine")
    if not ref:
        return None

    def _factory():
        from .tool_plugin import import_attr

        return import_attr(ref)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("tool_engine")
def apply_tool_engine(ctx):
    from ..core.tool_engines import register_builtin_tool_engine

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("tool_engine 条目缺少 config.id")
    undo = register_builtin_tool_engine(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_tool_engine"]
