"""DeitmsgPlugin — 直接编辑上一条消息 (/deitmsg)

/deitmsg 是 /editmsg 的快捷版本：不经过交互式消息选择弹窗，
直接定位到最后一条用户消息，恢复沙盒文件到该消息之前的状态，
截断消息并预填旧内容供用户重新编辑。

同时在上屏显示本次沙盒还原了多少个文件。
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry
# ★ 修复（/deitmsg 同步降级路径 NameError）：常量原先仅在 ``async_execute``
#   内局部导入，``execute``（同步降级路径）引用 YELLOW/RESET 时抛 NameError
#   ——被 ``except Exception`` 吞掉，用户在非交互环境执行 /deitmsg 得不到
#   任何提示（提示串的 f-string 求值先于 write 失败）。改为模块级导入（唯一
#   真源 src.core.constants 为纯常量模块，无循环导入风险），两条路径共用。
from ....core.constants import YELLOW, RESET, GREEN, DIM

_logger = logging.getLogger(__name__)

# ★ P0（review 修复）：删除本地 ``_content_str`` 副本——它与
#   ``tui/pipeline/message_display._content_str``（消毒/兜底更全）重复，且
#   **预填用途错误**：把非文本部分拍平成 ``[图片: <url>]``（provider 常用
#   base64 data URL，可能极大）注入编辑行。``/editmsg`` 已修（``_text_part_str``
#   + ``_content_has_nontext`` + ⚠ 提示），本插件为同语义快捷路径必须同步
#   （见 ``async_execute`` 内注释）。

class DeitmsgPlugin(InteractiveCommandPlugin):
    """直接编辑上一条用户消息 (/deitmsg)

    与 /editmsg 的区别：
    - 不经过交互式消息选择弹窗
    - 直接定位到最后一条用户消息
    - 恢复沙盒文件后，在上屏显示还原了多少个文件
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="deitmsg",
            description="直接编辑上一条消息（快捷版 /editmsg）",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /deitmsg 命令

        直接定位到最后一条 user 消息，恢复沙盒、截断消息、预填旧内容。
        """
        from ....app_loop import _non_system_messages
        from ....api.interrupt_async import flush_stdin, reset_interrupt_async

        loop = self._loop
        if loop is None:
            _logger.error("DeitmsgPlugin 未绑定 InteractiveLoop")
            return False

        chat_ui = loop._chat_ui
        monitor = loop._monitor
        session = ctx.session
        state = ctx.state  # dict: {"model": ..., "retry": ..., "prefill": ...}

        # ── 预检查：会话中是否有 user 消息可编辑 ──
        messages = getattr(session, 'messages', None) or []
        if not any(m.get("role") == "user" for m in messages):
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u65e0\u7528\u6237\u6d88\u606f\uff0c\u8bf7\u5148\u53d1\u9001\u6d88\u606f\u540e\u518d\u4f7f\u7528 /deitmsg"
                )
            return True

        needs_rerender = False
        restore_text = ""
        try:
            # Layer 2 防御：排空 stdin 残余字节
            flush_stdin(input_instance=chat_ui._input if chat_ui else None)

            # ── 定位最后一条 user 消息 ──
            last_user_idx = -1
            for i in range(len(messages) - 1, -1, -1):
                if messages[i].get("role") == "user":
                    last_user_idx = i
                    break

            if last_user_idx < 0:
                if chat_ui is not None:
                    chat_ui.write_line(
                        f"  {YELLOW}\u26a0{RESET} \u672a\u627e\u5230\u7528\u6237\u6d88\u606f"
                    )
                return True

            from ....tui.pipeline.message_editor import (
                _content_has_nontext,
                _text_part_str,
                _truncate_messages,
            )
            # ★ P0（review 修复）：与 /editmsg 同步——预填只取**纯文本部分**
            #   （``_text_part_str``）。修复前用本模块 ``_content_str`` 拍平：
            #   多模态消息（含图片）的非文本部分被展开为 ``[图片: <url>]``
            #   （provider 常用 base64 data URL，可能极大）注入输入行（垃圾
            #   文本），且重发后非文本部分静默丢失、无任何提示。检测到非文本
            #   内容时记录警告（下方渲染 ⚠ 行，与 editmsg_plugin 一致）。
            old_content_raw = messages[last_user_idx].get("content", "")
            old_content = _text_part_str(old_content_raw)
            if _content_has_nontext(old_content_raw):
                state["_prefill_warning"] = (
                    "\u539f\u6d88\u606f\u542b\u975e\u6587\u672c\u5185\u5bb9"
                    "\uff08\u5982\u56fe\u7247\uff09\uff0c\u7f16\u8f91\u91cd\u53d1"
                    "\u540e\u975e\u6587\u672c\u90e8\u5206\u5c06\u4e22\u5931"
                )

            # ── 恢复沙盒 + 截断 + remap（统一公共助手） ──
            # ★ P1-1 修复（先 remap 后删）：修复前本插件内联「restore → del
            #   messages → remap_indices」顺序——remap 抛异常时消息已删且
            #   prefill 未设置（old_content 只存于局部变量，用户内容永久
            #   丢失），沙盒记录与消息索引不一致且无补偿；界面也不重渲染
            #   （needs_rerender=False）残留已删消息。editmsg 侧同逻辑已在
            #   _truncate_messages（P2-7）修复为「先 remap 后删」，本插件
            #   未同步。现复用同一助手：remap 失败时异常在消息删除**前**
            #   抛出（无中间态），被 except 捕获显示「编辑失败」。
            restore_text = _truncate_messages(session.agent, last_user_idx)

            # ── 设置 prefill ──
            state["prefill"] = old_content
            state["retry"] = False
            session.sync_retry_pending()

            # ── Edit 语义：预填旧内容供用户编辑重发，不是自动续接 ──
            session.reset_retry_pending_for_edit(has_prefill=bool(state["prefill"]))

            # ── 标记需重新渲染 ──
            needs_rerender = True

        except Exception as exc:
            _logger.warning("DeitmsgPlugin 编辑异常: %s", exc, exc_info=True)
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u7f16\u8f91\u5931\u8d25: {exc}"
                )
            needs_rerender = False
        finally:
            if monitor is not None:
                try:
                    session.captured_prefill = ''
                    reset_interrupt_async(
                        input_instance=chat_ui._input if chat_ui else None
                    )
                    monitor.clear_interrupted()
                except Exception:
                    _logger.warning(
                        "DeitmsgPlugin finally 块清理异常", exc_info=True
                    )
            if chat_ui is not None:
                try:
                    chat_ui.flush()
                except Exception:
                    _logger.warning(
                        "DeitmsgPlugin chat_ui.flush() finally 异常", exc_info=True
                    )

        # ── 显示沙盒还原信息并重新渲染 ──
        #    与 /editmsg 同语义：先清空消息区旧显示，再重新渲染剩余消息一次。
        try:
            if needs_rerender and chat_ui is not None:
                input_inst = chat_ui.get_input()
                prefill_text = state.get("prefill", "")
                # ★ prefill 提前注入（2026-08-19，与 /editmsg 同构——
                #   「很多上文时按回车不能编辑对应消息」根因修复）：注入时机
                #   从 wait_for_user_input（在 clear+display 全量重放 + flush
                #   之后，大量上文时 1s~10s）提前到本处——输入框立即显示旧
                #   消息内容（可编辑可提交），重放期间用户按 Enter 提交的是
                #   实际内容（非空提交，wait_for_user_input 直接从队列返回）。
                #   注入后清空 state["prefill"]（已履行注入职责，防
                #   orchestrator 重复注入/拼接覆盖用户已见内容）。
                if prefill_text and input_inst is not None:
                    try:
                        # 窗口期已有存活提交（Enter 已分发的空提交）→ 先消费
                        # 并转 deferred 提交意图（防 set_buffer 清 _input_ready
                        # 丢弃——注入后兑现，一次 Enter 完成编辑）。
                        try:
                            if input_inst.has_queued_input():
                                input_inst.get_queued_input()
                                mark_intent = getattr(
                                    input_inst, "mark_deferred_enter", None,
                                )
                                if callable(mark_intent):
                                    mark_intent()
                        except Exception:
                            _logger.debug(
                                "deitmsg_plugin: 注入前提交转换异常",
                                exc_info=True,
                            )
                        input_inst.set_buffer(prefill_text)
                        input_inst.echo(prefill_text)
                        state["prefill"] = ""
                    except Exception:
                        _logger.debug(
                            "deitmsg_plugin: prefill 提前注入异常",
                            exc_info=True,
                        )
                # 1. 先清空消息区旧显示（删除被编辑消息及其后内容的旧渲染）
                try:
                    chat_ui.clear_messages()
                except Exception as exc:
                    _logger.warning(
                        "DeitmsgPlugin clear_messages 异常: %s", exc
                    )
                # 2. 重新渲染截断后的剩余消息（一次，不追加残留副本）
                try:
                    non_system = _non_system_messages(session)
                    chat_ui.display_messages(non_system, speed=0)
                except Exception as exc:
                    _logger.warning(
                        "DeitmsgPlugin display_messages 异常: %s", exc
                    )
                # 3. 视觉分隔线 + 沙盒还原信息（在 display_messages 之后，避免被消息渲染滚动覆盖）
                # ★ P2-3 修复：恢复失败以 ⚠ 渲染（与 editmsg 统一经
                #   _restore_feedback 判定），不再无条件绿色 ✓。
                chat_ui.write_line(f"  {DIM}{'─' * 40}{RESET}")
                from ....tui.pipeline.message_editor import _restore_feedback
                feedback_text, restore_failed = _restore_feedback(restore_text)
                if restore_failed:
                    chat_ui.write_line(f"  {YELLOW}\u26a0{RESET} {feedback_text}")
                else:
                    chat_ui.write_line(f"  {GREEN}\u2713{RESET} {feedback_text}")

                # ★ P0（review 修复）：多模态消息编辑警告（与 /editmsg 同语义）
                #   ——预填仅含文本部分，非文本部分重发后丢失，显式提示用户。
                prefill_warning = state.get("_prefill_warning", "")
                if prefill_warning:
                    chat_ui.write_line(f"  {YELLOW}\u26a0{RESET} {prefill_warning}")

                # 确保渲染命令在插件返回前排空
                try:
                    chat_ui.flush()
                except Exception:
                    _logger.warning(
                        "DeitmsgPlugin chat_ui.flush() post-finally 异常", exc_info=True
                    )
                # ★ deferred 提交兑现（与 /editmsg 同构）：窗口期（截断 →
                #   prefill 注入前）用户按的 Enter 已转提交意图，prefill 注入
                #   缓冲后自动提交——一次 Enter 完成编辑重发（无需再按一次）。
                #   无存活提交时才兑现（防重复提交）。
                if prefill_text and input_inst is not None:
                    try:
                        if not input_inst.has_queued_input():
                            consume = getattr(
                                input_inst, "consume_deferred_enter", None,
                            )
                            if callable(consume) and consume():
                                input_inst._enter()
                    except Exception:
                        _logger.debug(
                            "deitmsg_plugin: deferred 提交兑现异常",
                            exc_info=True,
                        )
        finally:
            # ★ deferred 残留清理（取消/异常/编辑全路径）：未兑现的提交意图
            #   必须清除——标志泄漏会让下一轮正常 Enter 意外触发自动提交。
            if chat_ui is not None:
                try:
                    input_inst = chat_ui.get_input()
                    if input_inst is not None:
                        consume = getattr(input_inst, "consume_deferred_enter", None)
                        if callable(consume):
                            consume()
                except Exception:
                    _logger.debug(
                        "deitmsg_plugin: deferred 残留清理异常", exc_info=True,
                    )

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 旧命令系统路径友好降级（不抛异常）。

        ★ P2-2 附带修复：registry 自动注册同步 handler——同步路径
        （handle_command）触发本方法。修复前直接 raise RuntimeError 使
        调用方崩溃；现输出提示并返回 True。
        """
        try:
            from ....core.adapters.output import get_default_output_port
            get_default_output_port().write(
                f"  {YELLOW}\u26a0{RESET} /deitmsg \u9700\u8981\u4ea4\u4e92\u5f0f TUI \u73af\u5883\uff0c\u8bf7\u5728 TUI \u4e2d\u4f7f\u7528"
            )
        except Exception:
            _logger.debug("deitmsg_plugin: 同步降级提示输出异常", exc_info=True)
        return True

# 模块级自注册
get_plugin_registry().register(DeitmsgPlugin())