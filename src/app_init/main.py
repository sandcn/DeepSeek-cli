"""应用主入口 — 从 app_init.py 拆分而来

包含 async main() 函数，是应用的异步入口点。
"""

from __future__ import annotations

import asyncio
import logging
import os

from ._args import _parse_args, VERSION, _apply_theme
from ._signal import SignalManager
from ._session_cmd import _handle_session_command

from ..chat_msgs import load_session, list_sessions
from ..tui.events import OutputConsumer
from ..tui.events.consumers import publish_output
from ..core.constants import CYAN, DIM, RESET, YELLOW
from ..core.telemetry.trace_context import generate_trace_id, get_current_trace_id, set_current_trace_id
from ..observability import get_default_facade
from ..api.escape_monitor import stop_active_monitor
from ..application import Application, AppContext, InteractiveMode, SingleMode

_logger = logging.getLogger(__name__)


async def main():
    """异步主入口 — 使用 asyncio.run() 调用"""
    args = _parse_args()

    # ── 处理版本信息 ──
    if args.version or args.command == 'version':
        publish_output(f"  Chat {VERSION}", level="raw")
        return

    # ── 设置日志级别（先 basicConfig 再注册 ChatUI 错误处理器——root 已有
    #    handler 时 basicConfig 静默失效，方向2 修复调用顺序） ──
    if args.verbose >= 2:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose >= 1:
        logging.basicConfig(level=logging.INFO)

    # ── 注册 ChatUI 错误处理器（须在 basicConfig 之后调用） ──
    from src.tui.consumer import setup_chat_ui_error_handler
    setup_chat_ui_error_handler()

    # ── 覆盖模型配置 ──
    if args.model:
        os.environ["CHAT_MODEL"] = args.model

    # ── 初始化可观测性与输出 ──
    obs = get_default_facade()
    if not get_current_trace_id():
        set_current_trace_id(generate_trace_id())
    obs.start()

    # 单消费路径策略显式声明（方向D 步骤7）：ChatUI 活跃时 OutputEvent 由
    # ChatUIConsumer 渲染管线消费，OutputConsumer 仅处理非 ChatUI 上下文输出。
    output_consumer = OutputConsumer(chat_ui_managed=True)
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
            from ..webui.server import run_web_server

            loaded_data = None
            if args.load:
                data = load_session(args.load)
                if data is None:
                    publish_output(f"\n{YELLOW}  ! 未找到会话 '{args.load}'{RESET}", level="raw")
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
                publish_output(f"\n{YELLOW}  ! 未找到会话 '{args.load}'，可用的会话:{RESET}", level="raw")
                for s in list_sessions():
                    title = s.get("title", "")
                    title_info = f"「{title}」 " if title else ""
                    publish_output(f"    {DIM}{s['id']}  {title_info}{s['model']}  {s['message_count']}条消息  {s['saved_at']}{RESET}", level="raw")
                return
            loaded_data = data
            title = data.get("title", "")
            title_info = f"「{title}」 " if title else ""
            publish_output(f"\n{CYAN}  > 已恢复会话 {title_info}{args.load}{RESET}", level="raw")
            publish_output(f"{DIM}   模型: {data.get('model', '?')}  |  消息: {len(data.get('messages', []))} 条{RESET}", level="raw")

        if args.prompt:
            mode = SingleMode(AppContext(loaded_data=loaded_data), args.prompt)
        else:
            mode = InteractiveMode(AppContext(loaded_data=loaded_data))
        app = Application()
        app.set_mode(mode)
        await app.run()

    except KeyboardInterrupt:
        publish_output("\n\n  ⚠ 用户中断", level="raw")
    except asyncio.CancelledError:
        publish_output("\n\n  ⚠ 任务被取消", level="raw")
    except Exception as e:
        publish_output(f"\n  ❌ 致命错误: {e}", level="raw")
        logging.critical("应用崩溃", exc_info=True)
    finally:
        stop_active_monitor()
        if output_consumer is not None:
            output_consumer.stop()
