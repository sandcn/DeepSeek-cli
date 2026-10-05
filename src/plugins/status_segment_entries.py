"""状态栏段条目插件 — 清单中每个内置段一个独立插件条目。

「一切皆插件」：StatusBar 的各个信息段（model/tools/elapsed/tokens/speed）不再
硬编码在 ``status_bar._build_status_runs`` 里，而是由清单中的独立条目声明::

    - id: status_segment_model
      plugin: src.plugins.status_segment_entries:apply_status_segment
      config:
        id: model                           # 内置段 id（可被 patch/overlay 定位）
        # order: 0                          # 可选：覆盖显示顺序
        # handler: my_pkg:my_segment        # 可选：替换实现（点分引用）

插件挂载时把该 id 的内置段注册进注册表（``spec=None`` 用默认规格）；卸载时
撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置段随之缺席
（``status_segments`` 聚合插件经 ``managed_status_segments`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: status_segment 条目可由 config 覆盖的字段
_SPEC_FIELDS = ("order", "handler")


def _spec_from_config(spec_id: str, config: dict):
    from ..tui.app._status_segments import StatusSegment, default_segment

    if not any(field in config for field in _SPEC_FIELDS):
        return None
    base = default_segment(spec_id)
    return StatusSegment(
        id=spec_id,
        order=int(config.get("order", base.order)),
        handler=str(config.get("handler", base.handler)),
    )


@plugin("status_segment")
def apply_status_segment(ctx):
    from ..tui.app._status_segments import register_builtin_segment

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("status_segment 条目缺少 config.id")
    undo = register_builtin_segment(spec_id, _spec_from_config(spec_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_status_segment"]
