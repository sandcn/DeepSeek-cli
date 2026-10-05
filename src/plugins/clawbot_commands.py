"""ClawBot 命令条目插件 — 清单中每个远程指令一个独立插件条目。

「一切皆插件」：微信远程控制的斜杠指令（/help /shell /clear /new /time /
/status /model /stop）不再硬编码在 ``runner._dispatch_cmd`` 里，而是由清单中的
独立条目声明::

    - id: clawbot_command_shell
      plugin: src.plugins.clawbot_commands:apply_clawbot_command
      config:
        name: shell                         # 内置命令 id（可被 patch/overlay 定位）
        # usage: /shell <命令>              # 可选：覆盖用法
        # description: 远程执行 shell 命令   # 可选：覆盖说明
        # aliases: [sh]                      # 可选：覆盖别名
        # order: 1                           # 可选：帮助文本顺序
        # hidden: false                      # 可选：从帮助文本隐藏
        # handler: my_pkg.handler           # 可选：替换处理器（点分路径）

插件挂载时把该命令注册进 ``src.clawbot.command_registry`` 注册表（``command=None``
用默认声明/处理器）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不
挂载，对应指令随之缺席（``clawbot`` 聚合插件经 ``managed_clawbot_commands``
抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin

#: 可由条目 config 覆盖的字段
_SPEC_FIELDS = ("usage", "description", "aliases", "order", "hidden")


def _command_from_config(name: str, config: dict):
    from ..clawbot.command_registry import ClawbotCommand, default_clawbot_command

    base = default_clawbot_command(name)
    overrides = {key: config[key] for key in _SPEC_FIELDS if key in config}
    handler = base.handler
    ref = config.get("handler")
    if ref:
        from .tool_plugin import import_attr

        handler = import_attr(ref)
    if not overrides and handler is base.handler:
        return None
    data = base.to_dict()
    data.update(overrides)
    return ClawbotCommand(
        name=name,
        usage=str(data.get("usage", "")),
        description=str(data.get("description", "")),
        aliases=tuple(str(item) for item in (data.get("aliases") or ())),
        order=int(data.get("order", 0)),
        hidden=bool(data.get("hidden", False)),
        handler=handler,
    )


@plugin("clawbot_command")
def apply_clawbot_command(ctx):
    from ..clawbot.command_registry import register_builtin_clawbot_command

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("clawbot_command 条目缺少 config.name")
    undo = register_builtin_clawbot_command(name, _command_from_config(name, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_clawbot_command"]
