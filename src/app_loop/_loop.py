"""应用主循环 InteractiveLoop — 从 app_loop.py 拆分

封装交互模式主循环（InteractiveLoop 类），
是 CLI 交互对话的核心编排器。
"""

from __future__ import annotations

import asyncio
import logging
import sys

from ._utils import (
    _non_system_messages, _put_and_wait, _merge_prefill,
    _exit_save_and_stop, _save_loop_snapshot,
    _RETRY_SENTINEL,
)
from ._special_keys import make_special_key_callback
from ._session_setup import (
    SessionState, _RoundResult,
    _setup_session, _register_session_handlers,
)
from ._handlers import (
    _handle_retry_sentinel,
)
from ._single import _make_event_agent

from ..config import MODEL
from ..core.session import ChatSession
from ..core.commands import CommandContext
from ..core.commands.plugins import get_interactive_registry
from ..core.message_queue import MessageQueue
from ..core.exceptions import is_fatal_exception, is_network_error
from ..core.constants import CYAN, DIM, RESET, GREEN, YELLOW
from ..tui.terminal.terminal import TerminalWidthCache, narrow_sep_width
from ..api.escape_monitor import EscapeMonitor
from ..api.interrupt_async import reset_interrupt_async
from ..api.stats import reset_token_speed
from ..tui.consumer import ChatUIConsumer

_logger = logging.getLogger(__name__)


class InteractiveLoop:
    """交互模式主循环 — 封装循环状态和编排逻辑"""

    def __init__(self, loaded_data=None):
        self._loaded_data = loaded_data
        self._force_exit = asyncio.Event()
        self._msg_done_ref: asyncio.Event | None = None
        # ChatUI 消费者 — 通过事件系统渲染聊天内容到终端
        self._chat_ui: ChatUIConsumer | None = None
        # ★ EscapeMonitor — 始终开启，捕获所有键盘输入
        self._monitor: EscapeMonitor | None = None
        # ★ 流式输入状态共享：round_end 回调与 _handle_round 之间传递
        self._loop_state: dict = {}
        # ★ EscapeMonitor 恢复计数器（防止无限重启）
        self._monitor_recovery_count = 0

    def _get_term_width(self) -> int:
        return TerminalWidthCache.get_default().get_width()

    def _check_consumer_exception(self, task: asyncio.Task) -> None:
        """检查消费者任务是否有未捕获的异常。异常时设置 _force_exit 并唤醒 msg_done 防止死锁。"""
        if not task.done():
            return
        # 任务取消是正常退出路径，不设置 _force_exit
        if task.cancelled():
            _logger.info("消息队列消费者任务被取消")
            if self._msg_done_ref is not None and not self._msg_done_ref.is_set():
                self._msg_done_ref.set()
            return
        try:
            exc = task.exception()
        except asyncio.InvalidStateError as e:
            _logger.warning("检查消费者异常时出错: %s", e)
            return
        if exc is not None:
            if is_fatal_exception(exc):
                _logger.critical("消息队列消费者致命异常: %s", exc, exc_info=exc)
                self._force_exit.set()
            else:
                _logger.warning("消息队列消费者非致命异常 [non-fatal]: %s", exc, exc_info=exc)
            # 无论致命/非致命，都需要唤醒 msg_done 防止死锁
            if self._msg_done_ref is not None and not self._msg_done_ref.is_set():
                self._msg_done_ref.set()

    async def _handle_round(
        self,
        session,
        state: SessionState,
        queue: MessageQueue,
        msg_done: asyncio.Event,
    ) -> _RoundResult:
        """执行一轮对话交互——所有用户输入（包括命令）统一放入 MessageQueue。

        所有用户输入通过 MessageQueue 投递，与 WebUI 共用同一消息处理机制。
        """
        try:
            # ★ Bug 7: 确保 msg_done 为 cleared 状态，避免异常路径遗留 set 状态导致逻辑混乱
            msg_done.clear()
            # ★ 同步当前模型名到底部栏状态行（覆盖所有可能修改模型的路径）
            self._chat_ui.bottom_bar.set_model_name(state.model)

            # ── retry / retry_pending：放入队列执行（非直接 await） ──
            if state.retry or session.retry_pending:
                state.retry = False
                reset_interrupt_async()
                await _put_and_wait(queue, _RETRY_SENTINEL, msg_done)
                return _RoundResult(should_exit=False)

            # ★ 流式期间用户按了 Enter → 跳过输入提示，直接处理排队输入
            queued = self._loop_state.pop("queued_input", None)
            if queued:
                await _put_and_wait(queue, queued, msg_done)
                return _RoundResult(should_exit=False)

            _tw = self._get_term_width()
            self._chat_ui.write_line(f"{DIM}{'─' * max(min(_tw - 2, 40), 1)}{RESET}")
            # ★ 等待 render 线程处理完分隔线
            self._chat_ui.flush()
            # ★ 显式将光标定位到输入行（flush 返回时 render 线程可能尚未执行 _position_cursor）
            self._chat_ui.bottom_bar.ensure_cursor_in_lower()
            _logger.debug("_handle_round: before _merge_prefill, state.prefill='%s', len=%d", state.prefill[:80] if state.prefill else '', len(state.prefill))
            _cp_len_before = len(session.captured_prefill) if hasattr(session, 'captured_prefill') else 0
            prefill = _merge_prefill(state, session)
            _logger.debug("_handle_round: after _merge_prefill, merged prefill len=%d, captured_prefill len was=%d", len(prefill) if prefill else 0, _cp_len_before)

            # ★ 获取输入前清除残留中断信号
            reset_interrupt_async()

            try:
                user_input = await asyncio.to_thread(
                    self._chat_ui.wait_for_user_input, self._monitor, prefill,
                    input_=self._chat_ui._components.input,
                )
            except RuntimeError as e:
                if "EscapeMonitor" in str(e):
                    _logger.warning("EscapeMonitor I/O 不可用，尝试恢复: %s", e)
                    self._chat_ui.write_line(
                        "\n  ⚠ EscapeMonitor I/O 中断，正在重启…"
                    )
                    for attempt in range(1, 6):
                        try:
                            self._teardown_monitor()
                            self._setup_monitor(session, state)
                            # 验证恢复后 I/O 确实激活
                            if not self._monitor.is_alive:
                                raise RuntimeError(
                                    "EscapeMonitor 恢复后 I/O 未激活"
                                )
                            _register_session_handlers(
                                session, self._monitor,
                                self._loop_state, self._chat_ui,
                            )
                            self._monitor_recovery_count += 1
                            break  # 恢复成功，退出重试循环
                        except Exception as recovery_err:
                            _logger.warning(
                                "EscapeMonitor 恢复尝试 %d/5 失败: %s",
                                attempt, recovery_err,
                            )
                            if attempt >= 5:
                                _logger.critical(
                                    "EscapeMonitor 恢复失败（已重试 5 次）: %s",
                                    recovery_err, exc_info=recovery_err,
                                )
                                self._chat_ui.write_line(
                                    f"\n  [错误] 无法恢复输入监听"
                                    f"（已重试 5 次）: {recovery_err}"
                                )
                                return _RoundResult(should_exit=True)
                            # 指数退避: 1s, 2s, 4s, 8s
                            backoff = min(1.0 * (2 ** (attempt - 1)), 30.0)
                            await asyncio.sleep(backoff)
                            self._chat_ui.write_line(
                                f"\n  ⚠ 正在重试恢复 ({attempt + 1}/5)…"
                            )
                    if self._monitor_recovery_count > 10:
                        _logger.warning(
                            "EscapeMonitor 累计恢复 %d 次，可能存在持续问题",
                            self._monitor_recovery_count,
                        )
                    return _RoundResult(should_exit=False)
                # 非 monitor 死亡的 RuntimeError 重新抛出
                raise
            except (EOFError, KeyboardInterrupt):
                return _RoundResult(should_exit=True)

            _logger.debug("_handle_round: wait_for_user_input returned, len=%d", len(user_input) if user_input else 0)

            # ★ 成功获取输入 → 重置恢复计数器
            self._monitor_recovery_count = 0

            if user_input.strip().lower() == 'exit':
                pending = session.pending_messages
                if pending:
                    self._chat_ui.write_line(f"\n  [警告] 还有 {len(pending)} 条排队消息未处理，已丢弃")
                    session.pop_pending_messages()
                return _RoundResult(should_exit=True)

            if not user_input:
                return _RoundResult(should_exit=False)

            # ── 所有用户输入（包括 / 命令）统一通过 MessageQueue ──
            await _put_and_wait(queue, user_input, msg_done)
            return _RoundResult(should_exit=False)

        except asyncio.CancelledError:
            _logger.warning("对话轮次被取消")
            self._chat_ui.write_line("\n  ⚠ 对话轮次被取消，继续运行")
            self._force_exit.set()
            return _RoundResult(should_exit=True)
        except Exception as e:
            _logger.exception("对话轮次异常")
            self._chat_ui.write_line(f"\n  [错误] {e}，可继续输入")
            if not msg_done.is_set():
                msg_done.set()
            return _RoundResult(should_exit=False)

    async def _handle_loop_cmd(self, content: str, session, state: SessionState) -> None:
        """处理 /loop 命令"""
        parts = content.split(maxsplit=2)
        if len(parts) < 3 or not parts[1].isdigit() or int(parts[1]) < 1:
            self._chat_ui.write_line(f"  {YELLOW}用法: /loop <次数> <提词>{RESET}")
            return
        count = int(parts[1])
        prompt = parts[2].strip()
        if not prompt:
            self._chat_ui.write_line(f"  {YELLOW}用法: /loop <次数> <提词>{RESET}")
            return
        # ── 清理前一轮可能的强制退出标记 ──────────────────────
        self._force_exit.clear()
        # ── 自动保存循环前的对话 ────────────────────────────
        await _save_loop_snapshot(session, self._chat_ui)
        # ── /loop 模式：启用状态行持续活跃 + 跨轮累加耗时 ────
        try:
            # _loop_mode + enable_status + write_line 全部在 try 内，
            # 确保 finally 始终清理 _loop_mode，防止状态泄漏
            self._loop_state["_loop_mode"] = True
            if self._chat_ui is not None:
                self._chat_ui.bottom_bar.enable_status()
            self._chat_ui.write_line(f"  {GREEN}+ 开始循环 {count} 次: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"{RESET}")
            for i in range(count):
                self._chat_ui.write_line(f"  {DIM}  ─ 第 {i+1}/{count} 轮 · 第1次 ─{RESET}")
                # 清空对话（每轮开始前清空）
                reset_interrupt_async()
                session.clear_messages()
                # 第1次运行（含网络错误重试）
                result = await self._run_round_with_retry(session, prompt)
                if result.get("interrupted", False):
                    self._chat_ui.write_line(f"  {YELLOW}+ ESC 中断，提前结束循环（已执行 {i+1}/{count} 轮）{RESET}")
                    break

                # 第2次运行（固定提词"继续完成所有"，含网络错误重试）
                self._chat_ui.write_line(f"  {DIM}  ─ 第 {i+1}/{count} 轮 · 第2次 ─{RESET}")
                reset_interrupt_async()
                result2 = await self._run_round_with_retry(session, "继续完成所有")
                if result2.get("interrupted", False):
                    self._chat_ui.write_line(f"  {YELLOW}+ ESC 中断，提前结束循环（已执行 {i+1}/{count} 轮）{RESET}")
                    break
            else:
                self._chat_ui.write_line(f"  {GREEN}+ 循环 {count} 次执行完毕{RESET}")
        finally:
            # ── /loop 结束：清理 _loop_mode 标志 + 重置状态 ────
            self._loop_state["_loop_mode"] = False
            if self._chat_ui is not None:
                self._chat_ui.bottom_bar.disable_status()
                self._chat_ui.bottom_bar.reset_tool_count()
            reset_token_speed()
        # ── 自动保存循环后的对话 ────────────────────────────
        await _save_loop_snapshot(session, self._chat_ui)

    async def _run_round_with_retry(
        self, session, prompt: str, max_attempts: int = 3,
    ) -> dict:
        """运行 session.run_round()，检测网络错误并自动重试。

        Args:
            session: ChatSession 实例
            prompt: 用户输入文本
            max_attempts: 最大尝试次数（含首次，默认 3）

        Returns:
            run_round 返回的结果字典
        """
        for attempt in range(1, max_attempts + 1):
            # 每轮重试前确保 interrupt 信号已清除
            reset_interrupt_async()

            try:
                result = await session.run_round(prompt)
            except (KeyboardInterrupt, asyncio.CancelledError):
                raise
            except Exception as e:
                if attempt < max_attempts and is_network_error("", e):
                    _logger.warning(
                        "/loop run_round 网络错误 (第%d次重试): %s", attempt, e,
                    )
                    self._chat_ui.write_line(
                        f"  {YELLOW}+ 检测到网络错误，正在重试 ({attempt}/{max_attempts})...{RESET}"
                    )
                    # 追加"[继续]"消息通知模型重试
                    try:
                        session.add_user_message("【继续】网络错误已恢复，请重试")
                    except Exception:
                        _logger.exception("追加重试消息失败")
                    continue
                raise  # 非网络错误直接抛出

            # ── 检查最后一条 assistant 消息是否包含网络错误 ──
            interrupted = result.get("interrupted", False)
            if not interrupted and session.messages:
                last_msg = session.messages[-1] or {}
                if last_msg.get("role") == "assistant":
                    last_content = last_msg.get("content", "") or ""
                    if is_network_error(last_content, None):
                        if attempt < max_attempts:
                            _logger.warning(
                                "/loop 返回内容含网络错误 (第%d次重试): %s",
                                attempt, last_content[:100],
                            )
                            self._chat_ui.write_line(
                                f"  {YELLOW}+ 检测到网络错误，正在重试 ({attempt}/{max_attempts})...{RESET}"
                            )
                            try:
                                session.add_user_message("【继续】网络错误已恢复，请重试")
                            except Exception:
                                _logger.exception("追加重试消息失败")
                            continue
                        else:
                            _logger.error(
                                "/loop 网络错误重试 %d 次仍失败，跳过本轮: %s",
                                max_attempts, last_content[:200],
                            )
                            self._chat_ui.write_line(
                                f"  {YELLOW}+ 网络错误重试 {max_attempts} 次仍失败，跳过本轮{RESET}"
                            )
                            return {"interrupted": True, "session_id": None,
                                    "delta": {"input": 0, "output": 0, "calls": 0}}

            return result

        # 不会执行到这里（return result 在循环内），类型占位
        return {"interrupted": True}

    async def _handle_regular_msg(self, content: str, session, state: SessionState) -> None:
        """处理普通用户消息"""
        reset_interrupt_async()
        # ★ 重置工具计数（新轮开始）
        if self._chat_ui is not None:
            self._chat_ui.bottom_bar.reset_tool_count()
        # 通过 ChatUI 打印用户消息
        if self._chat_ui is not None:
            self._chat_ui.on_user_message(content)
        await session.run_round(content)
        # ★ Bug3 修复：首轮消息完成后保存 checkpoint，确保异常时已成功处理的消息不丢失
        try:
            session.save_checkpoint()
        except Exception:
            _logger.exception("_handle_regular_msg: save_checkpoint 异常，不阻断消息处理")
        state.model = session.model
        if self._chat_ui is not None:
            self._chat_ui.bottom_bar.set_model_name(state.model)
        breached, _ = await session.run_pending_loop(max_iter=10)
        if breached:
            self._chat_ui.write_line(f"\n  [错误] 系统繁忙，部分消息未能处理，请重新发送")
            session._force_state_recovery()
        # ★ 等待 ChatUI 渲染完所有待处理命令（工具输出/汇总等）
        #   确保输入提示符出现在完整渲染内容之后，不重叠。
        if self._chat_ui is not None:
            await asyncio.to_thread(self._chat_ui.flush)

    async def _handle_command_msg(self, content: str, session, state: SessionState) -> None:
        """处理 / 命令分发 — 统一走插件路径"""
        reset_interrupt_async()
        cmd_name = content.split()[0].lower()

        # 插件命令分发（统一路径）
        registry = get_interactive_registry()
        plugin = registry.get(cmd_name)
        if plugin is not None:
            # 确保插件绑定了当前 loop 实例（仅 InteractiveCommandPlugin 子类有此属性）
            if hasattr(plugin, 'loop') and hasattr(plugin, 'bind_loop'):
                if plugin.loop is None or plugin.loop is not self:
                    plugin.bind_loop(self)
            state_dict = {"model": state.model, "retry": False, "prefill": ""}
            arg = content.split(maxsplit=1)[1] if len(content.split(maxsplit=1)) > 1 else ""
            from ..core.commands import CommandUiAdapter
            _ui_adapter = CommandUiAdapter()
            input_ = self._chat_ui._components.input
            ctx = CommandContext(
                messages=session.messages, state=state_dict, arg=arg,
                build_system_prompt=session.agent.build_system_prompt,
                get_user_input=lambda prompt="": self._chat_ui.wait_for_user_input(
                    self._monitor, prefill=prompt, input_=input_),
                context_manager=session.context_manager,
                session=session,
                ui_adapter=_ui_adapter,
            )
            # 优先 async_execute（InteractiveCommandPlugin 子类/CompressCommand 有），
            # 否则回退到同步 execute（纯 CommandPlugin 子类）
            if hasattr(plugin, 'async_execute'):
                handled = await plugin.async_execute(ctx)
            else:
                handled = await asyncio.to_thread(plugin.execute, ctx)
            if handled:
                state.model = state_dict.get("model", state.model)
                self._chat_ui.bottom_bar.set_model_name(state.model)
                state.retry = state_dict.get("retry", False)
                state.prefill = state_dict.get("prefill", "")
                _logger.debug("_handle_command_msg: state.prefill set, len=%d, retry=%s", len(state.prefill), state.retry)
            else:
                self._chat_ui.write_line(f"  {YELLOW}未知命令: {content}，输入 /help 查看可用命令{RESET}")
        else:
            self._chat_ui.write_line(f"  {YELLOW}未知命令: {content}，输入 /help 查看可用命令{RESET}")

    async def _cli_msg_consumer(self, msg, session, state: SessionState, msg_done: asyncio.Event) -> None:
        """CLI 消息消费者：处理放入队列的所有消息（命令 + 普通消息）。"""
        try:
            content = msg.content

            # ── retry 哨兵 ──
            if content is _RETRY_SENTINEL:
                await _handle_retry_sentinel(session)
                return

            # ── / 命令 ──
            if isinstance(content, str) and content.startswith('/'):
                await self._handle_command_msg(content, session, state)
                return

            # ── 普通用户消息 ──
            await self._handle_regular_msg(content, session, state)
        except asyncio.CancelledError:
            _logger.info("CLI 消息消费者被取消")
            raise
        except Exception as exc:
            if is_fatal_exception(exc):
                _logger.critical("CLI 消息消费者致命异常: %s", exc, exc_info=exc)
                self._force_exit.set()
            else:
                _logger.warning("CLI 消息消费者非致命异常 [non-fatal]: %s", exc, exc_info=exc)
                # ★ 给用户可见错误消息，终结"静默无响应"问题
                if self._chat_ui is not None:
                    try:
                        self._chat_ui.write_line(
                            f"  {YELLOW}\u26a0{RESET} \u5904\u7406\u6d88\u606f\u65f6\u51fa\u9519: {exc}"
                        )
                    except Exception:
                        _logger.warning("非致命异常用户反馈写入失败", exc_info=True)
        finally:
            if not msg_done.is_set():
                msg_done.set()

    # ── UI 生命周期管理 ──────────────────────────────────

    def _setup_chat_ui(self):
        """初始化 ChatUI 消费者并显示启动信息"""
        self._chat_ui = ChatUIConsumer()
        self._chat_ui.start()

        _term_width = self._get_term_width()
        _sep_width = narrow_sep_width(40)

    def _teardown_chat_ui(self):
        """停止 ChatUI 消费者"""
        if self._chat_ui is not None:
            self._chat_ui.stop()
            self._chat_ui = None

    # ── EscapeMonitor 生命周期管理 ───────────────────────

    def _create_monitor(self):
        """创建 EscapeMonitor 实例（仅创建，不绑定回调，不启动）。

        传入共享的 Input 实例，EscapeMonitor 仅负责原始 I/O，
        所有输入事件在 render 线程中通过 read_stdin_once() 直接分发。

        必须在 _register_session_handlers 之前调用，确保 _setup_session_and_handlers
        中 _register_session_handlers 传入的 self._monitor 为非 None 的 EscapeMonitor。
        """
        input_instance = self._chat_ui._components.input
        self._monitor = EscapeMonitor(input_instance=input_instance)

    def _setup_monitor(self, session, state):
        """初始化 EscapeMonitor 并注册回调（假设 self._monitor 已由 _create_monitor 创建）

        防御性重建：若 monitor 为 None，或 monitor 曾启动后 is_alive 为 False，
        先清理再创建新实例。首次调用时 _create_monitor 已创建的未启动
        monitor（_started=False）不会被误判为需要重建，确保
        _register_session_handlers 闭包中持有的引用与最终启动的是同一实例。

        回调注册在 Input 实例上（render 线程中统一分发），
        EscapeMonitor 仅负责原始 I/O。
        """
        input_instance = self._chat_ui._components.input
        if self._monitor is None or (self._monitor._started and not self._monitor.is_alive):
            if self._monitor is not None:
                try:
                    self._teardown_monitor()
                except Exception:
                    _logger.debug(
                        "_setup_monitor: teardown 已死 monitor 时异常 (预期内)",
                        exc_info=True,
                    )
            self._monitor = EscapeMonitor(input_instance=input_instance)
        # 回调设置在 Input 实例上（render 线程中统一分发）
        input_instance.set_special_key_callback(
            make_special_key_callback(self, session, state, self._chat_ui, monitor=self._monitor)
        )
        input_instance.set_echo_callback(
            lambda text, cursor_pos=-1: self._chat_ui.refresh_bottom_bar(text, cursor_pos)
        )
        if self._chat_ui is not None:
            self._chat_ui.setup_completion(input_instance)
        self._monitor.start()

    def _teardown_monitor(self):
        """停止 EscapeMonitor 并恢复终端设置"""
        if self._monitor is not None:
            self._monitor.stop()
            self._monitor = None

    # ── 会话生命周期辅助 ──────────────────────────────────

    def _setup_session_and_handlers(self, loaded_data):
        """初始化会话并注册事件处理器"""
        session, state = _setup_session(loaded_data, self._chat_ui)
        self._chat_ui.bottom_bar.set_model_name(state.model)
        self._chat_ui.setup_bottom_bar()
        _register_session_handlers(session, self._monitor, self._loop_state, self._chat_ui)
        return session, state

    async def run(self) -> None:
        """执行交互模式主循环"""
        self._force_exit.clear()

        # ── 初始化 ChatUI ──
        self._setup_chat_ui()

        # ── 创建 EscapeMonitor 实例（需在 _setup_session_and_handlers 之前，
        #    确保 _register_session_handlers 传递的 monitor 非 None） ──
        self._create_monitor()

        # ── 初始化会话 ──
        session, state = self._setup_session_and_handlers(self._loaded_data)

        # ── 初始化 EscapeMonitor 回调并启动 ──
        self._setup_monitor(session, state)

        # ── 创建 MessageQueue + 消费者 ──
        queue = MessageQueue()
        msg_done = asyncio.Event()
        self._msg_done_ref = msg_done

        consume_task = asyncio.create_task(queue.async_consume(lambda msg: self._cli_msg_consumer(msg, session, state, msg_done)))
        consume_task.add_done_callback(self._check_consumer_exception)

        try:
            while True:
                if self._force_exit.is_set():
                    _exit_save_and_stop(session, self._chat_ui)
                    break
                result = await self._handle_round(session, state, queue, msg_done)
                if result.should_exit:
                    _exit_save_and_stop(session, self._chat_ui)
                    break
        except BaseException as exc:
            if isinstance(exc, SystemExit):
                raise
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                _logger.info("交互模式被用户中断")
            else:
                _logger.critical("交互模式未捕获异常", exc_info=True)
            try:
                _exit_save_and_stop(session, self._chat_ui)
            except Exception:
                _logger.exception("异常路径保存会话失败")
            _logger.info("异常路径：终端设置已恢复")
            raise
        finally:
            self._msg_done_ref = None
            self._teardown_monitor()
            self._teardown_chat_ui()
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass


async def run_interactive_mode_async(loaded_data: dict | None = None):
    """交互式对话模式（异步版）"""
    loop = InteractiveLoop(loaded_data)
    await loop.run()
