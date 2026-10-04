"""插件管理命令 — 处理 plugin 子命令（list/add/remove）。

对应 dsh 的 ``dsh plugin add/list``：列出内置/外部/entry-point 插件，
把本地插件安装进 ``~/.chat_config/plugins/`` 或卸载。
"""

from __future__ import annotations

import argparse
import logging

from ..core.constants import CYAN, DIM, GREEN, RESET, YELLOW
from ..tui.events.consumers import publish_output
from ..plugins.manager import (
    add_plugin,
    format_plugins,
    list_plugins,
    remove_plugin,
)

_logger = logging.getLogger(__name__)


def _handle_plugin_command(args: argparse.Namespace) -> None:
    sub = getattr(args, "plugin_cmd", None)

    if sub in (None, "list"):
        profile = getattr(args, "profile", "") or "cli"
        summary = list_plugins(profile)
        publish_output("\n" + format_plugins(summary), level="raw")
        return

    if sub == "add":
        try:
            name = add_plugin(args.source, name=getattr(args, "name", "") or "")
        except Exception as exc:
            publish_output(f"\n{YELLOW}  ! 安装失败: {exc}{RESET}", level="raw")
            return
        publish_output(f"\n{GREEN}  ✓ 已安装插件: {name}{RESET}", level="raw")
        publish_output(f"{DIM}  下次启动自动发现并挂载（~/.chat_config/plugins）{RESET}", level="raw")
        return

    if sub == "remove":
        try:
            removed = remove_plugin(args.name)
        except Exception as exc:
            publish_output(f"\n{YELLOW}  ! 卸载失败: {exc}{RESET}", level="raw")
            return
        if removed:
            publish_output(f"\n{GREEN}  ✓ 已卸载插件: {args.name}{RESET}", level="raw")
        else:
            publish_output(f"\n{YELLOW}  ! 未找到插件: {args.name}{RESET}", level="raw")
        return

    publish_output(f"\n{YELLOW}  ! 未知的 plugin 命令: {sub}{RESET}", level="raw")
    publish_output(f"{DIM}  可用命令: list, add <路径>, remove <名称>{RESET}", level="raw")
