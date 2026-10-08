"""keymap 命令 — ``/keymap`` 键位自定义编辑器（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「键位编辑器」（``model.fullscreen == "keymap"``）；
无 ChatUI（单次模式 / 测试桩）回退文本列出当前生效的 Ctrl 组合键绑定。

改键经视图 ``applied_seq`` 回传：注册运行时覆盖（``register_builtin_keybinding``）
并持久化到配置 ``keybindings_overrides``（``{绑定 id: 组合键文本}``）——由
``KeybindingsService`` 启动时读取应用，重启后仍生效。
"""

from __future__ import annotations

import logging

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _build_keymap_entries() -> list:
    """当前生效的键位绑定 → 编辑器条目列表（经 ui_runtime 桥接，不直连 tui）。"""
    try:
        from ..adapters.ui_runtime import list_keybindings

        return list_keybindings()
    except Exception:
        return []


def _apply_override(spec_id: str, combo: str) -> bool:
    """应用并持久化一次改键（注册运行时覆盖 + 写配置）。"""
    try:
        from ..adapters.ui_runtime import register_keybinding_override

        if not register_keybinding_override(spec_id, combo):
            return False
    except Exception:
        return False
    try:
        from ...config.loader import update_config
        from ...config.proxy import config as _cfg

        cur = _cfg.get("keybindings_overrides", {}) or {}
        if not isinstance(cur, dict):
            cur = {}
        cur[spec_id] = combo
        update_config("keybindings_overrides", cur)
    except Exception:
        _logger.debug("持久化改键失败: %s", spec_id, exc_info=True)
    return True


def _open_keymap_ui(ctx) -> bool:
    """打开全屏键位编辑器视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_keymap_view_state_cls
    from ._view_opener import open_fullscreen_view

    applied_seq = {"v": 0}

    def setup(model, state):
        state.entries = _build_keymap_entries()

    def tick(state) -> bool:
        if state.applied_seq > applied_seq["v"]:
            applied_seq["v"] = state.applied_seq
            report = state.applied or {}
            spec_id = str(report.get("id", "") or "")
            combo = str(report.get("combo", "") or "")
            ok = _apply_override(spec_id, combo) if spec_id else False
            state.status_message = (
                f"已改键：{spec_id} \u2192 {combo}" if ok else f"无效组合键：{combo}"
            )
            state.entries = _build_keymap_entries()
        return False

    return open_fullscreen_view(
        ctx, view_id="keymap", state_attr="keymap_view",
        state_cls=get_keymap_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="键位编辑器已关闭", timeout_hint="键位编辑器超时关闭",
    )


def _keymap_text() -> bool:
    """文本列出当前生效的键位绑定（无 ChatUI / 单次模式回退）。"""
    entries = _build_keymap_entries()
    if not entries:
        _out.write(f"{YELLOW}  ! 无可用键位绑定{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 键位绑定{RESET}", level="raw", source="cmd")
    for e in entries:
        combo = e.get("combo", "") or e.get("key", "")
        _out.write(
            f"  {CYAN}{combo:<12}{RESET} {e.get('action', '')}  {DIM}{e.get('description', '')}{RESET}",
            level="raw", source="cmd",
        )
    _out.write(f"  {DIM} 使用: /keymap 打开编辑器{RESET}", level="raw", source="cmd")
    return True


def _cmd_keymap(ctx) -> bool:
    """打开键位编辑器（有 ChatUI）或文本列出（回退）。"""
    if _open_keymap_ui(ctx):
        return True
    return _keymap_text()


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class KeymapCommand(CommandPlugin):
    """键位自定义编辑器（/keymap）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="keymap", description="键位自定义编辑器（查看 / 改键）", group="ui",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_keymap(ctx)


declare_command_plugin(KeymapCommand())
