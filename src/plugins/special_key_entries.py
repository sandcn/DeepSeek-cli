"""特殊键处理器条目插件 — 清单中每个内置 action 一个独立插件条目。

「一切皆插件」：``make_special_key_callback`` 的 action 处理器（vim / editmsg /
retry / toggle_theme / switch_model / cycle_mode）不再硬编码在
``src.app_loop._special_keys`` 的 if/elif 链里，而是由清单中的独立条目声明::

    - id: special_key_vim
      plugin: src.plugins.special_key_entries:apply_special_key
      config:
        id: vim                                   # 内置 action（可被 patch/overlay 定位）
        # handler: my_pkg:my_builder              # 可选：替换处理器工厂（点分引用）

插件挂载时把该 action 的处理器工厂注册进注册表（``factory=None`` 用默认
实现）；卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应
内置处理器随之缺席（``special_keys`` 聚合插件经 ``managed_special_keys``
抑制默认装配；缺失的 action 在回调中返回 None，等价未知 action）。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..kernel import plugin


def _factory_from_config(config: dict) -> Optional[Callable]:
    ref = config.get("handler")
    if not ref:
        return None

    def _builder(env):
        from .tool_plugin import import_attr

        return import_attr(ref)(env)

    _builder.__name__ = f"_override_{ref}"
    return _builder


@plugin("special_key")
def apply_special_key(ctx):
    from ..app_loop._special_handlers import register_builtin_special_key

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("special_key 条目缺少 config.id")
    undo = register_builtin_special_key(spec_id, _factory_from_config(ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_special_key"]
