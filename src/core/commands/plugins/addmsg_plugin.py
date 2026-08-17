"""AddmsgPlugin — 向流式输出中的主 Agent 插入用户消息 (/addmsg)

用法: /addmsg <消息内容>

行为（2026-08-17 用户需求）：
- 把消息内容以 user 角色插入正在流式输出的大模型对话中；
- 插入时机：回答（answer）、思考（thinking）、工具调用（tool call）
  三者中当前正在进行的那一个阶段完成后插入（不打断当前阶段输出）；
- 没有流式输出时：当作普通用户消息插入消息队列（走正常 run_round）；
- 只有 mainagent 有用（SubAgent 不经过命令分发路径，天然不受影响）。

实现路径：
1. 流式输出期间：AddmsgMiddleware 在 after_model_call（思考/回答完成）
   与 after_tool_execution（工具调用完成）钩子中，把本插件暂存到
   agent._addmsg_queue 的消息（以及用户在输入框直接输入的
   "/addmsg 内容"）以 user 角色插入对话，并让 pipeline 继续下一轮
   模型调用处理新消息。
2. 非流式期间：本插件直接调用 session.run_round(content)，
   与普通用户消息行为一致。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry

_logger = logging.getLogger(__name__)


class AddmsgPlugin(InteractiveCommandPlugin):
    """向流式输出中的主 Agent 插入用户消息 (/addmsg)"""

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="addmsg",
            description="向正在流式输出的主 Agent 插入用户消息（当前阶段完成后生效）",
            usage="<消息内容>",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /addmsg 命令"""
        from ....core.constants import YELLOW, RESET, GREEN
        from ....core.state_machine import SessionState

        content = (ctx.arg or "").strip()
        chat_ui = self._loop._chat_ui if self._loop is not None else None

        # ── 参数校验：需要非空消息内容 ──
        if not content:
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}用法: /addmsg <消息内容>{RESET}"
                )
            else:
                _logger.warning("/addmsg 缺少消息内容")
            return True

        session = ctx.session
        if session is None:
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} 会话不可用，无法插入消息"
                )
            return True

        agent = getattr(session, "agent", None)
        if agent is None:
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} Agent 不可用，无法插入消息"
                )
            return True

        # ── 仅主 Agent 支持（SubAgent 无独立会话上下文） ──
        if self._is_subagent(agent):
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} /addmsg 仅主 Agent 可用"
                )
            return True

        # ── 有流式输出：排队等待当前阶段完成后由中间件插入 ──
        if session.state_machine.is_(SessionState.RUNNING):
            if hasattr(agent, "add_addmsg"):
                agent.add_addmsg(content)
                if chat_ui is not None:
                    chat_ui.write_line(
                        f"  {GREEN}+ 消息已排队: {content[:60]}"
                        f"{'...' if len(content) > 60 else ''}{RESET}"
                        f"（将在当前阶段完成后插入）"
                    )
                else:
                    _logger.info("addmsg 已排队: %s", content[:60])
            else:
                if chat_ui is not None:
                    chat_ui.write_line(
                        f"  {YELLOW}\u26a0{RESET} 当前 Agent 不支持流式插入"
                    )
            return True

        # ── 无流式输出：当作普通用户消息处理 ──
        await self._run_as_normal_message(ctx, content)
        return True

    async def _run_as_normal_message(self, ctx: Any, content: str) -> None:
        """无流式输出：把消息当作普通用户消息插入消息队列并立即处理。

        复刻 _handle_regular_msg 的核心收尾逻辑（checkpoint / pending
        loop / flush），确保与正常用户输入行为一致。
        """
        from ....api.interrupt_async import reset_interrupt_async
        from ....core.constants import YELLOW, RESET

        loop = self._loop
        session = ctx.session
        chat_ui = loop._chat_ui if loop is not None else None

        if chat_ui is not None:
            reset_interrupt_async(
                input_instance=chat_ui.input if chat_ui is not None else None
            )
            # 重置工具计数（新轮开始）
            try:
                chat_ui.bottom_bar.reset_tool_count()
            except Exception:
                _logger.debug("bottom_bar.reset_tool_count 异常", exc_info=True)
            # 通过 ChatUI 打印用户消息
            chat_ui.on_user_message(content)

        result = await session.run_round(content)

        # 首轮消息完成后保存 checkpoint（异常时已处理的消息不丢失）
        if not result.get("interrupted", False):
            try:
                session.save_checkpoint()
            except Exception:
                _logger.exception("addmsg run_round 后 save_checkpoint 异常，不阻断处理")

        # 同步模型名
        state = getattr(ctx, "state", None)
        if isinstance(state, dict):
            state["model"] = getattr(session, "model", state.get("model", ""))
        if chat_ui is not None:
            try:
                chat_ui.bottom_bar.set_model_name(getattr(session, "model", ""))
            except Exception:
                _logger.debug("bottom_bar.set_model_name 异常", exc_info=True)

        # 处理排队消息
        try:
            breached, _ = await session.run_pending_loop(max_iter=10)
            if breached and chat_ui is not None:
                chat_ui.write_line(
                    f"\n  {YELLOW}[错误]{RESET} 系统繁忙，部分消息未能处理，请重新发送"
                )
                session._force_state_recovery()
        except Exception:
            _logger.debug("addmsg run_pending_loop 异常", exc_info=True)

        # 等待 ChatUI 渲染完所有待处理命令
        if chat_ui is not None:
            try:
                await asyncio.to_thread(chat_ui.flush)
            except Exception:
                _logger.debug("addmsg chat_ui.flush 异常", exc_info=True)

    @staticmethod
    def _is_subagent(agent) -> bool:
        """判断 agent 是否为 SubAgent（仅主 Agent 支持 /addmsg）。"""
        from ....core.subagent import SubAgent
        return isinstance(agent, SubAgent)

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 抛出异常，防止误调用"""
        raise RuntimeError(
            "AddmsgPlugin 需要异步执行，请调用 async_execute()"
        )


# 模块级自注册
get_plugin_registry().register(AddmsgPlugin())
