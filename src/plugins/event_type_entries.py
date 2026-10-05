"""事件类型条目插件 — 清单中每个内置事件类型一条独立条目。

「一切皆插件」：事件类型（核心事件类型字符串常量 + 显示事件类型类）不再硬编码
在 ``event_types`` / ``display_types`` 模块里，而是由清单中的独立条目声明::

    - id: event_type_core_model_call_started
      plugin: src.plugins.event_type_entries:apply_event_type
      config:
        name: core::MODEL_CALL_STARTED        # 复合 id（域::名称）
        # value: model.call.started           # 可选：覆盖核心事件类型字符串

插件挂载时把该复合 id 的内置事件类型注册进注册表（``value=None`` 用默认声明）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置事件类型
随之缺席（``event_types`` 聚合插件经 ``managed_event_types`` 抑制默认装配）。
"""

from __future__ import annotations

import importlib

from ..kernel import plugin

#: 声明模块（按域）——条目独立挂载顺序不确定，注册前按需导入声明模块。
_DOMAIN_DECLARERS = {
    "core": "src.core.events.event_types",
    "display": "src.core.events.display_types",
    "session": "src.core.events.agent_types",
    "agent": "src.core.events.agent_types",
    "capability": "src.core.events.agent_types",
}


@plugin("event_type")
def apply_event_type(ctx):
    from ..core.events.type_registry import (
        builtin_composite_ids,
        register_builtin_event,
        split_id,
    )

    name = ctx.config.get("name")
    if not name:
        raise ValueError("event_type 条目缺少 config.name")
    if name not in builtin_composite_ids():
        domain, _ = split_id(name)
        module_name = _DOMAIN_DECLARERS.get(domain)
        if module_name:
            try:
                importlib.import_module(module_name)
            except Exception:  # noqa: BLE001 - 导入失败由下方未知类型报错
                pass
    if name not in builtin_composite_ids():
        raise ValueError(f"未知内置事件类型: {name!r}")
    value = ctx.config.get("value")
    undo = register_builtin_event(name, value)
    ctx.effect(lambda: undo)


__all__ = ["apply_event_type"]
