"""AddmsgMiddleware — /addmsg 流式插入用户消息中间件

背景（2026-08-17 用户需求：增加 addmsg 命令）：
- /addmsg <消息内容> 把消息作为 user 角色插入正在流式输出的对话中
- 插入时机：回答（answer）、思考（thinking）、工具调用（tool call）
  其中一个阶段完成后插入，不打断当前阶段的输出
- 无流式输出时由 AddmsgPlugin 当作普通用户消息处理（本中间件不介入）

阶段完成点：
- after_model_call — 单次模型调用（流）结束，思考/回答阶段已完成
- after_tool_execution — 工具调用执行完成

插入来源（两种）：
1. AddmsgPlugin 在命令路径中调 agent.add_addmsg(content) 暂存的消息
2. 流式期间用户直接在输入框输入 "/addmsg <内容>" 并回车——经注入的
   Input provider（peek_queued_input）捕获，消费后并入待插入队列

仅注册于 MainAgent（Agent）的 Pipeline；SubAgent 不经过 Pipeline
（独立 _run_impl 循环），天然只对主 Agent 生效（用户需求：只有 mainagent 有用）。
"""

from __future__ import annotations

import logging

from ..pipeline import AsyncMiddleware

_logger = logging.getLogger(__name__)

_ADDMSG_CMD_PREFIX = "/addmsg"


class AddmsgMiddleware(AsyncMiddleware):
    """addmsg 流式插入中间件 — 阶段完成后把排队用户消息插入对话。"""

    name = "AddmsgMiddleware"

    async def after_model_call(self, ctx) -> None:
        """思考/回答阶段完成：插入排队的 addmsg 消息。

        先重置 addmsg_inserted 标志（清除上一轮 after_tool_execution
        设置的残留），再执行插入检查。
        """
        try:
            ctx.addmsg_inserted = False
            await self._insert_pending(ctx)
        except Exception:
            _logger.exception("AddmsgMiddleware.after_model_call 异常")

    async def after_tool_execution(self, ctx) -> None:
        """工具调用完成：插入排队的 addmsg 消息。"""
        try:
            await self._insert_pending(ctx)
        except Exception:
            _logger.exception("AddmsgMiddleware.after_tool_execution 异常")

    async def _insert_pending(self, ctx) -> None:
        """检查并插入排队的 addmsg 消息。

        插入成功时设置 ctx.addmsg_inserted=True，pipeline 据此在
        无工具调用时继续下一轮模型调用（让模型处理新插入的用户消息）。
        """
        agent = getattr(ctx, "agent", None)
        if agent is None or not hasattr(agent, "has_pending_addmsg"):
            return
        self._capture_from_input(agent)
        msgs = agent.drain_addmsg()
        if not msgs:
            return
        agent.insert_addmsg_messages(msgs)
        ctx.addmsg_inserted = True
        self._notify_ui(agent, msgs)

    def _capture_from_input(self, agent) -> None:
        """捕获流式期间用户直接输入的 "/addmsg <内容>"（未消费的排队输入）。

        仅消费以 /addmsg 开头的排队输入；其余排队文本（普通消息）保留，
        由 round_end 的 drain_all 走原有 queued_input 路径处理。
        """
        provider = getattr(agent, "_addmsg_input_provider", None)
        if provider is None:
            return
        try:
            input_ = provider()
        except Exception:
            return
        if input_ is None or not hasattr(input_, "peek_queued_input"):
            return
        try:
            peeked = input_.peek_queued_input()
        except Exception:
            _logger.debug("addmsg peek_queued_input 异常", exc_info=True)
            return
        if not peeked or not peeked.lstrip().startswith(_ADDMSG_CMD_PREFIX):
            return
        try:
            queued = input_.get_queued_input()
        except Exception:
            _logger.debug("addmsg get_queued_input 异常", exc_info=True)
            return
        if queued is None:
            return
        parts = queued.split(maxsplit=1)
        content = parts[1].strip() if len(parts) > 1 else ""
        if content:
            agent.add_addmsg(content)

    def _notify_ui(self, agent, msgs: list[str]) -> None:
        """把插入的用户消息渲染到消息区（经注入的 chat_ui provider）。"""
        provider = getattr(agent, "_addmsg_chat_ui_provider", None)
        if provider is None:
            return
        try:
            chat_ui = provider()
        except Exception:
            return
        if chat_ui is None or not hasattr(chat_ui, "on_user_message"):
            return
        try:
            for m in msgs:
                chat_ui.on_user_message(m)
        except Exception:
            _logger.debug("addmsg UI 通知失败", exc_info=True)
