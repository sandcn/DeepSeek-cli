"""不变量条目插件 — 清单中每条内置检查一个独立插件条目。

「一切皆插件」：``ctx.invariants`` 的 42 条内置检查不再硬编码在插件内部，
而是由清单中的独立条目声明::

    - id: invariant_services_keys_valid
      plugin: src.plugins.invariant_entries:apply_invariant
      config:
        name: services.keys_valid          # 内置检查 id（可被 patch/overlay 定位）
        # check: my_pkg.checks:my_check     # 可选：替换检查实现（点分引用）

插件挂载时把该 id 的内置检查注册进不变量注册表（``check`` 缺省用内置声明，
惰性解析）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，
对应内置检查随之缺席（``invariants`` 聚合插件经 ``managed_invariants`` 抑制
默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin


@plugin("invariant")
def apply_invariant(ctx):
    from ..kernel.invariant_registry import register_builtin_check

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("invariant 条目缺少 config.name")
    check_ref = ctx.config.get("check")
    if check_ref:
        from .tool_plugin import import_attr

        undo = register_builtin_check(name, import_attr(str(check_ref)))
    else:
        undo = register_builtin_check(name)
    ctx.effect(lambda: undo)


__all__ = ["apply_invariant"]
