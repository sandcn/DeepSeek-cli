"""EditmsgPlugin — 编辑当前会话消息 (/editmsg, Ctrl+O)

保持 ChatUIConsumer + EscapeMonitor 运行，
通过底部栏补全弹窗 + render 线程 ↑↓/Enter 交互，
选择完成后 prefill 走正常 state.prefill → _merge_prefill → wait_for_user_input 路径。

prefill 数据流（正常路径）:

  1. MessageEditor.edit_current_messages — 截断消息 + edit_state["prefill"] = 旧内容
  2. editmsg_plugin.py:async_execute — state["prefill"] = edit_state.get("prefill", "")
     ★ finally 块不再清除 state["prefill"]，保留给 _merge_prefill 处理
  3. _loop.py:_handle_command_msg — 从 state_dict 同步到 state.prefill（非空）
  4. _loop.py:_handle_round — _merge_prefill(state, session) 合并 prefill：
     - 读取 state.prefill → 清除 state.prefill
     - session.captured_prefill 已在 finally 块中清除（防前一回合残留）
     - 返回编辑后的消息内容
  5. consumer.py:wait_for_user_input — 从参数接收 prefill，调用 set_buffer 注入输入行
"""

from __future__ import annotations

import logging
from typing import Any

from .base import InteractiveCommandPlugin
from ..base import CommandMeta, get_plugin_registry
from ....api.interrupt_async import flush_stdin, reset_interrupt_async
from ....core.constants import YELLOW, RESET, GREEN, DIM

_logger = logging.getLogger(__name__)


class EditmsgPlugin(InteractiveCommandPlugin):
    """编辑当前会话消息 (Ctrl+O)

    保持 ChatUIConsumer + EscapeMonitor 运行，
    让底部栏补全弹窗 + render 线程 ↑↓/Enter 交互，
    选择完成后 prefill 走正常 state.prefill → _merge_prefill 路径。
    """

    def __init__(self):
        super().__init__()
        self.meta = CommandMeta(
            name="editmsg",
            description="编辑当前会话消息 (Ctrl+O)",
        )

    async def async_execute(self, ctx: Any) -> bool:
        """异步执行 /editmsg 命令

        使用底部栏补全弹窗进行交互式消息选择（↑↓/Enter）。
        不执行 chat_ui.suspend()（重构后 suspend 会拆除 _BottomBar），
        而是保持 render 线程运行，在补全弹窗中完成选择。
        """
        from ....app_loop import _non_system_messages
        from ....tui.pipeline.message_editor import MessageEditor

        loop = self._loop
        if loop is None:
            _logger.error("EditmsgPlugin 未绑定 InteractiveLoop")
            return False

        chat_ui = loop._chat_ui
        monitor = loop._monitor
        session = ctx.session
        state = ctx.state  # dict: {"model": ..., "retry": ..., "prefill": ...}

        # ── 预检查：会话中是否有 user 消息可编辑 ──
        has_user_msg = any(
            m.get("role") == "user"
            for m in getattr(session, 'messages', []) or []
        )
        if not has_user_msg:
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u5f53\u524d\u4f1a\u8bdd\u65e0\u7528\u6237\u6d88\u606f\uff0c\u8bf7\u5148\u53d1\u9001\u6d88\u606f\u540e\u518d\u4f7f\u7528 /editmsg"
                )
            return True

        needs_rerender = False
        saved_buffer = ""
        try:
            # ★ 不执行 chat_ui.suspend() — 保持 render 线程 + _BottomBar 运行
            # ★ 不执行 monitor.stop() — 保持 cbreak 模式供 render 线程驱动 ↑↓/Enter
            # Layer 2 防御：进入选择界面前排空 stdin 残余字节
            flush_stdin(input_instance=chat_ui.get_input() if chat_ui else None)

            edit_state = {"model": state.get("model", ""), "retry": False, "prefill": ""}
            bottom_bar = chat_ui.bottom_bar if chat_ui is not None else None
            input_ = chat_ui.get_input() if chat_ui is not None else None

            editor = MessageEditor(bottom_bar=bottom_bar, input_=input_)
            # ★ 编辑逻辑同步直接执行（用户需求：不用 run_in_executor 线程池）——
            #   交互选择期间主协程阻塞在内部轮询（render 线程独立驱动
            #   UserSelectPopup 组件写 done），按回车确认后编辑立即生效，
            #   不依赖线程调度返回（去除多线程 + await 延迟）。
            edited = editor.edit_current_messages(
                session.agent, edit_state, "edit",
            )

            if edited:
                state["prefill"] = edit_state.get("prefill", "")
                _logger.debug("editmsg_plugin: state['prefill'] set, len=%d", len(state["prefill"]))
            # ★ 将 restore_text 的提取移到 if edited 外部，使其在 needs_rerender 块中可用
            restore_text = edit_state.get("_restore_text", "")
            state["retry"] = edit_state.get("retry", False)
            state["model"] = edit_state.get("model", state.get("model", ""))
            session.sync_retry_pending()

            # ★ Edit 语义：预填旧内容供用户编辑重发，不是自动续接。
            #    无条件重置 retry_pending，与 deitmsg_plugin.py 保持一致。
            session.reset_retry_pending_for_edit(has_prefill=bool(state["prefill"]))

            # ★ 编辑生效后标记需重新渲染：_edit_performed 独立于 prefill 是否为空，
            #   确保空内容编辑的沙盒信息也能显示
            needs_rerender = bool(edit_state.get("_edit_performed", False) or state["retry"] or state["prefill"])
        except Exception as exc:
            _logger.warning("EditmsgPlugin 编辑异常: %s", exc, exc_info=True)
            if chat_ui is not None:
                chat_ui.write_line(
                    f"  {YELLOW}\u26a0{RESET} \u7f16\u8f91\u5931\u8d25: {exc}"
                )
            needs_rerender = False
        finally:
            if monitor is not None:
                try:
                    # ★ 清除 captured_prefill（防前一回合残留）
                    #    注意：不在此处清空 state["prefill"]，保留给
                    #    _merge_prefill(state, session) 在下一回合正常读取。
                    #    也不在此处调用 monitor.start()（monitor 从未被停止，
                    #    start() 内部的 _input.reset() 会与 render 线程竞态）。
                    session.captured_prefill = ''

                    # ★ 清除 _input_ready 残留（防 cascading 效应导致需按多次 Enter）
                    #    确保即使有未处理的 Enter 事件残留，_input_ready 也会被清除，
                    #    防止下一轮 wait_for_user_input 立即返回空字符串。
                    if chat_ui is not None:
                        input_inst = chat_ui.get_input()
                        if input_inst is not None:
                            try:
                                # ★ 窗口期提交意图转换（2026-08-19，editmsg
                                #   「很多上文时按回车不能编辑」根因修复）：
                                #   清除前若已有排队提交（用户在「弹窗关闭 →
                                #   prefill 注入前」窗口按的 Enter 产生的空
                                #   提交——缓冲恒空），先转为 deferred 提交
                                #   意图——修复前直接清除 = 无痕丢弃，用户
                                #   「按回车没反应，要再按一次」；注入 prefill
                                #   后消费兑现（自动提交）。
                                try:
                                    if input_inst.has_queued_input():
                                        mark_intent = getattr(
                                            input_inst, "mark_deferred_enter", None,
                                        )
                                        if callable(mark_intent):
                                            mark_intent()
                                except Exception:
                                    _logger.debug(
                                        "editmsg_plugin: 窗口期提交意图转换异常",
                                        exc_info=True,
                                    )
                                with input_inst._lock:
                                    input_inst._input_ready.clear()
                                    input_inst._submitted_text = ""
                            except Exception:
                                _logger.debug("editmsg_plugin: 清除 _input_ready 残留时异常", exc_info=True)
                            # 双重保障：清除残留的 submitted_text 和 buffer
                            # 即使 _input_ready 清除后仍有 Enter 被处理设置 _submitted_text，
                            # drain_all() 也会将其清空
                            # ★ P2-5 修复（窗口期输入保留）：drain_all 返回
                            #   (submitted, buffer)——保存 buffer（用户在「确认
                            #   选择后 → prefill 注入前」窗口期键入的字符），
                            #   渲染完成后经 handle_chars 写回，下一轮
                            #   wait_for_user_input 与 prefill 拼接（不再静默
                            #   丢弃窗口期输入）。
                            try:
                                drained = input_inst.drain_all()
                                if drained and len(drained) > 1 and drained[1]:
                                    saved_buffer = drained[1]
                            except Exception:
                                _logger.debug("editmsg_plugin: drain_all 异常", exc_info=True)

                    reset_interrupt_async(input_instance=chat_ui.get_input() if chat_ui else None)
                    monitor.clear_interrupted()
                except Exception:
                    _logger.warning("finally 块清理异常", exc_info=True)
            if chat_ui is not None:
                try:
                    chat_ui.flush()
                except Exception:
                    _logger.warning("chat_ui.flush() 在 finally 中异常", exc_info=True)

        # ★ 编辑后反馈：编辑失败（未产生 prefill/retry）时给用户明确提示
        if not needs_rerender and chat_ui is not None:
            chat_ui.write_line(
                f"  {YELLOW}\u26a0{RESET} \u672a\u7f16\u8f91\u4efb\u4f55\u6d88\u606f\uff0c\u5df2\u53d6\u6d88"
            )

        # ★ 编辑生效后：清空消息区旧显示 → 重新渲染剩余消息 → 显示沙盒恢复提示。
        #    用户需求（/editmsg TUI）：按下回车确认选择后，删除消息区原来显示的
        #    信息（含被编辑消息及其后内容的旧渲染），把剩下信息重新渲染一次，
        #    再进入 prefill 编辑输入行。
        try:
            if needs_rerender and chat_ui is not None:
                input_inst = chat_ui.get_input()
                prefill_text = state.get("prefill", "")
                # ★ prefill 提前注入（2026-08-19，editmsg「很多上文时按回车不能
                #   编辑对应消息」根因修复——注入时机从 wait_for_user_input
                #   （在 clear+display 全量重放 + flush 之后，大量上文时
                #   1s~10s）提前到本处）：输入框**立即**显示旧消息内容
                #   （可编辑可提交），重放期间用户按 Enter 提交的是实际内容
                #   （非空提交，wait_for_user_input 直接从队列返回）——一次
                #   Enter 完成编辑。注入后清空 state["prefill"]（已履行
                #   注入职责，防 orchestrator 重复注入/拼接覆盖用户已见内容）。
                if prefill_text and input_inst is not None:
                    try:
                        # 窗口期已有存活提交（Enter 已分发）→ 先消费并转
                        # deferred 意图（防 set_buffer 清 _input_ready 丢弃）
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
                                "editmsg_plugin: 注入前提交转换异常", exc_info=True,
                            )
                        merged = prefill_text + (saved_buffer or "")
                        input_inst.set_buffer(merged)
                        input_inst.echo(merged)
                        state["prefill"] = ""
                    except Exception:
                        _logger.debug(
                            "editmsg_plugin: prefill 提前注入异常", exc_info=True,
                        )
                # 1. 先清空消息区旧显示（ClearMsgsCmd + DisplayMsgsCmd 同批按序处理）
                chat_ui.clear_messages()
                # 2. 重新渲染截断后的剩余消息（一次，不追加残留副本）
                non_system = _non_system_messages(session)
                chat_ui.display_messages(non_system, speed=0)
                # 3. 显示沙盒恢复提示（在 display_messages 之后，避免被消息渲染滚动覆盖）
                chat_ui.write_line(f"  {DIM}{'─' * 40}{RESET}")
                # ★ P2-3 修复：恢复失败（降级继续编辑语义）以 ⚠ 黄色渲染——
                #   修复前无条件 GREEN ✓ 把「沙盒恢复失败: …」显示成成功结果。
                from ....tui.pipeline.message_editor import _restore_feedback
                feedback_text, restore_failed = _restore_feedback(restore_text)
                if restore_failed:
                    chat_ui.write_line(f"  {YELLOW}\u26a0{RESET} {feedback_text}")
                else:
                    chat_ui.write_line(f"  {GREEN}\u2713{RESET} {feedback_text}")
                # ★ P3-3：多模态消息编辑警告（EditCommand 检测非文本 content）
                prefill_warning = edit_state.get("_prefill_warning", "")
                if prefill_warning:
                    chat_ui.write_line(f"  {YELLOW}\u26a0{RESET} {prefill_warning}")
                chat_ui.flush()
                # ★ deferred 提交兑现（2026-08-19 根因修复）：重放期间被吞/
                #   被丢的 Enter（capture 激活期记录为提交意图）在 prefill
                #   已注入缓冲后自动提交——用户「弹窗确认后按的那次 Enter」
                #   直接完成编辑重发（无需再按一次）。无存活提交时才兑现
                #   （防用户重放期间又实际按 Enter 提交造成重复提交）。
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
                            "editmsg_plugin: deferred 提交兑现异常", exc_info=True,
                        )
        finally:
            # ★ capture 收尾（取消/异常/编辑全路径）：关闭窗口期 Enter 捕获 +
            #   清 deferred 残留——修复前标志泄漏会让下一轮正常 Enter 提交
            #   意外触发/残留状态错乱。取消路径（needs_rerender=False）也
            #   经此清理。
            if chat_ui is not None:
                try:
                    input_inst = chat_ui.get_input()
                    if input_inst is not None:
                        set_capture = getattr(input_inst, "set_enter_capture", None)
                        if callable(set_capture):
                            set_capture(False)
                        consume = getattr(input_inst, "consume_deferred_enter", None)
                        if callable(consume):
                            consume()
                except Exception:
                    _logger.debug(
                        "editmsg_plugin: capture 收尾异常", exc_info=True,
                    )

        return True

    def execute(self, ctx: Any) -> bool:
        """同步版本 — 旧命令系统路径友好降级（不抛异常）。

        ★ P2-2 附带修复：CommandPluginRegistry.register 自动把插件的
        execute 注册进旧命令表（``_commands["/editmsg"]``）——同步路径
        （handle_command，如非 TUI 调用方）会触发本方法。修复前直接
        raise RuntimeError 使调用方崩溃；现输出提示并返回 True（命令已
        处理——TUI 主循环走 async_execute，不受影响）。
        """
        try:
            from ....core.adapters.output import get_default_output_port
            get_default_output_port().write(
                f"  {YELLOW}\u26a0{RESET} /editmsg \u9700\u8981\u4ea4\u4e92\u5f0f TUI \u73af\u5883\uff0c\u8bf7\u5728 TUI \u4e2d\u4f7f\u7528\uff08Ctrl+O\uff09"
            )
        except Exception:
            _logger.debug("editmsg_plugin: 同步降级提示输出异常", exc_info=True)
        return True


# 模块级自注册
get_plugin_registry().register(EditmsgPlugin())
