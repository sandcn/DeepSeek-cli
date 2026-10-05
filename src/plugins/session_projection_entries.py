"""会话投影条目插件 — 清单中每个内置投影一个独立插件条目。

「一切皆插件」：内置会话投影（turnBoundary）不再硬编码在
``ctx.session_projections`` 服务构造里，而是由清单中的独立条目声明::

    - id: session_projection_turn_boundary
      plugin: src.plugins.session_projection_entries:apply_projection
      config:
        name: turnBoundary                 # 内置投影 id（可被 patch/overlay 定位）
        # folder: my_pkg.folder             # 可选：替换折叠函数（点分路径）
        # initial: my_pkg.initial           # 可选：替换初值工厂（点分路径）

插件挂载时把该 id 的内置投影注册进投影注册表（``folder=None`` 用默认实现）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置投影
随之缺席（``session_projections`` 聚合插件经 ``managed_session_projections``
抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _resolve_ref(config: dict, key: str) -> Optional[Callable]:
    ref = config.get(key)
    if not ref:
        return None
    from .tool_plugin import import_attr

    return import_attr(ref)


@plugin("session_projection", inject=["session_projections"])
def apply_projection(ctx):
    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("session_projection 条目缺少 config.name")
    folder = _resolve_ref(ctx.config, "folder")
    initial = _resolve_ref(ctx.config, "initial")
    undo = ctx.session_projections.register_builtin(name, folder, initial)
    ctx.effect(lambda: undo)


__all__ = ["apply_projection"]
