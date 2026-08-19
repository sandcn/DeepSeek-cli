"""命令行参数解析 — 从 app_init.py 拆分而来

负责 argparse 参数定义、旧语法兼容。
"""

from __future__ import annotations

import argparse
import logging
import sys

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
            '  python chat.py -m xxx              指定模型启动（旧语法）\n'
            '  python chat.py session list        列出所有会话\n'
            '  python chat.py session delete xxx  删除会话\n'
            '  python chat.py session export xxx  导出会话\n'
            '  python chat.py config              显示全部配置\n'
            '  python chat.py config get model    查询单个配置\n'
            '  python chat.py config set model deepseek-v4-pro  设置配置\n'
            '  python chat.py version             查看版本\n'
            '  python chat.py clawbot             微信 ClawBot 远程控制（扫码登录）\n'
            '  python chat.py clawbot --re-login  强制重新扫码\n'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── 全局选项默认值（所有子命令共享；-m/-v 实际参数注册在 run 子命令上，
    #   旧语法兼容把 -m/-v 放在 run 之后解析——见 p_run） ──
    parser.add_argument('--version', action='store_true', help='显示版本信息并退出')
    parser.set_defaults(model='', verbose=0)

    # ── 子命令 ──
    subparsers = parser.add_subparsers(dest='command', title='子命令')

    # run — 交互/单次对话模式
    p_run = subparsers.add_parser('run', help='交互/单次对话模式（默认）')
    p_run.add_argument('-p', '--prompt', type=str, help='输入一句话，大模型回答完成后退出')
    p_run.add_argument('--load', type=str, help='从保存的会话 ID 恢复对话')
    # ★ P0（review 2026-08-20）：-m/--model、-v/--verbose、--version 注册在
    #   run 子命令上——修复前仅注册在主 parser：旧语法兼容把 -m/-v 挤到
    #   'run' 之后（``chat.py -v`` → ``['chat.py','run','-v']``），run 子解析器
    #   无这些参数 → argparse 必然报 ``unrecognized arguments`` 退出（README
    #   宣称的 ``-m/-v/-vv`` 用法全部失效）。注册到 run 后旧语法/新语法均
    #   正常；其他子命令（session/config/version/clawbot）经 parser.set_defaults
    #   兜底 model/verbose 默认值，main.py 读取不报 AttributeError。
    p_run.add_argument('-m', '--model', type=str, default='', help='指定模型（覆盖配置文件）')
    p_run.add_argument('-v', '--verbose', action='count', default=0, help='增加日志详细度（-v INFO, -vv DEBUG）')
    p_run.add_argument('--version', action='store_true', help='显示版本信息并退出')

    # session — 会话管理
    p_session = subparsers.add_parser('session', help='会话管理')
    p_session_sub = p_session.add_subparsers(dest='session_cmd', title='会话操作')

    p_session_sub.add_parser('list', help='列出所有保存的会话')

    p_delete = p_session_sub.add_parser('delete', help='删除指定会话')
    p_delete.add_argument('session_id', type=str, help='要删除的会话 ID')

    p_export = p_session_sub.add_parser('export', help='导出会话为 JSON 格式')
    p_export.add_argument('session_id', type=str, help='要导出的会话 ID')
    p_export.add_argument('-o', '--output', type=str, help='输出文件路径（默认打印到 stdout）')

    # config — 程序配置管理（显示/编辑）
    p_config = subparsers.add_parser('config', help='显示/编辑程序配置')
    p_config_sub = p_config.add_subparsers(dest='config_cmd', title='配置操作')

    p_config_sub.add_parser('list', help='列出全部配置')
    p_config_sub.add_parser('show', help='列出全部配置（同 list）')

    p_get = p_config_sub.add_parser('get', help='查询单个配置')
    p_get.add_argument('key', type=str, help='配置键名（如 MODEL / model）')

    p_set = p_config_sub.add_parser('set', help='设置配置并持久化')
    p_set.add_argument('key', type=str, help='配置键名（如 MODEL / model）')
    p_set.add_argument('value', type=str, help='配置值（bool 接受 true/false；list/dict 接受 JSON）')

    p_reset = p_config_sub.add_parser('reset', help='重置为默认值')
    p_reset.add_argument('key', type=str, help='配置键名（如 MODEL / model）')

    # version
    subparsers.add_parser('version', help='显示版本信息并退出')

    # clawbot — 微信 ClawBot 远程控制
    p_clawbot = subparsers.add_parser(
        'clawbot',
        help='微信 ClawBot 远程控制（扫码登录、远程发命令、结果回显）',
    )
    p_clawbot.add_argument('--re-login', action='store_true',
                           help='强制重新扫码登录（忽略本地缓存凭证）')
    p_clawbot.add_argument('--no-tui', action='store_true',
                           help='不使用 TUI 界面（回退纯文本日志模式）')

    return parser.parse_args(argv[1:])