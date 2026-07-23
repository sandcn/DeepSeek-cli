"""会话管理命令 — 处理 session 子命令（list/delete/export）

从 app_init.py 拆分而来。
"""

from __future__ import annotations

import argparse
import logging

from ..chat_msgs import list_sessions, delete_session, export_session
from ..core.constants import CYAN, DIM, RESET, YELLOW, GREEN
from ..tui.events.consumers import publish_output

_logger = logging.getLogger(__name__)


def _handle_session_command(args: argparse.Namespace) -> None:
    """处理 session 子命令（list/delete/export）"""
    if args.session_cmd == 'list':
        sessions = list_sessions()
        if not sessions:
            publish_output(f"\n{DIM}  没有保存的会话{RESET}", level="raw")
            return
        publish_output(f"\n{CYAN}  > 已保存的会话:{RESET}", level="raw")
        for s in sessions:
            title = s.get("title", "")
            title_info = f"「{title}」 " if title else ""
            publish_output(f"    {DIM}{s['id']}  {title_info}{s['model']}  {s['message_count']}条消息  {s['saved_at']}{RESET}", level="raw")
        publish_output("", level="raw")

    elif args.session_cmd == 'delete':
        ok = delete_session(args.session_id)
        if ok:
            publish_output(f"\n{GREEN}  ✓ 会话已删除: {args.session_id}{RESET}", level="raw")
        else:
            publish_output(f"\n{YELLOW}  ! 未找到会话: {args.session_id}{RESET}", level="raw")

    elif args.session_cmd == 'export':
        result = export_session(args.session_id, output=args.output)
        if result is None:
            publish_output(f"\n{YELLOW}  ! 未找到会话: {args.session_id}{RESET}", level="raw")
        elif args.output:
            publish_output(f"\n{GREEN}  ✓ 会话已导出到: {result}{RESET}", level="raw")
        else:
            publish_output(result, level="raw")

    else:
        publish_output(f"\n{YELLOW}  ! 未知的 session 命令: {args.session_cmd}{RESET}", level="raw")
        publish_output(f"{DIM}  可用命令: list, delete <id>, export <id> [-o 文件路径]{RESET}", level="raw")
