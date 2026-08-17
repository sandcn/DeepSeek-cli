"""ClawBot 主循环 — 微信远程发命令 + 结果显示。

核心流程：
1. 登录：复用本地缓存凭证，或扫码登录微信 ClawBot（iLink Bot API）
2. 长轮询：getupdates 接收微信发来的消息
3. 配对：首次发消息的用户需回复终端打印的配对码完成授权
4. 分发：/指令 直接执行（/shell 远程命令、/clear、/new 等）；
        普通消息走 AI 对话（复用 ChatSession 会话引擎，可自动调用工具）
5. 回显：sendmessage 分段发送结果；sendtyping 显示"正在输入"状态
6. 重连：iLink 连接 24h 有效，到期前自动提醒/扫码重连

架构：
- ClawBotRunner 依赖注入 client / session_factory / print_fn，便于测试
- TUI 模式（默认）：显示非全屏聊天界面（ChatUIConsumer，与 python chat.py
  同一渲染引擎），多个微信用户 + 本地输入共享**同一个** ChatSession
  （多用户控制一个聊天信息）；本地用户（LOCAL_USER_ID）内置授权
- 非 TUI 模式（--no-tui）：保留纯文本日志输出，每个微信用户一个独立
  ChatSession（LRU 淘汰），向后兼容
- 已授权用户持久化到 ~/.chat_config/clawbot_allowed.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Callable, Optional

from ..config.defaults import CONFIG_DIR
from ..core.session import ChatSession
from .client import IlinkClient, extract_text, is_user_message
from .auth import SESSION_DURATION, login, save_cred
from .commands import (
    HELP_TEXT,
    SHELL_USAGE,
    parse_command,
    run_shell_command,
)
from .render import (
    extract_reply,
    extract_reasoning,
    extract_tool_summary,
    split_message,
    strip_ansi,
)

_logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────
MAX_SESSIONS = 20                # 并发用户会话上限（LRU 淘汰，仅非 TUI 模式）
PAIRING_TIMEOUT = 600            # 配对码有效期（秒）
MAX_PAIRING_TRIES = 5            # 配对码错误次数上限
RECONNECT_WARN_BEFORE = 2 * 3600  # 提前预警时间（秒）
RECONNECT_FORCE_BEFORE = 30 * 60  # 强制重连时间（秒）
RECONNECT_REMIND_INTERVAL = 30 * 60  # 用户回 N 后再次提醒间隔
QR_SCAN_TIMEOUT = 600            # 重连扫码等待超时（秒）

#: 本地 TUI 用户标识：在 TUI 界面直接输入的"用户"（内置授权，无需配对）
LOCAL_USER_ID = "local"


class ClawBotRunner:
    """微信 ClawBot 远程控制机器人。"""

    def __init__(self, *, model: str = "", re_login: bool = False,
                 print_fn: Callable = print,
                 client: Optional[IlinkClient] = None,
                 session_factory: Optional[Callable] = None,
                 tui: bool = False):
        self._model = model
        self._re_login = re_login
        self._print = print_fn
        self._tui = tui
        self._client = client or IlinkClient()
        if session_factory is not None:
            self._session_factory = session_factory
        elif tui:
            # TUI 模式：事件化 agent（流式输出经 EventBus 渲染到 ChatUI）
            self._session_factory = self._tui_session_factory
        else:
            self._session_factory = self._default_session_factory

        # 共享会话：TUI 模式下所有用户（微信 + 本地）共用一个聊天
        self._shared_session: Optional[ChatSession] = None
        # 每个微信用户一个会话（from_user_id → ChatSession，仅非 TUI 模式）
        self._sessions: dict[str, ChatSession] = {}
        # 已授权用户（配对成功）持久化
        self._allowed_users: set[str] = set()
        self._allowed_file = CONFIG_DIR / "clawbot_allowed.json"
        # 配对中：user_id → {"code", "created_at", "tries"}
        self._pairing_codes: dict[str, dict] = {}
        # typing ticket 缓存：user_id → ticket
        self._typing_tickets: dict[str, str] = {}
        # 最近联系人（重连通知用）
        self._last_contact: dict = {"from_id": "", "context_token": ""}
        # 重连状态
        self._login_time: float = time.time()
        self._reconnect_asking: bool = False
        self._reconnecting: bool = False
        self._next_remind_at: float = 0.0
        # TUI 组件（tui=True 时装配）
        self._chat_ui: Optional[object] = None
        self._monitor: Optional[object] = None
        self._loop_state: dict = {}

    # ── 工厂 ──────────────────────────────────────────

    @staticmethod
    def _default_session_factory(model: str = "") -> ChatSession:
        """创建 ChatSession（注入 NullPort，无 UI 依赖，保留全部工具）。"""
        session = ChatSession(model=model or None)
        session.initialize()
        return session

    @staticmethod
    def _tui_session_factory(model: str = "") -> ChatSession:
        """创建事件化 ChatSession（流式输出经 DisplayEventBus 渲染到 TUI）。

        与 app_loop 交互模式同源：agent 使用 EventBus 适配器，
        用户消息/工具调用/AI 回复自动在 ChatUIConsumer 上渲染。
        """
        from ..app_loop._single import _make_event_agent
        session = ChatSession(agent=_make_event_agent(), model=model or None)
        session.initialize()
        return session

    # ── 授权管理 ──────────────────────────────────────

    def _load_allowed(self) -> None:
        if self._allowed_file.exists():
            try:
                data = json.loads(self._allowed_file.read_text(encoding="utf-8"))
                self._allowed_users = set(data.get("users", []))
            except (json.JSONDecodeError, OSError):
                _logger.warning("读取已授权用户列表失败", exc_info=True)

    def _save_allowed(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self._allowed_file.write_text(
            json.dumps({"users": sorted(self._allowed_users)},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 会话管理 ──────────────────────────────────────

    def _get_shared_session(self) -> ChatSession:
        """获取全局共享会话（TUI 模式：多用户控制一个聊天信息）。

        所有用户（微信 + 本地）共用一个 ChatSession，LRU 不适用。
        """
        if self._shared_session is None:
            self._shared_session = self._session_factory(self._model)
        return self._shared_session

    def _get_session(self, from_id: str) -> ChatSession:
        """获取用户会话。

        TUI 模式下所有用户（微信 + 本地）返回同一个共享会话——即使共享会话
        尚未显式创建（_run_tui 启动时创建前有消息到达）也先创建共享会话，
        避免 per-user 会话与共享会话并存导致消息上下文分裂（fix：
        TUI 模式无条件走共享会话；非 TUI 模式按用户隔离 + LRU 淘汰）。
        """
        if self._tui:
            return self._get_shared_session()
        if self._shared_session is not None:
            return self._shared_session
        session = self._sessions.pop(from_id, None)
        if session is not None:
            self._sessions[from_id] = session  # 移到末尾（最近使用）
            return session
        session = self._session_factory(self._model)
        self._sessions[from_id] = session
        if len(self._sessions) > MAX_SESSIONS:
            oldest = next(iter(self._sessions))
            self._sessions.pop(oldest)
            self._print(f"[会话] 已淘汰最久未用会话: {oldest}")
        return session

    # ── 主循环 ────────────────────────────────────────

    async def run(self) -> None:
        """ClawBot 主入口：TUI 模式（默认）显示非全屏聊天界面。"""
        if self._tui:
            await self._run_tui()
        else:
            await self._run_legacy()

    async def _run_legacy(self) -> None:
        """非 TUI 模式：登录 + 长轮询主循环（纯文本日志，Ctrl+C 退出）。"""
        self._print("╔══════════════════════════════════════╗")
        self._print("║   微信 ClawBot 远程控制 · iLink API  ║")
        self._print("╚══════════════════════════════════════╝")

        token, base_url = await login(self._client, force=self._re_login,
                                      print_fn=self._print)
        self._client.set_auth(token, base_url)
        self._login_time = time.time()
        self._load_allowed()
        self._print(f"已授权用户: {len(self._allowed_users)} 个")

        self._print("开始监听微信消息（Ctrl+C 退出）...")
        buf = ""
        retry = 0
        while True:
            try:
                resp = await self._client.get_updates(buf)
                retry = 0
                buf = resp.get("get_updates_buf") or buf
                for msg in resp.get("msgs") or []:
                    try:
                        await self._handle_msg(msg)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        self._print(f"[处理消息异常] {e}")
                        _logger.exception("处理微信消息异常")
                await self._maybe_reconnect_flow()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                retry += 1
                wait = min(5 * retry, 60)
                self._print(f"[网络异常] {e}（第 {retry} 次，{wait}s 后重试）")
                await asyncio.sleep(wait)

    # ── TUI 模式 ──────────────────────────────────────

    async def _run_tui(self) -> None:
        """TUI 模式：非全屏聊天界面（同 python chat.py）+ 微信多用户共享会话。

        三个并行任务：
        - 微信轮询任务：get_updates 收微信消息 → 入队
        - 本地输入任务：wait_for_user_input 收本地输入 → 入队
        - 消息消费者任务：串行处理队列消息（配对/命令/AI 对话）
        """
        from ..tui.consumer import ChatUIConsumer
        from ..api.escape_monitor import EscapeMonitor, stop_active_monitor
        from ..api.interrupt_async import (
            request_interrupt_async,
            reset_interrupt_async,
        )
        from ..app_loop._session_setup import SessionState, _register_session_handlers
        from ..app_loop._special_keys import make_special_key_callback
        from ..app_loop._utils import _exit_save_and_stop, _merge_prefill

        # ── 装配非全屏 TUI ─────────────────────────────
        chat_ui = ChatUIConsumer()
        chat_ui.start()
        self._chat_ui = chat_ui
        self._print = lambda *a: chat_ui.write_line(" ".join(str(x) for x in a))

        # ★ 底部框渲染修复（2026-08-18）：start() 后**立即**创建共享会话并
        #   设置模型名——渲染线程首帧即渲染完整底部框（状态栏含模型名行）。
        #   修复前：start() 后先登录（扫码等待可达数分钟），渲染线程早已
        #   渲染首帧——此时 model.status.model_name 为空，状态栏**缺状态行**
        #   （仅一条分隔线）；登录完成才 set_model_name，状态行在文档中部
        #   插入、文档高度突变并超出屏幕高度，增量渲染光标定位错位 → 底部
        #   框行重叠/错位（输入行与模式行合并显示）。
        #   提前创建 session 无副作用（ChatSession.initialize 仅内存对象），
        #   登录失败/超时时会话不参与对话（沿用原清理路径）。
        session = self._get_shared_session()
        chat_ui.bottom_bar.set_model_name(session.model)
        chat_ui.setup_bottom_bar()

        # ★ 渲染修复（2026-08-18）：横幅合并为**一次** write_line —— TUI
        #   模式下逐行调用会生成多个聊天块（每块尾部自动追加空行）→ 行间
        #   出现空行、横幅被纵向拉长。用 \n 连接后单次输出，单块单尾空行。
        self._print("\n".join([
            "╔══════════════════════════════════════════════╗",
            "║  微信 ClawBot 远程控制 · iLink API           ║",
            "║  本地 TUI 输入 + 微信多用户共享同一个聊天     ║",
            "╚══════════════════════════════════════════════╝",
        ]))

        # ── 共享会话（多用户一个聊天） ─────────────────
        # session 已在启动时创建（底部框渲染修复：首帧即含状态行）；
        # 此处仅构建状态对象，重复调用 _get_shared_session 返回同一实例。
        state = SessionState(model=session.model)

        # ── 输入监听（提前到登录前） ──────────────────
        # ★ 输入修复（2026-08-18）：monitor.start() 提前到登录**之前**——
        #   登录（--re-login 扫码等待可达数分钟）期间用户输入字符会经渲染
        #   线程 read_stdin_once 读入 buffer 并显示在输入框；修复前 monitor
        #   在登录完成后才 start()，其内部 input.reset() + echo("") 把扫码
        #   等待期间已输入的字符清空 → 用户看到「输入一个字符，下一帧闪没」。
        #   提前启动后登录完成**不再**调用 monitor.start()（不 reset），扫码
        #   期间输入的内容保留到主界面继续编辑。monitor.start() 内部会 reset
        #   一次——登录前 buffer 本为空，无副作用。
        monitor = EscapeMonitor(input_instance=chat_ui._components.input)
        self._monitor = monitor
        input_ = chat_ui.input
        input_.set_special_key_callback(
            make_special_key_callback(self, session, state, chat_ui, monitor=monitor)
        )
        input_.set_interrupt_callback(lambda: request_interrupt_async())
        input_.set_echo_callback(
            lambda text, cursor_pos=-1: chat_ui.refresh_bottom_bar(text, cursor_pos)
        )
        chat_ui.setup_completion(input_)
        self._rebind_session_handlers(session)
        chat_ui.bottom_bar.set_model_name(session.model)
        chat_ui.setup_bottom_bar()
        monitor.start()

        # ── 并行任务 ──────────────────────────────────
        queue: asyncio.Queue = asyncio.Queue()
        poll_task = None
        consume_task = None

        # ── 登录（monitor 已启动：登录失败/超时也须恢复终端并停止 ChatUI，
        #    故登录纳入下方 try，由 finally 统一清理） ──
        try:
            term_width = self._get_term_width()
            token, base_url = await login(self._client, force=self._re_login,
                                          print_fn=self._print, width=term_width)
            self._client.set_auth(token, base_url)
            self._login_time = time.time()
            self._load_allowed()
            # 本地 TUI 用户内置授权（无需微信配对）
            self._allowed_users.add(LOCAL_USER_ID)
            wechat_count = len(self._allowed_users) - 1
            self._print(f"已授权微信用户: {wechat_count} 个（本地 TUI 内置授权）")
            self._print("开始监听微信消息；本地可直接输入对话（Ctrl+C 退出）...")

            poll_task = asyncio.create_task(self._poll_loop(queue))
            consume_task = asyncio.create_task(self._consume_loop(queue))

            while True:
                chat_ui.flush()
                chat_ui.bottom_bar.ensure_cursor_in_lower()

                # ★ 输入修复（2026-08-18）：优先消费 AI 生成期间 Enter 的排队输入
                #   ——round_end 回调（_make_round_callbacks._on_round_end）会
                #   drain_all 把用户 Enter 提交的文本转移到
                #   loop_state["queued_input"]；修复前主循环不消费它，用户在 AI
                #   回复期间输入的消息**静默丢失**（消息被吞、无任何处理/回显）。
                #   与 InteractiveLoop._handle_round 的 queued_input 语义一致。
                queued_input = self._loop_state.pop("queued_input", None)
                if queued_input:
                    await queue.put({
                        "source": "local",
                        "from_id": LOCAL_USER_ID,
                        "ctx": "",
                        "text": queued_input,
                    })
                    continue

                # ★ 输入修复：恢复 AI 生成期间用户正在输入（未 Enter）的文本——
                #   round_end 的 drain_all 将输入框 buffer 转移到
                #   session.captured_prefill；修复前主循环不恢复，用户打字内容
                #   被静默清空。经 _merge_prefill 合并为下一轮输入预填（与
                #   InteractiveLoop 的 prefill 语义一致；clawbot 无插件路径，
                #   state.prefill 恒为空）。
                try:
                    prefill = _merge_prefill(state, session)
                except AttributeError:
                    prefill = ""

                # ★ 清除中断残留 + stdin 残留字节（与 InteractiveLoop.
                #   _handle_round 一致）：ESC 中断 AI 生成后残留的 _interrupted
                #   标志会立即使下一次 run_round 中断；stdin 残留 \x1b 字节会
                #   污染输入解析（乱码注入）。
                reset_interrupt_async(input_instance=input_)

                try:
                    text = await asyncio.to_thread(
                        chat_ui.wait_for_user_input, monitor, prefill,
                        input_=input_,
                    )
                except (EOFError, KeyboardInterrupt):
                    break
                if text.strip().lower() == "exit":
                    break
                if not text:
                    continue
                await queue.put({
                    "source": "local",
                    "from_id": LOCAL_USER_ID,
                    "ctx": "",
                    "text": text,
                })
        finally:
            for task in (poll_task, consume_task):
                if task is not None:
                    task.cancel()
            for task in (poll_task, consume_task):
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
            try:
                _exit_save_and_stop(session, chat_ui)
            except Exception:
                _logger.debug("TUI 退出保存会话失败", exc_info=True)
            try:
                monitor.stop()
            except Exception:
                _logger.debug("monitor.stop 异常", exc_info=True)
            try:
                stop_active_monitor()
            except Exception:
                _logger.debug("stop_active_monitor 异常", exc_info=True)
            try:
                chat_ui.stop()
            except Exception:
                _logger.debug("chat_ui.stop 异常", exc_info=True)
            self._chat_ui = None
            self._monitor = None

    async def _poll_loop(self, queue: asyncio.Queue) -> None:
        """微信长轮询任务：收消息入队（TUI 显示/处理由消费者完成）。"""
        buf = ""
        retry = 0
        while True:
            try:
                resp = await self._client.get_updates(buf)
                retry = 0
                buf = resp.get("get_updates_buf") or buf
                for msg in resp.get("msgs") or []:
                    if not is_user_message(msg):
                        continue
                    from_id = msg.get("from_user_id") or ""
                    ctx = msg.get("context_token") or ""
                    text = extract_text(msg).strip()
                    if not from_id or not text:
                        continue
                    self._last_contact = {"from_id": from_id, "context_token": ctx}
                    await queue.put({
                        "source": "wechat",
                        "from_id": from_id,
                        "ctx": ctx,
                        "text": text,
                    })
                await self._maybe_reconnect_flow()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                retry += 1
                wait = min(5 * retry, 60)
                self._print(f"[网络异常] {e}（第 {retry} 次，{wait}s 后重试）")
                await asyncio.sleep(wait)

    async def _consume_loop(self, queue: asyncio.Queue) -> None:
        """消息消费者任务：串行处理本地输入与微信消息。"""
        while True:
            item = await queue.get()
            try:
                if item["source"] == "wechat":
                    msg = {
                        "message_type": 1,
                        "from_user_id": item["from_id"],
                        "context_token": item["ctx"],
                        "item_list": [{"type": 1, "text_item": {"text": item["text"]}}],
                    }
                    await self._handle_msg(msg)
                else:
                    await self._handle_local_input(item["text"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._print(f"[处理消息异常] {e}")
                _logger.exception("处理消息异常")

    def _rebind_session_handlers(self, session: ChatSession) -> None:
        """注册会话生命周期回调（round_start/round_end → 底部栏状态）。

        /new 重建共享会话后调用（幂等：重复注册覆盖旧回调）。
        """
        if self._chat_ui is None or self._monitor is None:
            return
        from ..app_loop._session_setup import _register_session_handlers
        _register_session_handlers(session, self._monitor, self._loop_state, self._chat_ui)

    def _get_term_width(self) -> int:
        """获取终端字符宽度（二维码自适应渲染用，失败回退 80）。"""
        try:
            from ..tui._screen import TerminalWidthCache
            return TerminalWidthCache.get_default().get_width()
        except Exception:
            return 80

    # ── 消息处理 ──────────────────────────────────────

    async def _handle_msg(self, msg: dict) -> None:
        if not is_user_message(msg):
            return
        from_id = msg.get("from_user_id") or ""
        if not from_id:
            return
        text = extract_text(msg).strip()
        ctx = msg.get("context_token") or ""
        self._print(f"[收到] {from_id}: {text[:80]}")
        self._last_contact = {"from_id": from_id, "context_token": ctx}
        if not text:
            return

        # 配对流程：未授权用户先配对
        if from_id not in self._allowed_users:
            await self._pairing_flow(from_id, ctx, text)
            return

        await self._dispatch_cmd(from_id, ctx, text)

    async def _handle_local_input(self, text: str) -> None:
        """本地 TUI 输入：视为已授权用户，命令/AI 对话与微信用户共享会话。

        ctx 为空字符串 → 结果仅显示在 TUI，不发送微信。
        """
        await self._dispatch_cmd(LOCAL_USER_ID, "", text)

    async def _dispatch_cmd(self, from_id: str, ctx: str, text: str) -> None:
        """命令分发（微信/本地共用入口）。"""
        # 重连询问 Y/N（优先于其他命令）
        if self._reconnect_asking and text.strip().upper() in ("Y", "N"):
            await self._handle_reconnect_reply(from_id, ctx, text.strip().upper())
            return

        name, arg = parse_command(text)
        if name == "help":
            await self._send(from_id, ctx, HELP_TEXT)
        elif name == "shell":
            await self._cmd_shell(from_id, ctx, arg)
        elif name == "clear":
            await self._cmd_clear(from_id, ctx)
        elif name == "new":
            await self._cmd_new(from_id, ctx)
        elif name == "time":
            await self._cmd_time(from_id, ctx)
        elif name == "status":
            await self._cmd_status(from_id, ctx)
        elif name == "model":
            await self._cmd_model(from_id, ctx, arg)
        elif name:
            await self._send(from_id, ctx, f"未知指令 /{name}\n{HELP_TEXT}")
        else:
            await self._ai_chat(from_id, ctx, text)

    # ── 配对流程 ──────────────────────────────────────

    async def _pairing_flow(self, from_id: str, ctx: str, text: str) -> None:
        """首次用户配对：生成配对码 → 用户回复配对码 → 授权。"""
        pairing = self._pairing_codes.get(from_id)
        now = time.time()
        if pairing and now - pairing["created_at"] > PAIRING_TIMEOUT:
            self._pairing_codes.pop(from_id, None)
            pairing = None
        if not pairing:
            code = self._gen_pairing_code(from_id)
            self._print(f"[配对] 新用户 {from_id}，配对码 {code}")
            await self._send(from_id, ctx,
                             f"🔐 首次使用需完成配对。\n请回复配对码：{code}\n"
                             f"（配对码已打印在运行 DeepSeek-cli 的终端上）")
            return
        code = pairing["code"]
        if text == code:
            self._allowed_users.add(from_id)
            self._save_allowed()
            self._pairing_codes.pop(from_id, None)
            self._print(f"[配对] 用户 {from_id} 已授权 ✅")
            await self._send(from_id, ctx, "✅ 配对成功！现在可以使用远程控制。\n\n" + HELP_TEXT)
            return
        pairing["tries"] += 1
        if pairing["tries"] >= MAX_PAIRING_TRIES:
            self._pairing_codes.pop(from_id, None)
            await self._send(from_id, ctx,
                             "❌ 配对码错误次数过多。请重新发送任意消息获得新配对码。")
        else:
            await self._send(from_id, ctx, f"❌ 配对码错误，请重新回复：{code}")

    def _gen_pairing_code(self, from_id: str) -> str:
        code = f"{random.randint(0, 9999):04d}"
        self._pairing_codes[from_id] = {"code": code, "created_at": time.time(), "tries": 0}
        return code

    # ── 指令实现 ──────────────────────────────────────

    async def _cmd_shell(self, from_id: str, ctx: str, arg: str) -> None:
        if not arg:
            await self._send(from_id, ctx, SHELL_USAGE)
            return
        self._print(f"[shell] {from_id}: {arg}")
        await self._typing(from_id, 1)
        out = await run_shell_command(arg)
        await self._typing(from_id, 2)
        for chunk in split_message(f"$ {arg}\n\n{out}"):
            await self._send(from_id, ctx, chunk)

    async def _cmd_clear(self, from_id: str, ctx: str) -> None:
        session = self._get_session(from_id)
        removed = session.clear_messages()
        await self._send(from_id, ctx, f"🧹 已清空会话上下文（移除 {removed} 条消息）")

    async def _cmd_new(self, from_id: str, ctx: str) -> None:
        if self._shared_session is not None:
            # 共享会话模式：重建新会话（多用户共同进入新聊天）
            self._shared_session = self._session_factory(self._model)
            if self._chat_ui is not None:
                self._chat_ui.clear_messages()
                self._rebind_session_handlers(self._shared_session)
        else:
            session = self._get_session(from_id)
            session.clear_messages()
            self._sessions.pop(from_id, None)
            fresh = self._get_session(from_id)
            fresh.clear_messages()
        await self._send(from_id, ctx, "🆕 已开始新会话")

    async def _cmd_time(self, from_id: str, ctx: str) -> None:
        remaining = self._login_time + SESSION_DURATION - time.time()
        h, m = int(remaining // 3600), int((remaining % 3600) // 60)
        await self._send(from_id, ctx, f"⏳ 连接剩余时间：{h} 小时 {m} 分钟")

    async def _cmd_status(self, from_id: str, ctx: str) -> None:
        session = self._get_session(from_id)
        remaining = self._login_time + SESSION_DURATION - time.time()
        h, m = int(remaining // 3600), int((remaining % 3600) // 60)
        non_system = [x for x in session.messages if x.get("role") != "system"]
        text = (f"🤖 模型: {session.model}\n"
                f"💬 当前会话消息: {len(non_system)} 条\n"
                f"👥 已授权用户: {len(self._allowed_users)} 个\n"
                f"⏳ 连接剩余: {h} 小时 {m} 分钟")
        await self._send(from_id, ctx, text)

    async def _cmd_model(self, from_id: str, ctx: str, arg: str) -> None:
        session = self._get_session(from_id)
        if not arg:
            await self._send(from_id, ctx,
                             f"当前模型: {session.model}\n用法: /model <模型名>")
            return
        session.model = arg
        await self._send(from_id, ctx, f"✅ 已切换模型: {arg}")

    # ── AI 对话 ───────────────────────────────────────

    async def _ai_chat(self, from_id: str, ctx: str, text: str) -> None:
        session = self._get_session(from_id)
        # TUI 模式：先显示用户消息（微信用户带来源标识；本地直接显示）
        if self._chat_ui is not None:
            label = text if from_id == LOCAL_USER_ID else f"[微信 {from_id}] {text}"
            self._chat_ui.on_user_message(label)
        await self._typing(from_id, 1)
        before = len(session.messages)
        try:
            result = await session.run_round(text)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._print(f"[AI 错误] {from_id}: {e}")
            _logger.exception("AI 对话失败")
            await self._typing(from_id, 2)
            await self._send(from_id, ctx, f"❌ 处理失败: {e}")
            return
        await self._typing(from_id, 2)

        new_msgs = session.messages[before:]
        summary = extract_tool_summary(new_msgs)
        reply = extract_reply(new_msgs) or extract_reply(session.messages)
        reasoning = extract_reasoning(new_msgs)

        parts = []
        if summary:
            parts.append(summary)
        if reasoning:
            parts.append(f"🤔 {reasoning}")
        parts.append(reply if reply else "(本轮无文本回复)")
        body = "\n\n".join(parts)
        for chunk in split_message(body):
            if self._chat_ui is not None:
                # TUI 已由事件化 agent 流式渲染 AI 回复，此处仅发送微信
                await self._send_wechat(from_id, ctx, chunk)
            else:
                await self._send(from_id, ctx, chunk)

    # ── 输入状态 / 发送 ───────────────────────────────

    async def _typing(self, from_id: str, status: int) -> None:
        """发送/取消"正在输入"状态（失败静默）。"""
        ticket = self._typing_tickets.get(from_id)
        if not ticket:
            try:
                cfg = await self._client.get_config(
                    from_id, self._last_contact.get("context_token", ""))
                ticket = (cfg.get("typing_ticket") or "").strip()
                if ticket:
                    self._typing_tickets[from_id] = ticket
            except Exception:
                return
        if not ticket:
            return
        try:
            await self._client.send_typing(from_id, ticket, status)
        except Exception:
            pass

    async def _send(self, from_id: str, ctx: str, text: str) -> None:
        """发送文本：TUI 模式先在界面显示，再发送到微信。

        本地输入（ctx 为空）仅显示到 TUI，不发送微信。
        """
        if self._chat_ui is not None:
            self._print(text)
        await self._send_wechat(from_id, ctx, text)

    async def _send_wechat(self, from_id: str, ctx: str, text: str) -> None:
        """仅发送文本到微信（TUI 已渲染内容时使用，避免重复显示）。

        失败打印日志，不中断主循环。
        """
        if not from_id or not ctx:
            return
        try:
            await self._client.send_message(from_id, ctx, text)
        except Exception as e:
            self._print(f"[发送失败] {from_id}: {e}")

    # ── 重连流程 ──────────────────────────────────────

    async def _maybe_reconnect_flow(self) -> None:
        """每轮长轮询后检查连接剩余时间，决定预警/强制重连。"""
        remaining = self._login_time + SESSION_DURATION - time.time()
        if remaining > RECONNECT_WARN_BEFORE:
            return
        if remaining <= RECONNECT_FORCE_BEFORE:
            await self._start_reconnect()
            return
        if not self._reconnect_asking and time.time() >= self._next_remind_at:
            self._reconnect_asking = True
            self._print("[重连] 连接即将到期，向最近联系人发送提醒")
            h = remaining / 3600
            await self._send(
                self._last_contact["from_id"],
                self._last_contact.get("context_token", ""),
                f"⏰ 连接还剩约 {h:.1f} 小时到期。\n回复 Y 立即重新扫码连接 / N 稍后提醒",
            )

    async def _handle_reconnect_reply(self, from_id: str, ctx: str, answer: str) -> None:
        if answer == "Y":
            await self._send(from_id, ctx, "好的，正在获取新二维码...")
            await self._start_reconnect()
        else:
            self._reconnect_asking = False
            self._next_remind_at = time.time() + RECONNECT_REMIND_INTERVAL
            await self._send(from_id, ctx, "好的，稍后再提醒您")

    async def _start_reconnect(self) -> None:
        """扫码重连：获取新二维码，终端渲染+发给最近联系人，扫码后切换 token。"""
        if self._reconnecting:
            return
        self._reconnecting = True
        contact = self._last_contact
        try:
            self._print("[重连] 获取新二维码...")
            data = await self._client.get_bot_qrcode()
            qrcode = (data.get("qrcode") or "").strip()
            if not qrcode:
                raise RuntimeError("获取二维码失败")

            # 终端渲染新二维码（与登录同款：终端二维码 + PNG 文件 + 链接）
            from .auth import display_qrcode
            display_qrcode(data, print_fn=self._print, width=self._get_term_width())

            # 微信侧：能发链接就发链接，否则发二维码标识并提示看终端
            img = data.get("qrcode_img_content") or ""
            if img.startswith("http"):
                qr_hint = img
            else:
                qr_hint = f"二维码标识: {qrcode}\n（二维码已显示在运行终端上）"
            await self._send(contact["from_id"], contact.get("context_token", ""),
                             f"🔁 请扫码完成重连（当前连接即将到期）：\n{qr_hint}")

            deadline = time.time() + QR_SCAN_TIMEOUT
            while time.time() < deadline:
                status = await self._client.get_qrcode_status(qrcode)
                st = status.get("status", "")
                if st == "confirmed":
                    token = (status.get("bot_token") or "").strip()
                    base_url = (status.get("baseurl") or "").strip()
                    if not token:
                        raise RuntimeError("扫码成功但未返回 bot_token")
                    self._client.set_auth(token, base_url)
                    save_cred(token, base_url)
                    self._login_time = time.time()
                    self._reconnect_asking = False
                    self._next_remind_at = 0.0
                    self._typing_tickets.clear()
                    self._print("[重连] ✅ 新连接已建立")
                    await self._send(contact["from_id"], contact.get("context_token", ""),
                                     "✅ 重连成功，继续使用")
                    return
                if st in ("expired", "canceled", "failed"):
                    self._print(f"[重连] 二维码失效（{st}）")
                    break
                await asyncio.sleep(1)
            await self._send(contact["from_id"], contact.get("context_token", ""),
                             "⏳ 扫码超时/失败，稍后将再次提醒")
        except Exception as e:
            self._print(f"[重连失败] {e}")
            _logger.exception("重连失败")
        finally:
            self._reconnecting = False

    # ── 清理 ──────────────────────────────────────────

    async def aclose(self) -> None:
        """关闭底层 HTTP 客户端。"""
        try:
            await self._client.aclose()
        except Exception:
            pass


# ── 入口函数 ──────────────────────────────────────────

async def run_clawbot(*, model: str = "", re_login: bool = False,
                      print_fn: Callable = print, tui: bool = True) -> None:
    """ClawBot 远程控制入口（供 app_init.main 调用）。

    Args:
        model: 覆盖模型名
        re_login: 强制重新扫码
        print_fn: 输出函数（非 TUI 模式；TUI 模式自动重定向到聊天界面）
        tui: 是否启用非全屏 TUI（默认 True：本地输入 + 微信多用户共享会话）
    """
    runner = ClawBotRunner(model=model, re_login=re_login,
                           print_fn=print_fn, tui=tui)
    try:
        await runner.run()
    finally:
        await runner.aclose()
