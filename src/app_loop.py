"""应用主循环模块 — 从 app.py 拆分而来

包含：会话状态、轮次处理、交互式/单次模式主循环。
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from src._compat import dataclass
from typing import Any

from .config import MODEL
from .core.session import ChatSession
from .core.agent import Agent
from .core.commands import handle_command
from .core.message_queue import MessageQueue

from .core.constants import CYAN, DIM, RESET, GREEN, YELLOW
from .chat_ui.components.message_editor import MessageEditor
from .chat_ui.infrastructure.message_display import _display_messages
from .ui.common.ttl_cache import TTLCache
from .chat_msgs import save_session, get_recover_cmd
from .paths import CHAT_MSGS_DIR
from .chat_ui.infrastructure.terminal_utils import is_narrow, narrow_sep_width
from .api.escape_monitor import EscapeMonitor, get_active_monitor, stop_active_monitor
from .api.interrupt_async import reset_interrupt_async
from .api.stats import reset_token_speed
from .notifications import notify_chat_completed
from .chat_ui import ChatUIConsumer
_logger = logging.getLogger(__name__)


# ── 辅助函数 ──

def _non_system_messages(session) -> list[dict]:
    """返回非 system 角色的消息列表。"""
    return [m for m in session.messages if m.get('role') != 'system']


# ── 会话状态 ──

@dataclass(slots=True)
class SessionState:
    """会话状态 — 替代 TypedDict，提供运行时类型安全"""
    model: str = ""
    retry: bool = False
    prefill: str = ""


@dataclass(slots=True)
class _RoundResult:
    """单轮交互返回值"""
    should_exit: bool = False
    result: Any = None


# 重试哨兵 — 放入 MessageQueue 表示执行 session.retry()
_RETRY_SENTINEL = object()


async def _put_and_wait(queue: MessageQueue, msg: object, msg_done: asyncio.Event) -> None:
    """将消息放入队列并等待消费者处理完成。

    封装了 queue.put() + msg_done.wait() + msg_done.clear() 三步操作，
    消除 _handle_round 中的重复代码。
    """
    await queue.put(msg)
    if not msg_done.is_set():
        await msg_done.wait()
    msg_done.clear()


# ── 会话初始化 ──

def _setup_session(loaded_data: dict | None = None, chat_ui: "ChatUIConsumer | None" = None) -> tuple:
    """初始化会话并加载历史消息"""
    session = ChatSession(agent=_make_event_agent())
    session.initialize()

    state: SessionState = SessionState(model=session.model)

    if loaded_data:
        data = session.load(loaded_data["id"])
        if data:
            model = data.get("model", session.model)
            session.model = model
            state.model = model
            non_system = _non_system_messages(session)
            _display_messages(non_system, session.agent, speed=0)
            if session.retry_pending and chat_ui is not None:
                chat_ui.write_line(f"  {DIM}  最后一条是用户消息，将自动继续生成回复…{RESET}")

    return session, state


# ── Monitor 回调工厂 ──

def _make_round_callbacks(
    session: "ChatSession",
    monitor: EscapeMonitor,
    loop_state: dict,
    chat_ui: "ChatUIConsumer | None" = None,
) -> dict:
    """创建 round_start / round_end 回调函数

    loop_state: 与 InteractiveLoop 共享的字典，用于 round_end 回调
                将流式期间的排队输入传递给主循环。
    chat_ui: ChatUIConsumer 实例，用于管理底部栏和渲染协调。
    """

    def _on_round_start():
        # ★ 重置轮次耗时（⏱从 0 开始计时）
        reset_token_speed()
        # ★ 激活底部栏状态行刷新（⏱耗时│总tok│实时tok/s）
        if chat_ui is not None:
            chat_ui.bottom_bar.enable_status()

    def _on_round_end(interrupted=False, delta=None, **kw):
        # ★ 冻结底部栏状态行（定格最终数值），同时获取耗时供通知复用
        notify_elapsed = kw.get("elapsed", 0.0)
        if chat_ui is not None:
            chat_ui.bottom_bar.disable_status()
            chat_ui.request_bottom_redraw()
            status_elapsed = chat_ui.bottom_bar.get_status_elapsed()
            if status_elapsed > 0:
                notify_elapsed = status_elapsed

        # ★ 排出流式输入：queued（Enter提交）优先 → 跳过下轮输入提示
        #   buffer_text（未提交）→ 作为 prefill
        queued, buffer_text = monitor.drain_stream_input()
        if queued:
            loop_state["queued_input"] = queued
        elif buffer_text:
            clean = ''.join(c for c in buffer_text
                            if c.isprintable() or c in ('\n', '\t'))
            if clean:
                session.captured_prefill = clean

        # ★ 原有逻辑：保存非可打印控制字符
        captured = monitor.drain_captured_input()
        if captured:
            session.captured_prefill = captured

        # 桌面通知
        notify_chat_completed(session.messages, elapsed=notify_elapsed)

    return {"on_start": _on_round_start, "on_end": _on_round_end}


def _register_session_handlers(
    session: "ChatSession",
    monitor: EscapeMonitor,
    loop_state: dict | None = None,
    chat_ui: "ChatUIConsumer | None" = None,
) -> None:
    """注册会话生命周期回调（round_start / round_end）"""
    if loop_state is None:
        loop_state = {}
    callbacks = _make_round_callbacks(session, monitor, loop_state, chat_ui)
    session.on("round_start", callbacks["on_start"])
    session.on("round_end", callbacks["on_end"])


# ── 辅助：prefill 合并 ──

def _merge_prefill(state: SessionState, session: "ChatSession") -> str:
    """合并预填文本：将 LLM 生成期间捕获的用户键入与 state.prefill 合并。"""
    prefill = state.prefill
    state.prefill = ""
    captured = session.captured_prefill
    if captured:
        captured = ''.join(c for c in captured if c.isprintable() or c in ('\n', '\t'))
        if captured:
            prefill = (captured + " " + prefill).strip() if prefill else captured
        session.captured_prefill = ''
    return prefill


# ── 交互模式子函数（从 run_interactive_mode_async 提升）──

async def _handle_retry_sentinel(session: "ChatSession") -> None:
    """处理 retry 哨兵"""
    await session.retry()


async def _handle_editmsg_cmd(session: "ChatSession", state: SessionState) -> None:
    """处理 /editmsg 命令

    停止 EscapeMonitor（join 线程、恢复 cooked 终端），执行后恢复。
    不暂停 ChatUIConsumer——底部栏保持活跃，用于消息选择弹窗。
    确保消息编辑器的底部栏交互不被 EscapeMonitor 的终端监听干扰。
    """
    from .chat_ui import get_active_chat_ui
    from .api.escape_monitor import get_active_monitor
    chat_ui = get_active_chat_ui()
    monitor = get_active_monitor()
    if monitor is not None:
        monitor.stop()
    _needs_rerender = False
    try:
        edit_state = {"model": state.model, "retry": False, "prefill": ""}
        await asyncio.to_thread(
            MessageEditor().edit_current_messages, session.agent, edit_state,
            chat_ui.bottom_bar if chat_ui else None,
        )
        state.prefill = edit_state.get("prefill", "")
        state.retry = edit_state.get("retry", False)
        state.model = edit_state.get("model", state.model)
        session.sync_retry_pending()

        # ★ 编辑生效（retry=True）后，标记需重新渲染剩余消息到上屏
        _needs_rerender = bool(state.retry or state.prefill)
    finally:
        if monitor is not None:
            monitor.start()

    # ★ 编辑后重新渲染剩余消息到上屏（scroll 区域内）
    # 通过 ChatUI 的 command queue 统一渲染，避免直接 stdout 写入
    # 与 render 线程（_drain_queue → force_redraw）的并发竞态。
    if _needs_rerender and chat_ui is not None:
        from .core.commands_data import filter_non_system as _filter_non_system
        non_system = _filter_non_system(session.messages)
        chat_ui.display_messages(non_system, speed=0)


async def _handle_model_cmd(
    content: str,
    session: "ChatSession",
    state: SessionState,
) -> None:
    """处理 /model 命令（无参数时使用底部栏补全弹窗交互选择）

    暂停 ChatUIConsumer + 停止 EscapeMonitor（join 线程、恢复 cooked 终端），
    让底部栏补全弹窗 + raw I/O 处理 ↑↓/Enter/Esc 交互，选择完成后恢复两者。
    使用 stop/start 而非 pause/resume，确保终端状态确定后再接管 stdin。
    """
    from .chat_ui import get_active_chat_ui
    chat_ui = get_active_chat_ui()
    monitor = get_active_monitor()
    if chat_ui is not None:
        chat_ui.suspend()
    if monitor is not None:
        monitor.stop()
    try:
        state_dict = {"model": state.model, "retry": False, "prefill": ""}

        # ★ 流式输入闭包：此路径不会调用 get_user_input，仅做安全兜底
        def _stream_input(default: str = "", show_prompt: bool = True) -> str:
            return default

        cmd_handled = await asyncio.to_thread(
            handle_command,
            content, session.messages, state_dict,
            session.agent.build_system_prompt,
            _stream_input,
            session.context_manager,
            session,
        )
        if cmd_handled:
            new_model = state_dict.get("model")
            if new_model and new_model != session.model:
                session.model = new_model
            state.model = state_dict.get("model", state.model)
            state.retry = state_dict.get("retry", False)
            state.prefill = state_dict.get("prefill", "")
    finally:
        if monitor is not None:
            monitor.start()
        if chat_ui is not None:
            chat_ui.resume()


async def _save_loop_snapshot(session: "ChatSession", chat_ui: "ChatUIConsumer | None" = None) -> None:
    """保存 /loop 循环前后的对话快照"""
    non_system = _non_system_messages(session)
    if non_system:
        sid = await asyncio.to_thread(save_session, session.messages, session.model)
        filepath = CHAT_MSGS_DIR / f"{sid}.json"
        if chat_ui is not None:
            chat_ui.write_line(f"  {filepath.name}")
        else:
            print(f"  {filepath.name}", flush=True)


# ── InteractiveLoop 类 — 封装交互模式主循环状态和编排逻辑 ──

class InteractiveLoop:
    """交互模式主循环 — 封装循环状态和编排逻辑"""

    def __init__(self, loaded_data=None):
        self._loaded_data = loaded_data
        self._force_exit = asyncio.Event()
        self._term_width_cache = TTLCache(
            fetcher=lambda: shutil.get_terminal_size().columns,
            ttl=2.0,
        )
        self._msg_done_ref: asyncio.Event | None = None
        # ChatUI 消费者 — 通过事件系统渲染聊天内容到终端
        self._chat_ui: ChatUIConsumer | None = None
        # ★ EscapeMonitor — 始终开启，捕获所有键盘输入
        self._monitor: EscapeMonitor | None = None
        # ★ 流式输入状态共享：round_end 回调与 _handle_round 之间传递
        self._loop_state: dict = {}

    def _get_term_width(self) -> int:
        return self._term_width_cache.get()

    def _check_consumer_exception(self, task: asyncio.Task) -> None:
        """检查消费者任务是否有未捕获的异常。异常时设置 _force_exit 并唤醒 msg_done 防止死锁。"""
        if not task.done():
            return
        if task.cancelled():
            _logger.warning("消息队列消费者任务被取消")
            self._force_exit.set()
            if self._msg_done_ref is not None and not self._msg_done_ref.is_set():
                self._msg_done_ref.set()
            return
        try:
            exc = task.exception()
        except asyncio.InvalidStateError as e:
            _logger.warning("检查消费者异常时出错: %s", e)
            return
        if exc is not None:
            _logger.critical("消息队列消费者异常退出: %s", exc, exc_info=exc)
            self._force_exit.set()
            if self._msg_done_ref is not None and not self._msg_done_ref.is_set():
                self._msg_done_ref.set()

    async def _handle_round(
        self,
        session: "ChatSession",
        state: SessionState,
        queue: MessageQueue,
        msg_done: asyncio.Event,
    ) -> _RoundResult:
        """执行一轮对话交互——所有用户输入（包括命令）统一放入 MessageQueue。

        所有用户输入通过 MessageQueue 投递，与 WebUI 共用同一消息处理机制。
        """
        try:
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
            prefill = _merge_prefill(state, session)

            # ★ 获取输入前清除残留中断信号
            reset_interrupt_async()

            try:
                user_input = await asyncio.to_thread(
                    self._chat_ui.wait_for_user_input, self._monitor, prefill,
                )
            except (EOFError, KeyboardInterrupt):
                return _RoundResult(should_exit=True)

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
            return _RoundResult(should_exit=False)

    async def _handle_loop_cmd(self, content: str, session: "ChatSession", state: SessionState) -> None:
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
        self._chat_ui.write_line(f"  {GREEN}+ 开始循环 {count} 次: \"{prompt[:60]}{'...' if len(prompt) > 60 else ''}\"{RESET}")
        for i in range(count):
            self._chat_ui.write_line(f"  {DIM}  ─ 第 {i+1}/{count} 轮 ─{RESET}")
            # 清空对话（复用 session.clear_messages()）
            reset_interrupt_async()
            session.clear_messages()
            # 执行一轮对话，返回结果包含 interrupted 标志
            result = await session.run_round(prompt)
            if result.get("interrupted", False):
                self._chat_ui.write_line(f"  {YELLOW}+ ESC 中断，提前结束循环（已执行 {i+1}/{count} 轮）{RESET}")
                break
        else:
            self._chat_ui.write_line(f"  {GREEN}+ 循环 {count} 次执行完毕{RESET}")
        # ── 自动保存循环后的对话 ────────────────────────────
        await _save_loop_snapshot(session, self._chat_ui)

    async def _handle_regular_msg(self, content: str, session: "ChatSession", state: SessionState) -> None:
        """处理普通用户消息"""
        reset_interrupt_async()
        # ★ 重置工具计数（新轮开始）
        if self._chat_ui is not None:
            self._chat_ui.bottom_bar.reset_tool_count()
        # 通过 ChatUI 打印用户消息
        if self._chat_ui is not None:
            self._chat_ui.on_user_message(content)
        await session.run_round(content)
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

    async def _handle_command_msg(self, content: str, session: "ChatSession", state: SessionState) -> None:
        """处理 / 命令分发"""
        reset_interrupt_async()
        cmd_name = content.split()[0].lower()
        if cmd_name == '/editmsg':
            await _handle_editmsg_cmd(session, state)
            self._chat_ui.bottom_bar.set_model_name(state.model)
            return
        if cmd_name == '/model':
            # 无参数时使用 Picker 交互选择 → 需 suspend ChatUI + EscapeMonitor
            parts = content.split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                await _handle_model_cmd(content, session, state)
                self._chat_ui.bottom_bar.set_model_name(state.model)
                return
            # 有参数时直接切换，走通用路径（无需 suspend）
        if cmd_name == '/loop':
            await self._handle_loop_cmd(content, session, state)
            return
        state_dict = {"model": state.model, "retry": False, "prefill": ""}

        # ★ 流式输入闭包：供 handle_command 中的交互式命令使用
        def _stream_input(default: str = "", show_prompt: bool = True) -> str:
            return self._chat_ui.wait_for_user_input(self._monitor, prefill=default)

        cmd_handled = await asyncio.to_thread(
            handle_command,
            content, session.messages, state_dict,
            session.agent.build_system_prompt,
            _stream_input,
            session.context_manager,
            session,
        )
        if cmd_handled:
            new_model = state_dict.get("model")
            if new_model and new_model != session.model:
                session.model = new_model
            state.model = state_dict.get("model", state.model)
            self._chat_ui.bottom_bar.set_model_name(state.model)
            state.retry = state_dict.get("retry", False)
            state.prefill = state_dict.get("prefill", "")
        else:
            self._chat_ui.write_line(f"  {YELLOW}未知命令: {content}，输入 /help 查看可用命令{RESET}")

    async def _cli_msg_consumer(self, msg, session: "ChatSession", state: SessionState, msg_done: asyncio.Event) -> None:
        """CLI 消息消费者：处理放入队列的所有消息（命令 + 普通消息）。

        参数说明：
            msg: 队列消息（Message 对象），含 content 属性
            session: 当前会话实例
            state: 会话状态，传入以便内部命令修改
            msg_done: 完成事件，每次处理前 clear，完成后 set
        """
        # 消息消费开始
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
        except Exception:
            _logger.exception("CLI 消息消费者异常")
            self._force_exit.set()
        finally:
            if not msg_done.is_set():
                msg_done.set()

    async def run(self) -> None:
        """执行交互模式主循环"""
        self._force_exit.clear()

        # ── 初始化 ChatUI 消费者（终端聊天渲染，通过事件系统驱动） ──
        # ★ 提前创建 ChatUI，使欢迎横幅等早期输出也通过统一渲染管线上屏
        self._chat_ui = ChatUIConsumer()
        self._chat_ui.start()

        _term_width = self._get_term_width()
        _sep_width = narrow_sep_width(40)
        self._chat_ui.write_line(f"\n{CYAN}  > {MODEL} Chat{RESET}")
        self._chat_ui.write_line(f"{DIM}  {'─' * _sep_width}{RESET}")
        if is_narrow():
            self._chat_ui.write_line(f"{DIM}  /help  Esc中断  /r重试  /edit重写{RESET}\n")
        else:
            self._chat_ui.write_line(f"{DIM}  /help   Esc中断   / 输前缀按 Tab 补全{RESET}\n")

        session, state = _setup_session(self._loaded_data, self._chat_ui)

        # ★ 同步初始模型名到底部栏状态行
        self._chat_ui.bottom_bar.set_model_name(state.model)

        # ★ 启用底部栏：终端底部固定显示 3 行输入界面（会话级持久）
        self._chat_ui.setup_bottom_bar()

        # ── 初始化 EscapeMonitor（始终开启，捕获所有键盘输入） ──
        self._monitor = EscapeMonitor()

        # ★ 注册特殊按键回调：Ctrl+G (vim编辑) / Ctrl+O (/editmsg) / Ctrl+N/Ctrl+R (模型切换，Cygwin 中用 Ctrl+R)

        def _edit_in_vim_sync(initial_text: str) -> str | None:
            """同步版 vim 编辑 — 在 monitor 线程中直接调用 subprocess.call。

            ``_on_special_key`` 是同步回调（在 monitor 线程中执行），
            不能使用 ``asyncio.create_subprocess_exec``（需要事件循环）。
            改用同步 ``subprocess.call`` 打开编辑器，等待用户编辑完成后返回。
            """
            tmpfile: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False, encoding='utf-8') as f:
                    f.write(initial_text)
                    tmpfile = f.name
                editor = os.environ.get('EDITOR', 'vim')
                editor_path = shutil.which(editor)
                if not editor_path:
                    _logger.warning("vim 编辑器未找到: %s", editor)
                    return None
                ret = subprocess.call([editor_path, tmpfile])
                if ret != 0:
                    _logger.warning("vim 退出码: %d", ret)
                with open(tmpfile, 'r', encoding='utf-8') as f:
                    result = f.read()
                return result
            except FileNotFoundError:
                _logger.warning("vim 未安装，请先安装 vim")
                return None
            except OSError as e:
                _logger.error("vim 编辑失败: %s", e)
                return None
            finally:
                if tmpfile is not None:
                    try:
                        os.unlink(tmpfile)
                    except OSError:
                        pass

        def _on_special_key(action: str, text: str) -> str | None:
            if action == 'vim':
                # ★ 暂停 ChatUI（render 线程 + 底部栏），恢复后 vim 可独占终端
                if self._chat_ui is not None:
                    self._chat_ui.suspend()
                try:
                    return _edit_in_vim_sync(text)
                finally:
                    if self._chat_ui is not None:
                        self._chat_ui.resume()
            elif action == 'editmsg':
                # 注入 /editmsg 命令到输入缓冲区
                return '/editmsg'
            elif action == 'switch_model':
                _models: list[str] = []
                try:
                    from .config import MODELS as _MODELS
                    _models = _MODELS
                except Exception:
                    pass
                # 用户配置中无模型列表 → 从所有 PROVIDERS 聚合
                if not _models:
                    try:
                        from .config.defaults import PROVIDERS as _PROVIDERS
                        _seen: set[str] = set()
                        for _p in _PROVIDERS.values():
                            for _m in _p.get("models", []):
                                if _m not in _seen:
                                    _seen.add(_m)
                                    _models.append(_m)
                    except Exception:
                        _models = []
                if not _models:
                    return None
                current = state.model
                if not current:
                    return None
                # 当前模型不在列表中 → 切到列表第一个
                if current not in _models:
                    next_model = _models[0]
                else:
                    try:
                        idx = _models.index(current)
                        next_model = _models[(idx + 1) % len(_models)]
                    except (ValueError, IndexError):
                        return None
                session.model = next_model
                state.model = next_model
                if self._chat_ui is not None:
                    self._chat_ui.bottom_bar.set_model_name(next_model)
                    self._chat_ui.on_notification(f"+ 已切换到 {next_model}")
                # 保留当前输入文本（不清空缓冲区）
                return text
            return None

        self._monitor.set_special_key_callback(_on_special_key)
        self._monitor.start()

        # ★ 始终注册回显回调：非流式期间用户键入也实时显示在底部栏
        # VNode 路径下通过 push_cmd 声明式驱动，默认路径保持 refresh_bottom_bar 行为
        self._monitor.set_echo_callback(self._chat_ui.get_echo_callback())

        # ★ 注册 Tab 补全回调
        if self._chat_ui is not None:
            self._chat_ui.setup_completion(self._monitor)

        _register_session_handlers(session, self._monitor, self._loop_state, self._chat_ui)

        # ── 创建 MessageQueue + 消费者（与 WebUI 共用同一消息处理机制） ──
        queue = MessageQueue()
        # ★ Bug 修复：msg_done 初始为 cleared（不 set），
        #   _handle_round 每次 wait 前主动 clear，确保同步正确。
        #   之前初始化为 set() 导致首次 wait() 永不等待消费者，
        #   在 retry_pending 场景下哨兵消息尚未处理就开始新一轮输入。
        msg_done = asyncio.Event()  # 初始 cleared

        # ★ P0 修复：注册 msg_done 引用，使 _check_consumer_exception
        #   在 consumer 异常时能唤醒等待者，防止永久死锁。
        self._msg_done_ref = msg_done

        consume_task = asyncio.create_task(queue.async_consume(lambda msg: self._cli_msg_consumer(msg, session, state, msg_done)))
        consume_task.add_done_callback(self._check_consumer_exception)

        # ★ Bug2 修复：顶层异常保护，确保异常时保存会话 + 停止 monitor
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
            # ★ SystemExit（由 exit() 抛出）：_handle_round 中 exit 前已保存会话，直接放行
            if isinstance(exc, SystemExit):
                raise
            # 捕获任何未处理异常，保存会话后安全退出
            if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)):
                _logger.info("交互模式被用户中断")
            else:
                _logger.critical("交互模式未捕获异常", exc_info=True)
            try:
                _exit_save_and_stop(session, self._chat_ui)
            except Exception:
                _logger.exception("异常路径保存会话失败")
            raise  # 重新抛出，由 main() 中的 except 处理
        finally:
            # ★ P0 修复：清理 msg_done 引用
            self._msg_done_ref = None

            # 停止 EscapeMonitor
            if self._monitor is not None:
                self._monitor.stop()
                self._monitor = None

            # 停止 ChatUI 消费者
            if self._chat_ui is not None:
                self._chat_ui.stop()
                self._chat_ui = None

            # 停止消息队列消费者
            # ★ 安全说明：consume_task.cancel() 会向 async_consume 传播 CancelledError，
            #   async_consume 的 except CancelledError 分支已做 _running.clear() +
            #   消息重新入队保护，不会丢失数据。如果此时 _cli_msg_consumer 正在
            #   await session.run_round()，run_round 内部会通过 stream_call_async
            #   的 CancelledError 处理安全退出并返回已累积结果。
            consume_task.cancel()
            try:
                await consume_task
            except asyncio.CancelledError:
                pass


# ── 退出保存辅助 ──

def _exit_save_and_stop(session, chat_ui: "ChatUIConsumer | None" = None) -> None:
    """退出前保存会话 + 停止 EscapeMonitor"""
    try:
        non_system = _non_system_messages(session)
        if non_system:
            _save_and_show_recover(session, chat_ui)
    except Exception:
        _logger.debug("退出前保存会话失败（非关键）")
    stop_active_monitor()


# ── 交互式模式 ──

async def run_interactive_mode_async(loaded_data: dict | None = None):
    """交互式对话模式（异步版）"""
    loop = InteractiveLoop(loaded_data)
    await loop.run()


# ── 保存与恢复命令 ──

def _save_and_show_recover(session, chat_ui: "ChatUIConsumer | None" = None):
    """保存对话并输出恢复命令"""
    non_system_msgs = _non_system_messages(session)
    if not non_system_msgs:
        if chat_ui is not None:
            chat_ui.write_line(f"\n{DIM}再见{RESET}")
        else:
            print(f"\n{DIM}再见{RESET}", flush=True)
        return

    sid = session.save()
    if sid:
        msg = f"\n{DIM}再见   恢复: {get_recover_cmd(sid)}{RESET}"
    else:
        msg = f"\n{DIM}再见{RESET}"
    if chat_ui is not None:
        chat_ui.write_line(msg)
    else:
        print(msg, flush=True)


# ── 单次模式 ──

def _make_event_agent():
    """创建通过 EventBus 发布事件的 Agent 实例。"""
    from .ui.events.adapters import EventBusDisplayProxy
    from .ui.adapters import UIDisplayAdapter, UIEventAdapter, UIOutputAdapter
    return Agent(
        display_port=UIDisplayAdapter(EventBusDisplayProxy(source="agent")),
        event_port=UIEventAdapter(),
        output_port=UIOutputAdapter(),
    )


async def run_single_mode_async(prompt_text):
    """单次对话模式（异步版）：输入一句话，回答后退出"""
    chat_ui = ChatUIConsumer()
    chat_ui.start()
    from .chat_ui.infrastructure.terminal_utils import narrow_sep_width
    _sep_w = narrow_sep_width(30)
    chat_ui.write_line(f"{CYAN}  > {MODEL} Chat{RESET} {DIM}· 单次模式{RESET}")
    chat_ui.write_line(f"{DIM}  {'─' * _sep_w}{RESET}")

    session = ChatSession(agent=_make_event_agent())
    session.initialize()

    monitor = EscapeMonitor()
    _register_session_handlers(session, monitor, chat_ui=chat_ui)

    try:
        result = await session.run_single(prompt_text)

        delta = result.get("delta", {})
        _save_and_show_recover(session, chat_ui)
    except Exception:
        # 异常时尝试保存会话（如果已有消息），避免对话丢失
        try:
            non_system = _non_system_messages(session)
            if non_system:
                session.save()
        except Exception:
            _logger.exception("单次模式异常路径保存会话失败")
        raise
    finally:
        chat_ui.stop()
        stop_active_monitor()
