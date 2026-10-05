"""CLI 子命令条目插件 — 清单中每个顶层子命令一个独立插件条目。

「一切皆插件」：``chat.py`` 的顶层子命令（version/dump-config/plugin/session/
config/check-invariants/clawbot）不再硬编码在 ``AppService._dispatch`` 里，而是
由清单中的独立条目声明::

    - id: subcommand_version
      plugin: src.plugins.subcommand_entries:apply_subcommand
      config:
        name: version                     # 内置子命令 id（可被 patch/overlay 定位）
        # description: 显示版本信息        # 可选：覆盖说明
        # phase: pre                       # 可选：pre / post（MCP 前/后）
        # order: 0                         # 可选：同阶段内判定顺序
        # hidden: false                    # 可选：隐藏标记
        # handler: my_pkg.run               # 可选：替换处理器（点分路径）
        # matcher: my_pkg.match             # 可选：替换判定（点分路径）

插件挂载时把该子命令注册进 ``src.app_init.subcommands`` 注册表（``command=None``
用默认声明/处理器/判定）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用
即不挂载，对应子命令随之缺席（``app`` 聚合插件经 ``managed_subcommands`` 抑制
默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin

#: 可由条目 config 覆盖的字段
_SPEC_FIELDS = ("description", "phase", "order", "hidden")


def _subcommand_from_config(name: str, config: dict):
    from ..app_init.subcommands import Subcommand, default_subcommand

    base = default_subcommand(name)
    overrides = {key: config[key] for key in _SPEC_FIELDS if key in config}
    matcher = base.matcher
    handler = base.handler
    if config.get("matcher") or config.get("handler"):
        from .tool_plugin import import_attr

        matcher = import_attr(config["matcher"]) if config.get("matcher") else matcher
        handler = import_attr(config["handler"]) if config.get("handler") else handler
    if not overrides and matcher is base.matcher and handler is base.handler:
        return None
    data = base.to_dict()
    data.update(overrides)
    return Subcommand(
        name=name,
        description=str(data.get("description", "")),
        phase=str(data.get("phase", base.phase)),
        order=int(data.get("order", 0)),
        matcher=matcher,
        handler=handler,
        hidden=bool(data.get("hidden", False)),
    )


@plugin("subcommand")
def apply_subcommand(ctx):
    from ..app_init.subcommands import register_builtin_subcommand

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("subcommand 条目缺少 config.name")
    undo = register_builtin_subcommand(name, _subcommand_from_config(name, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_subcommand"]
