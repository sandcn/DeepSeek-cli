"""UI 视图条目插件 — 清单中每个内置 TUI 视图一个独立插件条目。

「一切皆插件」：内置 TUI 视图（全屏 trace/trace_tools/config/plugin，底部
user_select/editmsg）不再硬编码在 ``app.py`` 的字典字面量里，而是由清单中的
独立条目声明::

    - id: ui_view_trace
      plugin: src.plugins.ui_views:apply_ui_view
      config:
        id: trace                         # 内置视图 id（可被 patch/overlay 定位）
        # component: my_pkg.MyView        # 可选：替换实现（点分路径）
        # kind: fullscreen                # 可选：覆盖类型（fullscreen/bottom）
        # key_mode: static                # 可选：底部视图 fiber key 模式

插件挂载时把该视图注册进 UI 视图注册表（``spec=None`` 用默认声明）；卸载时撤销
（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置视图随之缺席
（``ui`` 聚合插件经 ``managed_ui_views`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin


def _spec_from_config(spec_id: str, config: dict) -> Optional[object]:
    from ..tui.app.view_registry import ViewSpec, default_view_spec

    overrides = {k: config[k] for k in ("component", "kind", "key_mode", "description") if k in config}
    if not overrides:
        return None
    try:
        base = default_view_spec(spec_id)
    except KeyError:
        base = ViewSpec(id=spec_id, kind="fullscreen", component="")
    data = base.to_dict()
    data.update(overrides)
    return ViewSpec(
        id=spec_id,
        kind=str(data.get("kind", "fullscreen")),
        component=data.get("component"),
        key_mode=str(data.get("key_mode", "")),
        description=str(data.get("description", "")),
    )


@plugin("ui_view")
def apply_ui_view(ctx):
    from ..tui.app.view_registry import register_builtin_view

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("ui_view 条目缺少 config.id")
    undo = register_builtin_view(spec_id, _spec_from_config(spec_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_ui_view"]
