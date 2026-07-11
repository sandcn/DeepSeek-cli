"""ModelPlugin — 切换模型 (/model)

无参数时：暂停 ChatUIConsumer + 停止 EscapeMonitor，
让底部栏补全弹窗交互选择模型，选择完成后恢复两者。

有参数时：直接调用 _cmd_model 切换模型（无需 suspend）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry

_logger = logging.getLogger(__name__)


class ModelPlugin(InteractiveCommandPlugin):
    """切换模型 (/model)

    无参数时使用底部栏补全弹窗交互选择模型（需 suspend/resume）；
    有参数时直接调用 _cmd_model 切换模型。
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="model",
            description="切换模型",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /model 命令

        无参数 → Picker 交互（suspend/stop → _cmd_model → resume/start）
        有参数 → 直接切换（调用 _cmd_model）
        """
        loop = self._loop
        if loop is None:
            _logger.error("ModelPlugin 未绑定 InteractiveLoop")
            return False

        chat_ui = loop._chat_ui
        monitor = loop._monitor
        session = ctx.session
        state = ctx.state

        has_args = bool(ctx.arg and ctx.arg.strip())

        if not has_args:
            # ★ 无参数 → Picker 交互（需 suspend/stop）
            if chat_ui is not None:
                chat_ui.suspend()
            if monitor is not None:
                # ★ stop 在 try 外（非 finally）：必须在进入模型选择 Picker 交互前
                #   确认终端已恢复 cooked 模式（Picker 需要 raw I/O 处理 ↑↓/Enter），
                #   若放入 finally，Picker 过程中终端仍处于 cbreak 模式，无法正常交互。
                #   start 在 finally 中确保选择完成后始终恢复监听，无竞态风险。
                monitor.stop()
            try:
                from ...commands._config_cmd import _cmd_model
                from ...internal.commands._command_core import CommandContext
                from ...commands import CommandUiAdapter
                _ui_adapter = CommandUiAdapter()
                _cmd_ctx = CommandContext(
                    messages=session.messages,
                    state=state,
                    arg="",
                    build_system_prompt=session.agent.build_system_prompt,
                    get_user_input=lambda prompt="": "",
                    context_manager=session.context_manager,
                    session=session,
                    config_port=getattr(session, '_config_port', None),
                    ui_adapter=_ui_adapter,
                )
                cmd_handled = await asyncio.to_thread(_cmd_model, _cmd_ctx)
                if cmd_handled:
                    new_model = state.get("model")
                    if new_model and new_model != session.model:
                        session.model = new_model
            finally:
                if monitor is not None:
                    monitor.start()
                if chat_ui is not None:
                    chat_ui.resume()
        else:
            # ★ 有参数 → 直接切换（无需 suspend）
            from ...commands._config_cmd import _cmd_model
            from ...internal.commands._command_core import CommandContext
            from ...commands import CommandUiAdapter
            _ui_adapter = CommandUiAdapter()
            _cmd_ctx = CommandContext(
                messages=session.messages,
                state=state,
                arg=ctx.arg,
                build_system_prompt=session.agent.build_system_prompt,
                get_user_input=lambda prompt="": "",
                context_manager=session.context_manager,
                session=session,
                config_port=getattr(session, '_config_port', None),
                ui_adapter=_ui_adapter,
            )
            cmd_handled = await asyncio.to_thread(_cmd_model, _cmd_ctx)
            if cmd_handled:
                new_model = state.get("model")
                if new_model and new_model != session.model:
                    session.model = new_model

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "ModelPlugin 需要异步执行，请调用 async_execute()"
        )


# 模块级自注册
get_plugin_registry().register(ModelPlugin())
