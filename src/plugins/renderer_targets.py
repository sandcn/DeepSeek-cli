"""渲染目标条目插件 — 清单中每个内置目标一个独立插件条目。

「一切皆插件」：内置渲染目标（terminal/file）不再硬编码在
``src.renderer.targets`` 里，而是由清单中的独立条目声明::

    - id: renderer_target_terminal
      plugin: src.plugins.renderer_targets:apply_renderer_target
      config:
        id: terminal                       # 内置目标 id（可被 patch/overlay 定位）
        # target: my_pkg.MyRenderTarget    # 可选：替换实现（点分路径）

插件挂载时把该 id 的内置目标注册进渲染目标注册表（``target=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置目标随之缺席（``renderer`` 聚合插件经 ``managed_render_targets`` 抑制
默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[..., Any]]:
    ref = config.get("target")
    if not ref:
        return None

    def _factory(**kwargs):
        from .tool_plugin import import_attr

        return import_attr(ref)(**kwargs)

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("renderer_target", inject=["renderer"])
def apply_renderer_target(ctx):
    from ..renderer.targets.registry import register_builtin_render_target

    spec_id = ctx.config.get("id") or ctx.config.get("name")
    if not spec_id:
        raise ValueError("renderer_target 条目缺少 config.id")
    undo = register_builtin_render_target(spec_id, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_renderer_target"]
