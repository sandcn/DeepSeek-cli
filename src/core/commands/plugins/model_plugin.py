"""ModelPlugin — 切换模型 (/model)

有参数时：直接调用 _cmd_model 按序号/名称切换模型。
无参数时：UserSelectPopup 模态底部视图交互选择模型（↑↓/j/k/g/G 导航、
Enter 确认、Esc 取消）。

★ 2026-08-19（模型选择弹窗上下键无效果修复）：无参数分支不再执行
``chat_ui.suspend()`` / ``monitor.stop()``。根因：本项目 TUI 的键盘事件
分发（``InputDispatcher.read_stdin_once``）由 **render 线程**渲染循环的
INPUT 阶段（``_phase_process_input``）驱动，且 ``monitor.stop()`` 还会
``input.stop_io()``（``can_read()`` 返回 False）并恢复 cooked 模式——
修复前 suspend + stop 后，弹窗虽经 ``request_bottom_redraw`` 同步渲染
一帧显示出来，但渲染线程/输入 I/O 均已停止 → ``SelectInput`` 控件收不到
任何按键事件（↑↓/j/k/g/G/Enter/Esc 全部无效，只能等 60s 超时「已取消」）。

现对齐 ``editmsg_plugin`` 同构模式：保持 render 线程 + _BottomBar +
cbreak 模式运行，弹窗交互由 render 线程驱动组件写 ``us.done``（轮询在
工作线程 ``asyncio.to_thread`` 内进行，不阻塞事件循环）；选择完成后无需
resume/start（从未停止）。
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

    无参数时：UserSelectPopup 弹窗交互选择（保持 render 线程运行驱动 ↑↓/Enter）；
    有参数时：直接调用 _cmd_model 切换模型。
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="model",
            description="切换模型",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /model 命令

        无参数 → 弹窗交互选择（不 suspend/stop，render 线程驱动键盘分发）
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
            # ★ 修复（2026-08-19）：不执行 chat_ui.suspend() / monitor.stop()
            #   ——render 线程停止后键盘分发（INPUT 阶段）不再执行、
            #   stop_io 后输入不可读：弹窗显示但 ↑↓/Enter 无效果。
            #   保持 render 线程 + cbreak + 输入 I/O 运行，弹窗交互由
            #   render 线程驱动（与 editmsg_plugin 同构）。
            # 进入选择前：排空 stdin 残留字节（残留 Enter/Esc 会把弹窗
            # 误确认/误取消）+ 清残留中断标志（防残留 Ctrl+C 干扰选择）。
            self._prepare_selection_input(chat_ui, monitor)
            try:
                _cmd_ctx = self._build_cmd_ctx(ctx)
                cmd_handled = await asyncio.to_thread(self._run_cmd_model, _cmd_ctx)
                if cmd_handled:
                    self._apply_model_change(session, state, chat_ui)
            finally:
                # 无需 resume/start（从未 suspend/stop）；仅清残留中断标志
                if monitor is not None:
                    try:
                        monitor.clear_interrupted()
                    except Exception:
                        _logger.debug("ModelPlugin finally clear_interrupted 异常", exc_info=True)
        else:
            # ★ 有参数 → 直接切换（无需弹窗，无需任何终端切换）
            _cmd_ctx = self._build_cmd_ctx(ctx)
            cmd_handled = await asyncio.to_thread(self._run_cmd_model, _cmd_ctx)
            if cmd_handled:
                self._apply_model_change(session, state, chat_ui)

        return True

    # ── 内部辅助 ──────────────────────────────────────────

    @staticmethod
    def _prepare_selection_input(chat_ui, monitor) -> None:
        """弹窗交互前清残留输入（stdin 残留字节 + 中断标志）。

        残留 Enter/Esc 字节会把刚打开的弹窗误确认/误取消（旧中断遗留），
        残留中断标志则会让后续轮询立即视为取消——两处防御与 editmsg
        插件的入口清理语义一致。
        """
        try:
            from ....api.interrupt_async import flush_stdin
            flush_stdin(input_instance=chat_ui.get_input() if chat_ui is not None else None)
        except Exception:
            _logger.debug("ModelPlugin flush_stdin 异常", exc_info=True)
        if monitor is not None:
            try:
                monitor.clear_interrupted()
            except Exception:
                _logger.debug("ModelPlugin clear_interrupted 异常", exc_info=True)

    @staticmethod
    def _build_cmd_ctx(ctx: Any):
        """构建 _cmd_model 所需的 CommandContext（ui_adapter = CommandUiAdapter）。"""
        from ...internal.commands._command_core import CommandContext
        from ...commands import CommandUiAdapter
        session = ctx.session
        return CommandContext(
            messages=session.messages,
            state=ctx.state,
            arg=ctx.arg,
            build_system_prompt=session.agent.build_system_prompt,
            get_user_input=lambda prompt="": "",
            context_manager=session.context_manager,
            session=session,
            config_port=getattr(session, '_config_port', None),
            ui_adapter=CommandUiAdapter(),
        )

    @staticmethod
    def _run_cmd_model(cmd_ctx) -> bool:
        """工作线程入口：执行 _cmd_model（无参数时内部轮询弹窗 done）。"""
        from ...commands._model_cmd import _cmd_model
        return _cmd_model(cmd_ctx)

    @staticmethod
    def _apply_model_change(session, state, chat_ui) -> None:
        """命令处理后同步 session.model + 刷新状态栏模型名显示。"""
        new_model = state.get("model")
        if not (new_model and new_model != session.model):
            return
        session.model = new_model
        if chat_ui is not None:
            try:
                chat_ui.bottom_bar.set_model_name(new_model)
            except Exception:
                _logger.debug("ModelPlugin set_model_name 异常", exc_info=True)

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "ModelPlugin 需要异步执行，请调用 async_execute()"
        )


# 模块级自注册
get_plugin_registry().register(ModelPlugin())
