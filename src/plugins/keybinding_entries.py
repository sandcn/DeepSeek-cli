"""键位绑定条目插件 — 清单中每个内置 Ctrl 绑定一个独立插件条目。

「一切皆插件」：TUI 输入分发的 Ctrl 组合键绑定（vim/editmsg/trace_toggle/
ctrl_r/clear_screen/ctrl_d/toggle_theme/switch_model/history_prev/cycle_mode）
不再硬编码在 ``InputDispatcher._handle_ctrl_key`` 的 if/elif 链里，而是由清单
中的独立条目声明::

    - id: keybinding_ctrl_g
      plugin: src.plugins.keybinding_entries:apply_keybinding
      config:
        id: ctrl_g                          # 内置绑定 id（可被 patch/overlay 定位）
        # key: "\\x07"                      # 可选：覆盖按键（控制字符）
        # action: vim                       # 可选：覆盖分发动作
        # description: ...                  # 可选：覆盖说明

插件挂载时把该 id 的内置绑定注册进键位注册表（``spec=None`` 用默认规格）；
卸载时撤销（可逆副作用）。条目被 Patch/Overlay 禁用即不挂载，对应内置绑定
随之缺席（``keybindings`` 聚合插件经 ``managed_keybindings`` 抑制默认装配）。
"""

from __future__ import annotations

from typing import Optional

from ..kernel import plugin

#: keybinding 条目可由 config 覆盖的字段
_BINDING_FIELDS = ("key", "action", "description")


def _binding_from_config(spec_id: str, config: dict):
    from ..tui._keybindings import KeyBinding, default_keybinding

    if not any(field in config for field in _BINDING_FIELDS):
        return None
    base = default_keybinding(spec_id)
    return KeyBinding(
        id=spec_id,
        key=str(config.get("key", base.key)),
        action=str(config.get("action", base.action)),
        description=str(config.get("description", base.description)),
    )


@plugin("keybinding")
def apply_keybinding(ctx):
    from ..tui._keybindings import register_builtin_keybinding

    spec_id = ctx.config.get("id")
    if not spec_id:
        raise ValueError("keybinding 条目缺少 config.id")
    undo = register_builtin_keybinding(spec_id, _binding_from_config(spec_id, ctx.config))
    ctx.effect(lambda: undo)


__all__ = ["apply_keybinding"]
