"""host 组件条目插件 — 清单中每个内置 host 一个独立插件条目。

「一切皆插件」：内置 host（``static-lines``）的 measure/paint 不再由
``staticlines`` 模块导入期的一次性副作用注册，而是由清单中的独立条目声明::

    - id: host_static_lines
      plugin: src.plugins.host_entries:apply_host
      config:
        id: static-lines                    # 内置 host tag（可被 patch/overlay 定位）
        # measure: my_pkg:my_measure        # 可选：替换 measure 实现（点分引用）
        # paint: my_pkg:my_paint            # 可选：替换 paint 实现

插件挂载时把该 tag 的内置 host 注册进 host 注册表（``pair=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置 host 随之缺席（``hosts`` 聚合插件经 ``managed_hosts`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..kernel import plugin


def _pair_from_config(config: dict) -> Optional[Tuple]:
    measure_ref = config.get("measure")
    paint_ref = config.get("paint")
    if not measure_ref and not paint_ref:
        return None
    from .tool_plugin import import_attr

    measure = import_attr(measure_ref) if measure_ref else None
    paint = import_attr(paint_ref) if paint_ref else None
    return (measure, paint)


@plugin("host")
def apply_host(ctx):
    from ..tui.ink.registry import default_host, register_builtin_host

    tag = ctx.config.get("id")
    if not tag:
        raise ValueError("host 条目缺少 config.id")
    pair = _pair_from_config(ctx.config)
    if pair is not None:
        base = default_host(tag)
        pair = (pair[0] or base[0], pair[1] or base[1])
    undo = register_builtin_host(tag, pair)
    ctx.effect(lambda: undo)


__all__ = ["apply_host"]
