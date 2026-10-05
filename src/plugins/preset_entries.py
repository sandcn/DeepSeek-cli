"""Preset 条目插件 — 清单中每个内置 preset 一个独立插件条目。

「一切皆插件」：内置 preset（standard/minimal/code）不再硬编码在
``src.plugins.presets`` 里，而是由清单中的独立条目声明::

    - id: preset_minimal
      plugin: src.plugins.preset_entries:apply_preset
      config:
        name: minimal                      # 内置 preset id（可被 patch/overlay 定位）
        # description: 最小模式             # 可选：覆盖描述
        # tool_includes: [read_file]        # 可选：覆盖包含集合
        # tool_excludes: [bash]             # 可选：覆盖排除集合
        # model: ""                         # 可选：模型覆盖
        # preset: my_pkg.MyPreset           # 可选：替换实现（点分路径 / 工厂）

插件挂载时把该 id 的内置 preset 注册进 ``src.core.presets`` 注册表（``preset=None``
用默认声明）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置 preset 随之缺席（``presets`` 聚合插件经 ``managed_presets`` 抑制默认装配）。
"""

from __future__ import annotations

from ..kernel import plugin

#: 可由条目 config 覆盖的规格字段
_SPEC_FIELDS = ("description", "tool_excludes", "tool_includes", "model")


def _preset_from_config(name: str, config: dict):
    from ..core.presets import Preset, default_preset

    overrides = {key: config[key] for key in _SPEC_FIELDS if key in config}
    if not overrides:
        return None
    try:
        base = default_preset(name)
    except KeyError:
        base = Preset(name=name)
    data = base.to_dict()
    data.update(overrides)
    return Preset(
        name=name,
        description=str(data.get("description", "")),
        tool_excludes=tuple(str(item) for item in (data.get("tool_excludes") or ())),
        tool_includes=tuple(str(item) for item in (data.get("tool_includes") or ())),
        model=str(data.get("model", "")),
    )


def _override_from_ref(ref: str):
    from .tool_plugin import import_attr

    target = import_attr(ref)
    if callable(target) and not hasattr(target, "allows"):
        return target()
    return target


@plugin("preset")
def apply_preset(ctx):
    from ..core.presets import Preset, register_builtin_preset

    name = ctx.config.get("name") or ctx.config.get("id")
    if not name:
        raise ValueError("preset 条目缺少 config.name")
    ref = ctx.config.get("preset")
    if ref:
        override = _override_from_ref(ref)
    else:
        override = _preset_from_config(name, ctx.config)
    if override is not None and not isinstance(override, Preset):
        raise TypeError(f"preset 覆盖必须是 Preset 实例: {override!r}")
    undo = register_builtin_preset(name, override)
    ctx.effect(lambda: undo)


__all__ = ["apply_preset"]
