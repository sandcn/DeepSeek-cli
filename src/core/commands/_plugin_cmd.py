"""plugin 命令 — /plugin 打开插件总览独立界面。

用户需求（2026-10-04）：执行 ``/plugin`` 后左边显示所有插件名称，右边显示
选中插件的详细信息。数据由 ``plugins.view_model.build_plugin_entries``
汇总四类来源（内核运行时 Fiber / 内置清单条目 / 命令插件 / 外部与已安装
插件）；有活跃 ChatUI 时打开**全屏插件总览界面**（PluginView——
``model.fullscreen = "plugin"``，左右布局 + ↑↓ 选择 + Esc 关闭），无 ChatUI
（单次模式/测试桩）时回退文本显示。
"""

from __future__ import annotations

import logging
import time as _time

from ..constants import DIM, RESET, YELLOW
from ..adapters.output import get_default_output_port
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _show_plugin_text(ctx) -> bool:
    """文本显示插件总览（无 ChatUI / 单次模式回退共用）。"""
    from ...plugins.view_model import build_plugin_entries, format_plugin_text

    text = format_plugin_text(build_plugin_entries())
    _out.write("\n" + text, level="raw", source="cmd")
    return True


def _open_plugin_ui(ctx) -> bool:
    """打开全屏插件总览界面（PluginView 模态全屏视图）。

    协议（与 ``_config_cmd._open_config_ui`` 同构）：设置
    ``model.plugin_view``（visible=True, seq+1, entries）→
    ``model.fullscreen = "plugin"`` → request_bottom_redraw → 轮询
    ``state.done``（deadline 超时兜底）→ finally 清理（重置 plugin_view
    保留 seq + fullscreen 置空 + request_bottom_redraw + flush router）。

    无活跃 ChatUI / 模型不可用（单次模式、测试桩）返回 False——调用方
    回退文本显示。
    """
    try:
        from ..adapters.ui_runtime import get_active_chat_ui

        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return False
        model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        if model is None or not hasattr(model, "plugin_view"):
            return False
    except Exception:
        return False

    from ...plugins.view_model import build_plugin_entries
    from ..adapters.ui_runtime import get_plugin_view_state_cls

    PluginViewState = get_plugin_view_state_cls()
    entries = build_plugin_entries()
    prev_seq = getattr(model.plugin_view, "seq", 0)
    state = PluginViewState(
        visible=True,
        seq=prev_seq + 1,
        entries=entries,
        # 超时兜底（默认 600s=10 分钟）：用户长时间无操作自动关闭，
        # 命令线程不永久阻塞（与 _open_config_ui 语义同源）。
        deadline=_time.monotonic() + 600,
    )
    model.plugin_view = state
    model.fullscreen = "plugin"
    try:
        chat_ui.request_bottom_redraw()
    except Exception:
        pass

    try:
        while not state.done:
            if _time.monotonic() >= state.deadline:
                # 超时：原子终态写入（first-write-wins——组件恰在临界窗口
                # 已关闭则放弃覆盖，保留组件结果）
                state.try_set_final("timeout")
                break
            _time.sleep(0.05)
        if state.action == "timeout":
            _out.write(f"{YELLOW}  ! 插件界面超时关闭{RESET}", level="raw", source="cmd")
        else:
            _out.write(f"{DIM}  插件界面已关闭{RESET}", level="raw", source="cmd")
        return True
    finally:
        # 清理：重置 plugin_view（**保留当前 seq**——重新读取清理前的
        # ``model.plugin_view.seq``，保证 seq 单调递增 → App key 永不重复 →
        # 调和器每次强制重挂载，不残留旧选中/旧滚动）+ 仅当仍占用 fullscreen
        # 时置空（用户可能已切换到其他全屏视图）+ request_bottom_redraw +
        # flush router（与 _open_config_ui 同构的 identity 比较防御）。
        try:
            if not state.done:
                state.try_set_final("timeout")
            if getattr(model, "plugin_view", None) is state:
                cur_seq = getattr(model.plugin_view, "seq", 0)
                model.plugin_view = PluginViewState(seq=cur_seq)
            if getattr(model, "fullscreen", "") == "plugin":
                model.fullscreen = ""
            try:
                chat_ui.request_bottom_redraw()
            except Exception:
                pass
            try:
                chat_ui.flush_input_router(2.0)
            except Exception:
                pass
        except Exception:
            _logger.debug("_open_plugin_ui cleanup 失败", exc_info=True)


def _cmd_plugin(ctx):
    """查看插件（/plugin）。

    - 无 ChatUI：文本显示插件总览（四类来源分组）。
    - 有 ChatUI：打开**全屏插件总览界面**（PluginView——左插件列表 +
      右详细信息 + ↑↓ 选择 + Esc 关闭）。
    """
    if _open_plugin_ui(ctx):
        return True
    return _show_plugin_text(ctx)


# ── CommandPlugin 子类 ──────────────────────────────
# 命令通过 get_plugin_registry().register() 注册，不再使用 register_command()。
# CommandPluginRegistry.register() 内部自动调用 register_command() 确保向后兼容。

from .base import CommandPlugin, CommandMeta, get_plugin_registry


class PluginCommand(CommandPlugin):
    """查看插件总览（独立界面）"""
    def __init__(self):
        self.meta = CommandMeta(
            name="plugin",
            description="查看插件（内核/清单/命令/外部，独立界面）",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_plugin(ctx)


# ── 自动注册插件 ────────────────────────────────────
get_plugin_registry().register(PluginCommand())
