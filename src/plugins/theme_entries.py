"""主题条目插件 — 清单中每个内置主题一个独立插件条目。

「一切皆插件」：内置主题（dark/light/high-contrast）不再硬编码在
``tui.core._theme`` 里，而是由清单中的独立条目声明::

    - id: theme_dark
      plugin: src.plugins.theme_entries:apply_theme
      config:
        name: dark                         # 内置主题 id（可被 patch/overlay 定位）
        # palette: my_pkg.MyPalette         # 可选：替换实现（点分路径/工厂）

插件挂载时把该 id 的内置主题注册进主题注册表（``palette=None`` 用默认实现）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置主题
随之缺席（``themes`` 聚合插件经 ``managed_themes`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from ..kernel import plugin


def _make_override_factory(config: dict) -> Optional[Callable[[], Any]]:
    ref = config.get("palette")
    if not ref:
        return None

    def _factory():
        from .tool_plugin import import_attr

        target = import_attr(ref)
        return target() if callable(target) else target

    _factory.__name__ = f"_override_{ref}"
    return _factory


@plugin("theme", inject=["themes"])
def apply_theme(ctx):
    from ..tui.core._theme import register_builtin_theme

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("theme 条目缺少 config.name")
    undo = register_builtin_theme(name, _make_override_factory(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_theme"]
