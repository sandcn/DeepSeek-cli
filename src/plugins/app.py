"""应用组合根插件 — 提供 ``ctx.app``。

「一切皆插件」：``main.py`` 只保留最薄入口（解析参数 → 构建内核 →
``ctx.app.run`` → 卸载内核）；应用生命周期——可观测性启动、trace_id 初始化、
输出消费者、信号处理、ChatUI 错误处理器、子命令分发（version / dump-config /
plugin / session / config / check-invariants）、MCP 初始化、clawbot 与
交互/单次模式装配——全部由本插件服务承载，可按 Profile/Patch 启用、禁用或
替换（对应 dsh 的 runtime 应用插件）。

应用层组件（``Application`` / ``AppContext`` / 模式）由本服务装配套接，
``main.py`` 不再直接 import 它们。
"""

from __future__ import annotations

import logging

from ..kernel import Service, get_current_kernel, plugin

_logger = logging.getLogger(__name__)


class AppService(Service):
    """应用服务 — 占据 ``ctx.app``。"""

    provide = "app"
    name = "app"
    inject = ("config", "events")

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        self._output_consumer = None
        self._signal_manager = None
        self._bootstrapped = False

    # ── 启动 / 停止 ──────────────────────────────────────

    def bootstrap(self) -> None:
        """注册错误处理器 + 启动可观测性与输出消费者（幂等）。

        须在 ``logging.basicConfig()`` 之后调用（错误处理器依赖 root 已配置）。

        「一切皆插件」：错误处理器与输出消费者由内核 ``ctx.consumers`` 提供
        （可替换/禁用）；内核缺失时回退直接构造。
        """
        if self._bootstrapped:
            return
        consumers = self.ctx.service("consumers") if self.ctx.has("consumers") else None
        if consumers is not None:
            consumers.setup_error_handler()
            self._output_consumer = consumers.create_output_consumer(chat_ui_managed=True)
        else:
            from ..tui.consumer import setup_chat_ui_error_handler

            setup_chat_ui_error_handler()
            from ..tui.events import OutputConsumer

            self._output_consumer = OutputConsumer(chat_ui_managed=True)
        self._start_observability()
        self._output_consumer.start()
        self._bootstrapped = True

    def _start_observability(self) -> None:
        from ..core.telemetry.trace_context import (
            generate_trace_id,
            get_current_trace_id,
            set_current_trace_id,
        )

        obs = None
        if self.ctx.has("observability"):
            obs = self.ctx.consume("observability").port()
        if obs is not None:
            obs.start()
        if not get_current_trace_id():
            set_current_trace_id(generate_trace_id())

    def register_signals(self) -> None:
        """注册进程信号处理（幂等）。"""
        if self._signal_manager is not None:
            return
        from ..app_init._signal import SignalManager

        self._signal_manager = SignalManager()
        self._signal_manager.register_handlers()

    def stop(self) -> None:
        """终止 escape monitor、标记退出清理阶段（保留输出消费者至 shutdown）。"""
        from ..api.escape_monitor import stop_active_monitor

        try:
            stop_active_monitor()
        except Exception:
            _logger.debug("停止 escape monitor 异常", exc_info=True)
        if self._signal_manager is not None:
            self._signal_manager.mark_exiting()

    def shutdown(self) -> None:
        """进程退出前清理：停止输出消费者（幂等）。"""
        if self._output_consumer is not None:
            try:
                self._output_consumer.stop()
            except Exception:
                _logger.debug("停止输出消费者异常", exc_info=True)
            self._output_consumer = None

    # ── 运行 ─────────────────────────────────────────────

    async def run(self, args) -> None:
        """执行一次完整应用运行（组合根编排）。"""
        self.bootstrap()
        self.register_signals()
        try:
            await self._dispatch(args)
        finally:
            self.stop()

    async def _dispatch(self, args) -> None:
        from ..app_init._args import VERSION
        from ..tui.events.consumers import publish_output

        if args.version or args.command == "version":
            publish_output(f"  Chat {VERSION}", level="raw")
            return

        if getattr(args, "dump_config", False) or args.command == "dump-config":
            await self._dump_config(args)
            return

        if args.command == "plugin":
            from ..app_init._plugin_cmd import _handle_plugin_command

            _handle_plugin_command(args)
            return

        if args.command == "session":
            from ..app_init._session_cmd import _handle_session_command

            _handle_session_command(args)
            return

        if args.command == "config":
            from ..app_init._config_cmd import _handle_config_command

            _handle_config_command(args)
            return

        await self._setup_mcp()

        if getattr(args, "check_invariants", False):
            self._check_invariants()
            return

        if args.command == "clawbot":
            await self._run_clawbot(args)
            return

        loaded_data = await self._load_session(args)
        if args.load and loaded_data is None:
            return
        await self._run_modes(args, loaded_data)

    async def _dump_config(self, args) -> None:
        from ..tui.events.consumers import publish_output
        from .bootstrap import dump_profile

        try:
            kernel = get_current_kernel()
            profile = getattr(kernel, "profile", "") or "cli"
            patch_paths = list(getattr(args, "patch", None) or [])
            publish_output(dump_profile(profile, patch_paths=patch_paths), level="raw")
        except Exception as exc:
            publish_output(f"  ❌ dump-config 失败: {exc}", level="raw")

    async def _setup_mcp(self) -> None:
        if not self.ctx.has("mcp"):
            return
        try:
            await self.ctx.consume("mcp").setup_mcp()
        except Exception:
            _logger.warning("MCP 初始化失败（忽略，继续启动）", exc_info=True)

    def _check_invariants(self) -> None:
        from ..tui.events.consumers import publish_output

        if self.ctx.has("invariants"):
            failures = self.ctx.consume("invariants").check()
        else:
            failures = ["invariants 插件未加载"]
        if failures:
            publish_output("  ✗ 运行时不变量检查失败:", level="raw")
            for failure in failures:
                publish_output(f"    - {failure}", level="raw")
        else:
            publish_output("  ✓ 运行时不变量检查通过", level="raw")

    async def _load_session(self, args):
        from ..core.constants import CYAN, DIM, RESET, YELLOW
        from ..tui.events.consumers import publish_output

        if not args.load:
            return None
        from ..chat_msgs import list_sessions, load_session

        data = load_session(args.load)
        if data is None:
            publish_output(f"\n{YELLOW}  ! 未找到会话 '{args.load}'，可用的会话:{RESET}", level="raw")
            for session in list_sessions():
                title = session.get("title", "")
                title_info = f"「{title}」 " if title else ""
                publish_output(
                    f"    {DIM}{session['id']}  {title_info}{session['model']}  "
                    f"{session['message_count']}条消息  {session['saved_at']}{RESET}",
                    level="raw",
                )
            return None
        title = data.get("title", "")
        title_info = f"「{title}」 " if title else ""
        publish_output(f"\n{CYAN}  > 已恢复会话 {title_info}{args.load}{RESET}", level="raw")
        publish_output(
            f"{DIM}   模型: {data.get('model', '?')}  |  消息: {len(data.get('messages', []))} 条{RESET}",
            level="raw",
        )
        return data

    async def _run_modes(self, args, loaded_data) -> None:
        service = self.ctx.service("application") if self.ctx.has("application") else None
        if service is not None:
            await service.run(args, loaded_data)
            return

        from ..application import Application, AppContext, InteractiveMode, SingleMode

        if args.prompt:
            mode = SingleMode(AppContext(loaded_data=loaded_data), args.prompt)
        else:
            mode = InteractiveMode(AppContext(loaded_data=loaded_data))
        application = Application()
        application.set_mode(mode)
        await application.run()

    async def _run_clawbot(self, args) -> None:
        if self.ctx.has("clawbot"):
            await self.ctx.consume("clawbot").run(
                model=args.model, re_login=args.re_login, tui=not args.no_tui,
            )
            return
        from ..clawbot.runner import run_clawbot

        await run_clawbot(model=args.model, re_login=args.re_login, tui=not args.no_tui)


@plugin("app", inject=["config", "events"], provide=["app"])
def apply(ctx):
    return AppService(ctx)


__all__ = ["AppService", "apply"]
