"""应用初始化模块 — 从 app.py 拆分而来

包含：信号处理、参数解析、应用入口 main()。
"""

import asyncio
import argparse
import os
import signal
import logging
import threading

from .config import config
from .ui.events import OutputConsumer
from .ui.theme import set_theme
from .chat_msgs import load_session, list_sessions
from .core.telemetry.trace_context import generate_trace_id, get_current_trace_id, set_current_trace_id
from .observability import get_default_facade
from .ui.colors import CYAN, DIM, RESET, YELLOW
from .ui._lock import locked_print

from .api.escape_monitor import get_active_monitor, stop_active_monitor

from .application import Application, AppContext, InteractiveMode, SingleMode

_logger = logging.getLogger(__name__)


# ── 版本常量 ──
VERSION = "v2.2.0"


# ── 信号处理管理器 ──

_SHUTDOWN_GRACE_PERIOD = 3.0


class SignalManager:
    """信号处理管理器 — 封装 SIGINT/SIGTERM 处理和降级路径"""

    def __init__(self):
        self._registered: bool = False
        self._shutdown_requested = asyncio.Event()
        self._sigint_lock = threading.Lock()

    @property
    def is_shutdown_requested(self) -> bool:
        return self._shutdown_requested.is_set()

    async def handle_sigint(self) -> None:
        """处理 SIGINT — 首按优雅中断，再按强制关闭"""
        from .api.interrupt_async import request_interrupt_async

        with self._sigint_lock:
            if self._shutdown_requested.is_set():
                # 第二次按 Ctrl+C 直接强关，不再去抖
                locked_print("\n  ⚠ 强制关闭所有任务…", flush=True)
                stop_active_monitor()
                current = asyncio.current_task()
                if current is None:
                    # current_task() 返回 None：取消所有任务触发优雅关闭
                    # 替代 sys.exit(1)，避免 SystemExit 在 asyncio 中导致资源泄漏
                    for t in asyncio.all_tasks():
                        t.cancel()
                    return
                tasks_to_cancel = [
                    t for t in asyncio.all_tasks() if t is not current
                ]
                for t in tasks_to_cancel:
                    t.cancel()
                return

            self._shutdown_requested.set()

        # 锁外执行非关键路径
        locked_print("\n  ⚠ 正在中断…（再按一次 Ctrl+C 强制退出）", flush=True)
        request_interrupt_async()

        await asyncio.sleep(_SHUTDOWN_GRACE_PERIOD)

    async def shutdown(self) -> None:
        """SIGTERM 的优雅关闭 — 直接强制退出"""
        locked_print("\n  ⚠ 正在关闭…", flush=True)
        stop_active_monitor()
        current = asyncio.current_task()
        if current is None:
            import sys
            sys.exit(1)
        tasks = [t for t in asyncio.all_tasks() if t is not current]
        for t in tasks:
            t.cancel()
        # 不 await gather，不 stop loop

    def register_handlers(self, loop=None) -> None:
        """注册 SIGINT/SIGTERM 回调

        优先使用 asyncio 原生 add_signal_handler（与事件循环集成最佳），
        降级到 signal.signal + loop.call_soon_threadsafe。
        在 Termux 下 SIGTERM 设为忽略（Android 进程管理发来的非用户信号）。
        """
        if self._registered:
            return
        if loop is None:
            loop = asyncio.get_event_loop()
        _sigint_ok = False
        _sigterm_ok = False

        try:
            loop.add_signal_handler(signal.SIGINT, lambda: asyncio.create_task(self.handle_sigint()))
            _sigint_ok = True
        except NotImplementedError:
            pass

        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(self.shutdown()))
            _sigterm_ok = True
        except NotImplementedError:
            pass

        # ★ Bug1 修复：降级到 signal.signal（Windows / Android Termux 不支持 add_signal_handler）
        if not _sigint_ok:
            try:
                signal.signal(signal.SIGINT, lambda s, f: loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self.handle_sigint())
                ))
            except (ValueError, RuntimeError):
                pass

        if not _sigterm_ok:
            try:
                if os.environ.get('TERMUX_VERSION'):
                    # ★ Termux 修复：Android 系统会向后台进程发送 SIGTERM，
                    # 这是进程生命周期管理信号，不是用户意图，不应退出服务器。
                    signal.signal(signal.SIGTERM, signal.SIG_IGN)
                else:
                    signal.signal(signal.SIGTERM, lambda s, f: loop.call_soon_threadsafe(
                        lambda: asyncio.create_task(self.shutdown())
                    ))
            except (ValueError, RuntimeError):
                pass

        self._registered = True


# ── 参数解析 ──

def _parse_args() -> argparse.Namespace:
    """解析命令行参数，支持子命令和旧语法自动兼容"""
    # 旧语法兼容：无参数或首个参数以 - 开头时，自动映射到对应子命令
    import sys
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


# ── 会话管理命令 ──

def _handle_session_command(args: argparse.Namespace) -> None:
    """处理 session 子命令（list/delete/export）"""
    from .chat_msgs import list_sessions, delete_session, export_session
    from .ui.colors import CYAN, DIM, RESET, YELLOW, GREEN

    if args.session_cmd == 'list':
        sessions = list_sessions()
        if not sessions:
            locked_print(f"\n{DIM}  没有保存的会话{RESET}", flush=True)
            return
        locked_print(f"\n{CYAN}  > 已保存的会话:{RESET}", flush=True)
        for s in sessions:
            title = s.get("title", "")
            title_info = f"「{title}」 " if title else ""
            locked_print(f"    {DIM}{s['id']}  {title_info}{s['model']}  {s['message_count']}条消息  {s['saved_at']}{RESET}", flush=True)
        locked_print()

    elif args.session_cmd == 'delete':
        ok = delete_session(args.session_id)
        if ok:
            locked_print(f"\n{GREEN}  ✓ 会话已删除: {args.session_id}{RESET}", flush=True)
        else:
            locked_print(f"\n{YELLOW}  ! 未找到会话: {args.session_id}{RESET}", flush=True)

    elif args.session_cmd == 'export':
        result = export_session(args.session_id, output=args.output)
        if result is None:
            locked_print(f"\n{YELLOW}  ! 未找到会话: {args.session_id}{RESET}", flush=True)
        elif args.output:
            locked_print(f"\n{GREEN}  ✓ 会话已导出到: {result}{RESET}", flush=True)
        else:
            locked_print(result)

    else:
        locked_print(f"\n{YELLOW}  ! 未知的 session 命令: {args.session_cmd}{RESET}", flush=True)
        locked_print(f"{DIM}  可用命令: list, delete <id>, export <id> [-o 文件路径]{RESET}", flush=True)


# ── 主入口 ──

async def main():
    """异步主入口 — 使用 asyncio.run() 调用"""
    args = _parse_args()

    # ── 处理版本信息 ──
    if args.version or args.command == 'version':
        locked_print(f"  Chat {VERSION}")
        return

    # ── 注册 ChatUI 错误处理器 ──
    from src.chat_ui import setup_chat_ui_error_handler
    setup_chat_ui_error_handler()

    # ── 设置日志级别 ──
    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose >= 1:
        logging.basicConfig(level=logging.INFO)

    # ── 覆盖模型配置 ──
    if args.model:
        os.environ["CHAT_MODEL"] = args.model

    # ── 初始化可观测性与输出 ──
    obs = get_default_facade()
    if not get_current_trace_id():
        set_current_trace_id(generate_trace_id())
    obs.start()

    output_consumer = OutputConsumer()
    output_consumer.start()

    # ── session 子命令：无异步操作，快速返回 ──
    if args.command == 'session':
        _handle_session_command(args)
        return

    # ── 信号处理（非 webui 模式） ──
    if args.command != 'webui':
        signal_mgr = SignalManager()
        signal_mgr.register_handlers()

    try:
        # ── Web UI 模式 ──
        if args.command == 'webui':
            output_consumer.stop()
            from .webui.server import run_web_server

            loaded_data = None
            if args.load:
                data = load_session(args.load)
                if data is None:
                    locked_print(f"\n{YELLOW}  ! 未找到会话 '{args.load}'{RESET}", flush=True)
                    return
                loaded_data = data

            await run_web_server(
                host=args.host,
                port=args.port,
                loaded_data=loaded_data,
            )
            return

        # ── run 模式 ──
        _apply_theme(args)

        loaded_data = None
        if args.load:
            data = load_session(args.load)
            if data is None:
                locked_print(f"\n{YELLOW}  ! 未找到会话 '{args.load}'，可用的会话:{RESET}", flush=True)
                for s in list_sessions():
                    title = s.get("title", "")
                    title_info = f"「{title}」 " if title else ""
                    locked_print(f"    {DIM}{s['id']}  {title_info}{s['model']}  {s['message_count']}条消息  {s['saved_at']}{RESET}", flush=True)
                return
            loaded_data = data
            title = data.get("title", "")
            title_info = f"「{title}」 " if title else ""
            locked_print(f"\n{CYAN}  > 已恢复会话 {title_info}{args.load}{RESET}", flush=True)
            locked_print(f"{DIM}   模型: {data.get('model', '?')}  |  消息: {len(data.get('messages', []))} 条{RESET}", flush=True)

        if args.prompt:
            mode = SingleMode(AppContext(loaded_data=loaded_data), args.prompt)
        else:
            mode = InteractiveMode(AppContext(loaded_data=loaded_data))
        app = Application()
        app.set_mode(mode)
        await app.run()

    except KeyboardInterrupt:
        locked_print("\n\n  ⚠ 用户中断", flush=True)
    except asyncio.CancelledError:
        locked_print("\n\n  ⚠ 任务被取消", flush=True)
    except Exception as e:
        locked_print(f"\n  ❌ 致命错误: {e}", flush=True)
        logging.critical("应用崩溃", exc_info=True)
    finally:
        stop_active_monitor()
        if output_consumer is not None:
            output_consumer.stop()
