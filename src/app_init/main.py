"""应用主入口 — 从 app_init.py 拆分而来

包含 async main() 函数，是应用的异步入口点。

「一切皆插件」：main() 作为组合根，从 Profile（默认 cli）构建内核插件树，
工具、技能、MCP、模型适配器、Agent 循环、会话、命令、事件、UI/渲染器、
策略均由内核插件提供；运行时组件经内核服务解析（无内核时回退默认实现）。
"""

from __future__ import annotations

import asyncio
import logging
import os

from ._args import _parse_args, VERSION
from ._signal import SignalManager
from ._session_cmd import _handle_session_command
from ._config_cmd import _handle_config_command
from ._plugin_cmd import _handle_plugin_command

from ..chat_msgs import load_session, list_sessions
from ..tui.events import OutputConsumer
from ..tui.events.consumers import publish_output
from ..core.constants import CYAN, DIM, RESET, YELLOW
from ..core.telemetry.trace_context import generate_trace_id, get_current_trace_id, set_current_trace_id
from ..observability import get_default_facade
from ..api.escape_monitor import stop_active_monitor
from ..application import Application, AppContext, InteractiveMode, SingleMode

_logger = logging.getLogger(__name__)

_DEFAULT_PROFILE = "cli"


async def main():
    """异步主入口 — 使用 asyncio.run() 调用"""
    args = _parse_args()

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

    # ── 初始化可观测性 ──
    obs = get_default_facade()
    if not get_current_trace_id():
        set_current_trace_id(generate_trace_id())
    obs.start()

    # 单消费路径策略显式声明（方向D 步骤7）：ChatUI 活跃时 OutputEvent 由
    # ChatUIConsumer 渲染管线消费，OutputConsumer 仅处理非 ChatUI 上下文输出。
    output_consumer = OutputConsumer(chat_ui_managed=True)
    output_consumer.start()

    profile = getattr(args, "profile", "") or _DEFAULT_PROFILE
    patch_paths = list(getattr(args, "patch", None) or [])

    # ── 处理版本信息 ──
    # ★ 修复（2026-08-20）：版本分支须在 output_consumer.start() 之后
    #   publish_output（输出经 EventBus → OutputConsumer 消费）——修复前在
    #   最前面调用（无消费者），``python chat.py --version`` 静默无输出。
    if args.version or args.command == 'version':
        try:
            publish_output(f"  Chat {VERSION}", level="raw")
        finally:
            output_consumer.stop()
        return

    # ── 打印最终运行时插件配置（Profile/Bundle/Patch）后退出 ──
    if getattr(args, "dump_config", False) or args.command == 'dump-config':
        try:
            from ..plugins.bootstrap import dump_profile
            publish_output(dump_profile(profile, patch_paths=patch_paths), level="raw")
        except Exception as exc:  # noqa: BLE001 - 展示错误后退出
            publish_output(f"  ❌ dump-config 失败: {exc}", level="raw")
        finally:
            output_consumer.stop()
        return

    # ── 插件管理（list/add/remove）——不需要构建内核 ──
    if args.command == 'plugin':
        try:
            _handle_plugin_command(args)
        finally:
            output_consumer.stop()
        return

    # ★ P3（review 2026-08-20）：session/config 子命令输出依赖
    #   output_consumer 消费（publish_output → EventBus），故保留在 start()
    #   之后处理；return 前显式 stop（修复前跳过 finally 中的 stop，清理
    #   路径不一致——输出消费者订阅随进程退出由 OS 回收，但显式清理更干净）。
    if args.command == 'session':
        try:
            _handle_session_command(args)
        finally:
            output_consumer.stop()
        return
    if args.command == 'config':
        try:
            _handle_config_command(args)
        finally:
            output_consumer.stop()
        return

    # ── 信号处理 ──
    signal_mgr = SignalManager()
    signal_mgr.register_handlers()

    kernel = None
    mcp = None
    try:
        # ── 构建内核插件树（一切皆插件的组合根） ──
        from ..plugins.bootstrap import build_kernel, shutdown_kernel
        kernel = await build_kernel(profile, patch_paths=patch_paths)
        mcp = kernel.resolve_service("mcp")

        # ── MCP 外部工具接入：连接配置的 MCP server 并注册其工具 ──
        # 未配置 mcp_servers 时零开销（不建连接、不注册工具）；单个 server
        # 连接/发现失败只记 WARNING 并跳过，不阻断应用启动。
        if mcp is not None:
            try:
                await mcp.setup_mcp()
            except Exception:
                _logger.warning("MCP 初始化失败（忽略，继续启动）", exc_info=True)

        # ── 运行时不变量检查（--check-invariants）后退出 ──
        if getattr(args, "check_invariants", False):
            if kernel.has_service("invariants"):
                failures = kernel.resolve_service("invariants").check()
            else:
                failures = ["invariants 插件未加载"]
            if failures:
                publish_output("  ✗ 运行时不变量检查失败:", level="raw")
                for failure in failures:
                    publish_output(f"    - {failure}", level="raw")
            else:
                publish_output("  ✓ 运行时不变量检查通过", level="raw")
            return

        # ── run 模式 ──

        # ── clawbot 模式：微信 ClawBot 远程控制（默认 TUI：非全屏聊天界面
        #     + 本地输入 + 微信多用户共享同一会话） ──
        if args.command == 'clawbot':
            from ..clawbot.runner import run_clawbot
            await run_clawbot(model=args.model, re_login=args.re_login,
                              tui=not args.no_tui)
            return

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
        # ── 关闭 MCP 连接（注销动态工具 + 终止 stdio 子进程 / 关闭 HTTP 客户端） ──
        if mcp is not None:
            try:
                await mcp.shutdown_mcp()
            except Exception:
                _logger.debug("MCP 关闭异常", exc_info=True)
        # ── 卸载内核插件树（逆序撤销全部注册） ──
        try:
            from ..plugins.bootstrap import shutdown_kernel
            await shutdown_kernel(kernel)
        except Exception:
            _logger.debug("内核卸载异常", exc_info=True)
        stop_active_monitor()
        if output_consumer is not None:
            output_consumer.stop()
        # ★ 进入退出清理阶段：此后的信号不再取消任务，
        #   避免 asyncio.run() 清理阶段（shutdown_default_executor）被
        #   二次信号取消而抛出 CancelledError 裸 traceback。
        if signal_mgr is not None:
            signal_mgr.mark_exiting()
