"""命令行参数解析 — 从 app_init.py 拆分而来

负责 argparse 参数定义、旧语法兼容和主题应用。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from ..config import config
# set_theme 已从 core/theme.py 移除 — 使用 no-op 存根
def _set_theme_noop(name: str) -> None:
    pass
set_theme = _set_theme_noop

_logger = logging.getLogger(__name__)

# ── 版本常量 ──
VERSION = "v2.2.0"


def _parse_args() -> argparse.Namespace:
    """解析命令行参数，支持子命令和旧语法自动兼容"""
    # 旧语法兼容：无参数或首个参数以 - 开头时，自动映射到对应子命令
    _OLD_FLAGS = {'--version', '-h', '--help'}
    # 创建局部副本，避免直接修改全局 sys.argv 产生副作用（B5 修复）
    argv = list(sys.argv)

    # 旧语法兼容：无参数或首个参数以 - 开头时，自动映射到对应子命令
    if len(argv) <= 1:
        argv.insert(1, 'run')
    elif argv[1] == '--webui':
        argv[1] = 'webui'
    elif argv[1].startswith('-') and argv[1] not in _OLD_FLAGS:
        argv.insert(1, 'run')

    parser = argparse.ArgumentParser(
        description='Chat 命令行助手 — AI 对话终端',
        epilog=(
            '使用示例:\n'
            '  python chat.py                     启动交互式对话\n'
            '  python chat.py -p "你好"           单次问答模式\n'
            '  python chat.py --load abc123       从会话恢复\n'
            '  python chat.py run --model xxx     指定模型启动\n'
            '  python chat.py session list        列出所有会话\n'
            '  python chat.py session delete xxx  删除会话\n'
            '  python chat.py session export xxx  导出会话\n'
            '  python chat.py version             查看版本\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── 全局选项（所有子命令共享） ──
    parser.add_argument('--version', action='store_true', help='显示版本信息并退出')
    parser.add_argument('-m', '--model', type=str, default='', help='指定模型（覆盖配置文件）')
    parser.add_argument('-v', '--verbose', action='count', default=0, help='增加日志详细度（-v INFO, -vv DEBUG）')

    # ── 子命令 ──
    subparsers = parser.add_subparsers(dest='command', title='子命令')

    # run — 交互/单次对话模式
    p_run = subparsers.add_parser('run', help='交互/单次对话模式（默认）')
    p_run.add_argument('-p', '--prompt', type=str, help='输入一句话，大模型回答完成后退出')
    p_run.add_argument('--load', type=str, help='从保存的会话 ID 恢复对话')

    # webui — Web UI 模式
    p_webui = subparsers.add_parser('webui', help='启动 Web UI 模式')
    p_webui.add_argument('--host', type=str, default='0.0.0.0', help='监听地址（默认 0.0.0.0）')
    p_webui.add_argument('--port', type=int, default=8080, help='端口（默认 8080）')
    p_webui.add_argument('--load', type=str, help='从保存的会话 ID 恢复')

    # session — 会话管理
    p_session = subparsers.add_parser('session', help='会话管理')
    p_session_sub = p_session.add_subparsers(dest='session_cmd', title='会话操作')

    p_session_sub.add_parser('list', help='列出所有保存的会话')

    p_delete = p_session_sub.add_parser('delete', help='删除指定会话')
    p_delete.add_argument('session_id', type=str, help='要删除的会话 ID')

    p_export = p_session_sub.add_parser('export', help='导出会话为 JSON 格式')
    p_export.add_argument('session_id', type=str, help='要导出的会话 ID')
    p_export.add_argument('-o', '--output', type=str, help='输出文件路径（默认打印到 stdout）')

    # version
    subparsers.add_parser('version', help='显示版本信息并退出')

    return parser.parse_args(argv[1:])


def _apply_theme(args: argparse.Namespace) -> None:
    """应用配色主题"""
    theme_name = config.get("theme", "dark")
    try:
        set_theme(theme_name)
    except ValueError:
        pass
