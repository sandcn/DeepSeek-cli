"""配置管理命令 — 处理 config 子命令（list/show/get/set/reset）

从 app_init.py 拆分而来。CLI 形态的配置显示/编辑（TUI 内 /config 命令的
独立界面见 ``core/commands/_config_cmd.py``；本模块复用 ``view_model``
纯逻辑构建/解析，与 TUI 界面同一数据源）。
"""

from __future__ import annotations

import argparse
import logging

from ..core.constants import CYAN, DIM, RESET, YELLOW, GREEN
from ..tui.events.consumers import publish_output
from ..config.view_model import (
    build_config_entries,
    format_config_text,
    resolve_config_key,
    parse_config_value,
    format_config_value,
)
from ..config.defaults import CONFIG_KEYS, DEFAULTS, RC_FILE

_logger = logging.getLogger(__name__)


def _handle_config_command(args: argparse.Namespace) -> None:
    """处理 config 子命令（list/show/get/set/reset）。

    无子命令（``python chat.py config``）与 list/show：显示全部配置文本。
    """
    sub = args.config_cmd

    if sub in (None, 'list', 'show'):
        text = format_config_text(build_config_entries(), rc_file=RC_FILE)
        publish_output("\n" + text, level="raw")
        return

    if sub == 'get':
        _cli_get(args.key)
        return
    if sub == 'set':
        _cli_set(args.key, args.value)
        return
    if sub == 'reset':
        _cli_reset(args.key)
        return

    publish_output(f"\n{YELLOW}  ! 未知的 config 命令: {sub}{RESET}", level="raw")
    publish_output(f"{DIM}  可用命令: list, show, get <键>, set <键> <值>, reset <键>{RESET}", level="raw")


def _cli_get(key_input: str) -> None:
    """查询单个配置。"""
    key = resolve_config_key(key_input)
    if key is None:
        publish_output(f"\n{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw")
        publish_output(f"{DIM}  使用 chat.py config list 查看全部配置键{RESET}", level="raw")
        return
    entry = next((e for e in build_config_entries() if e["key"] == key), None)
    if entry is None:
        publish_output(f"\n{YELLOW}  ! 未找到配置项: {key_input}{RESET}", level="raw")
        return
    publish_output(
        f"\n{CYAN}  {entry['path']}{RESET} = {entry['value_text']}"
        f"  {DIM}(默认: {entry['default_text']}){RESET}",
        level="raw",
    )
    if entry.get("desc"):
        publish_output(f"  {DIM}  {entry['desc']}{RESET}", level="raw")


def _cli_set(key_input: str, value_text: str) -> None:
    """设置单个配置并持久化。"""
    key = resolve_config_key(key_input)
    if key is None:
        publish_output(f"\n{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw")
        publish_output(f"{DIM}  使用 chat.py config list 查看全部配置键{RESET}", level="raw")
        return
    entry = next((e for e in build_config_entries() if e["key"] == key), None)
    if entry is None:
        publish_output(f"\n{YELLOW}  ! 未找到配置项: {key_input}{RESET}", level="raw")
        return
    value, err = parse_config_value(entry["type"], value_text)
    if err:
        publish_output(f"\n{YELLOW}  ! {entry['path']}: {err}{RESET}", level="raw")
        return
    try:
        from ..config.loader import update_config
        update_config(key, value)
    except Exception as e:
        publish_output(f"\n{YELLOW}  ! 写入配置失败: {e}{RESET}", level="raw")
        return
    shown = format_config_value(
        value, entry["type"], sensitive=bool(entry.get("sensitive")),
    )
    publish_output(f"\n{GREEN}  ✓ 已设置 {entry['path']} = {shown}{RESET}", level="raw")


def _cli_reset(key_input: str) -> None:
    """重置单个配置为默认值。"""
    key = resolve_config_key(key_input)
    if key is None:
        publish_output(f"\n{YELLOW}  ! 未找到配置键: {key_input}{RESET}", level="raw")
        publish_output(f"{DIM}  使用 chat.py config list 查看全部配置键{RESET}", level="raw")
        return
    if key in CONFIG_KEYS:
        default = CONFIG_KEYS[key]["default"]
        typ = CONFIG_KEYS[key]["type"]
    else:
        default = DEFAULTS.get(key)
        typ = type(default) if default is not None else str
    try:
        from ..config.loader import update_config
        update_config(key, default)
    except Exception as e:
        publish_output(f"\n{YELLOW}  ! 写入配置失败: {e}{RESET}", level="raw")
        return
    shown = format_config_value(default, typ)
    publish_output(f"\n{GREEN}  ✓ 已重置 {key} = {shown} (默认){RESET}", level="raw")
