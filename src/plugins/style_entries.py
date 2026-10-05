"""命名样式条目插件 — 清单中每个内置命名样式一条独立条目。

「一切皆插件」：内置命名样式集（dim/bold/error/success/diff_add/... ）不再
硬编码在 ``tui.core.style`` 的模块级字典里，而是由清单中的独立条目声明::

    - id: named_style_error
      plugin: src.plugins.style_entries:apply_named_style
      config:
        name: error                           # 内置样式名（可被 patch/overlay 定位）

插件挂载时把该名称的内置样式注册进命名样式注册表（``style=None`` 用默认声明）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置样式随之
缺席（``named_styles`` 聚合插件经 ``managed_named_styles`` 抑制默认装配）。

样式值为 ``Style`` 运行时对象，无法经 YAML 表达——覆盖/替换由外部 Python 插件
经 ``ctx.named_styles.register(name, style)`` 完成。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("named_style")
def apply_named_style(ctx):
    from ..tui.core.style import register_builtin_style

    name = ctx.config.get("name")
    if not name:
        raise ValueError("named_style 条目缺少 config.name")
    value = ctx.config.get("style")
    undo = register_builtin_style(name, value)
    ctx.effect(lambda: undo)


__all__ = ["apply_named_style"]
