"""数据命令 — 文件读写/会话管理相关命令处理函数"""

from __future__ import annotations

import logging
import threading
import time

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN, filter_system, filter_non_system
from ..adapters.output import get_default_output_port
from ...config import MODEL
from ..sandbox_manager import get_sandbox_manager
from ..internal.commands._command_core import CommandContext

_out = get_default_output_port()
_logger = logging.getLogger(__name__)

_SESSION_ID_TRUNCATE = 12     # 会话ID显示截断长度
#: 会话文件异步读取超时（秒）——超时按加载失败处理（后台线程为 daemon，无害）。
_SESSION_LOAD_TIMEOUT = 60.0


def _request_ui_redraw() -> None:
    """请求活跃 ChatUI 重绘（后台线程完成数据构建后调用，界面动态更新）。"""
    try:
        from ..adapters.ui_runtime import get_active_chat_ui

        ui = get_active_chat_ui()
        if ui is not None:
            ui.request_bottom_redraw()
    except Exception:
        _logger.debug("视图重绘请求失败", exc_info=True)


class _SessionLoadJob:
    """后台读取会话文件（``/load`` 命令与 ``/sessions`` 视图加载共用）。

    大会话 JSON 可达数十 MB，同步读取会阻塞调用线程；本任务在后台线程读取，
    调用方可先输出「正在加载」提示并与其它准备工作（自动保存当前会话 /
    清空文件沙盒）并行，完成后回填（界面动态更新）。
    """

    def __init__(self, persistence, session_id: str):
        self._persistence = persistence
        self._session_id = str(session_id)
        self._event = threading.Event()
        self._data = None
        self._error: Exception | None = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def error(self) -> Exception | None:
        return self._error

    def start(self) -> None:
        threading.Thread(
            target=self._run, name="session-load", daemon=True,
        ).start()

    def _run(self) -> None:
        try:
            self._data = self._persistence.load_session(self._session_id)
        except Exception as exc:
            self._error = exc
            _logger.debug("会话读取失败 %s", self._session_id, exc_info=True)
        finally:
            self._event.set()

    def wait(self, timeout: float | None = None) -> tuple[bool, dict | None]:
        """等待读取完成。

        Returns:
            ``(finished, data)``；finished=False 表示超时（后台线程继续运行）。
        """
        finished = self._event.wait(timeout)
        return finished, self._data


def _resolve_persistence(ctx):
    """解析持久化端口：命令上下文注入 > 内核服务 > 默认 JSON 文件实现。"""
    port = getattr(ctx, "persistence_port", None)
    if port is not None:
        return port
    from ..adapters.kernel_runtime import active_persistence_port

    port = active_persistence_port()
    if port is not None:
        return port
    from ..adapters.persistence import JsonFilePersistence

    return JsonFilePersistence()


def _cmd_load(ctx):
    """加载保存的对话（自动保存当前会话并清空沙盒）"""
    arg = ctx.arg.strip()
    if not arg:
        _out.write(f"{YELLOW}  ! 用法: /load <会话ID>{RESET}", level="raw", source="cmd")
        _out.write(f"  {DIM}  输入 /sessions 查看所有保存的对话{RESET}", level="raw", source="cmd")
        return True
    _load_session_by_id(ctx, arg)
    return True


def _load_session_by_id(ctx, session_id: str) -> bool:
    """按 ID 加载会话到当前上下文（自动保存当前会话 + 清空沙盒）。

    供 ``/load`` 命令与「会话浏览器视图」（SessionsView 加载回传）共用；
    返回是否成功加载（会话不存在/无消息返回 False）。

    ★ 2026-10-10（初始化性能异步加载）：会话文件读取在**后台线程**执行
    （大会话 JSON 可达数十 MB），与「自动保存当前会话 / 清空文件沙盒」
    并行；等待期间已输出「正在加载」提示，读取完成后回填消息并回放
    （界面动态更新）。
    """
    arg = str(session_id or "").strip()
    if not arg:
        return False

    _p = _resolve_persistence(ctx)
    # ── 第0步：后台异步读取目标会话（与下方准备步骤并行） ──────
    job = _SessionLoadJob(_p, arg)
    job.start()
    _out.write(
        f"{DIM}  \u2026 正在加载会话 {arg[:_SESSION_ID_TRUNCATE]}{RESET}",
        level="raw", source="cmd",
    )

    # ── 第1步：自动保存当前会话（如有非 system 消息） ──────────
    non_system_current = filter_non_system(ctx.messages)
    if non_system_current:
        current_model = ctx.state.get("model", MODEL)
        # 连同 SubAgent 记录一起保存（含完整聊天信息）
        current_subagents = []
        if ctx.session is not None:
            agent = getattr(ctx.session, "agent", None)
            if agent is not None:
                current_subagents = list(getattr(agent, "_subagent_records", None) or [])
        try:
            saved_id = _p.save_session(non_system_current, model=current_model,
                                       subagents=current_subagents)
            _out.write(f"{DIM}  + 已自动保存当前会话: {saved_id[:_SESSION_ID_TRUNCATE]}{RESET}", level="raw", source="cmd")
        except Exception as e:
            _out.write(f"{YELLOW}  ! 自动保存当前会话失败: {e}{RESET}", level="raw", source="cmd")

    # ── 第2步：清空文件沙盒 ──────────────────────────────────
    sandbox = get_sandbox_manager()
    if sandbox:
        sandbox.clear()
        _out.write(f"{DIM}  + 文件沙盒已清空{RESET}", level="raw", source="cmd")

    # ── 第3步：等待后台读取完成并回填 ────────────────────────
    finished, data = job.wait(_SESSION_LOAD_TIMEOUT)
    if not finished:
        _out.write(f"{YELLOW}  ! 会话加载超时: {arg}{RESET}", level="raw", source="cmd")
        return False
    if job.error is not None:
        _out.write(f"{YELLOW}  ! 会话加载失败: {job.error}{RESET}", level="raw", source="cmd")
        return False
    if data is None:
        _out.write(f"{YELLOW}  ! 未找到会话 '{arg}'{RESET}", level="raw", source="cmd")
        return False

    loaded_msgs = data.get("messages", [])
    if not loaded_msgs:
        _out.write(f"{YELLOW}  ! 该会话没有消息{RESET}", level="raw", source="cmd")
        return False

    # 替换当前消息（保留 system 消息）
    system_msgs = filter_system(ctx.messages)
    ctx.messages[:] = system_msgs
    for msg in loaded_msgs:
        ctx.messages.append(msg)

    # 恢复 SubAgent 记录（含完整聊天信息，供 /export 导出）
    loaded_subagents = data.get("subagents") or []
    if ctx.session is not None:
        agent = getattr(ctx.session, "agent", None)
        if agent is not None:
            setattr(agent, "_subagent_records", list(loaded_subagents))

    # ★ 2026-08-17（用户需求：load 命令支持已完成 subagent 轨迹）：恢复
    #   轨迹存档——主轨迹显示历史 subagent 记录、Enter 可进入查看完整轨迹
    #   （数据源与构建逻辑复用运行时同一套：``_trace_archive`` →
    #   ``_subagent_records``/``build_subagent_trace_records``，无第二份
    #   实现；非 TUI 环境/异常记 debug 日志零成本跳过）。
    try:
        from ..adapters.ui_runtime import get_subagent_panel_controller
        get_subagent_panel_controller().get_default().restore_trace_archive(loaded_subagents)
    except Exception:
        _logger.debug("恢复 subagent 轨迹存档异常", exc_info=True)

    model = data.get("model", ctx.state.get("model", MODEL))
    ctx.state["model"] = model

    title = data.get("title", "")
    title_info = f"「{title}」 " if title else ""
    _out.write(f"{GREEN}  + 已加载会话 {title_info}{arg} ({len(loaded_msgs)} 条消息, 模型: {model}){RESET}", level="raw", source="cmd")

    # 显示恢复的消息摘要（逐批回放：界面随回放进度逐渐增加显示）
    non_system = filter_non_system(ctx.messages)
    if ctx.ui_adapter is not None:
        _replay_messages_incrementally(ctx.ui_adapter, non_system)

    # 检查最后一条消息角色
    if ctx.messages and ctx.messages[-1].get("role") in ("assistant", "tool"):
        _out.write(f"  {DIM}  继续输入开始新的对话{RESET}", level="raw", source="cmd")
    elif ctx.messages and ctx.messages[-1].get("role") == "user":
        _out.write(f"  {DIM}  最后一条是用户消息，将自动继续生成回复…{RESET}", level="raw", source="cmd")
        ctx.state["retry"] = True
    return True


#: 会话预览最大加载会话数（避免超大历史目录一次性读全量文件）。
_SESSION_PREVIEW_LIMIT = 200
#: 单个会话预览最大消息行数。
_SESSION_PREVIEW_LINES = 40


def _msg_text(content) -> str:
    """消息 content → 纯文本（兼容 str 与多模态 content blocks）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text" or "text" in block:
                    parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return " ".join(parts)
    return "" if content is None else str(content)


#: 角色标签（会话预览显示）。
_ROLE_LABELS = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}


def _session_preview(data, max_lines: int = _SESSION_PREVIEW_LINES) -> list:
    """会话数据 → 预览行 ``[(角色, 单行文本), ...]``。"""
    if not isinstance(data, dict):
        return []
    lines: list = []
    for msg in (data.get("messages") or []):
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "?"))
        text = " ".join(_msg_text(msg.get("content")).split())
        if role == "tool" and not text:
            text = str(msg.get("name", "")) or "(工具返回)"
        label = _ROLE_LABELS.get(role, role)
        lines.append((label, text[:120] if text else "(空)"))
        if len(lines) >= int(max_lines):
            break
    return lines


#: /load 回放时每批消息之间的间隔（秒）——渲染线程按帧消费命令，间隔使
#: 消息**逐批上屏**（加载成功一条即界面增加一条信息）；批内保持
#: assistant 与其 tool 响应完整（匿名工具配对依赖同批内配对）。
_LOAD_REPLAY_INTERVAL = 0.02


def _group_messages_for_replay(messages: list) -> list:
    """历史消息分组（回放分批用）。

    分组边界保证 **assistant 与其紧随的 tool 响应同批**——``_do_display_messages``
    的匿名（无 tool_call_id）工具配对使用批次内局部队列，跨批拆分会导致
    tool 消息找不到对应工具卡片。
    """
    groups: list = []
    current: list = []
    index = 0
    total = len(messages)
    while index < total:
        msg = messages[index]
        index += 1
        if not isinstance(msg, dict):
            continue
        current.append(msg)
        if msg.get("role") == "assistant":
            while index < total:
                nxt = messages[index]
                if isinstance(nxt, dict) and nxt.get("role") == "tool":
                    current.append(nxt)
                    index += 1
                else:
                    break
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _replay_messages_incrementally(ui_adapter, messages: list) -> None:
    """逐批回放历史消息（界面随回放进度逐渐增加显示）。

    每批一次 ``display_messages`` 调用 + 短间隔（渲染线程按帧消费，形成
    「逐条上屏」效果）；仅一批（无工具交互的短会话）时不分帧。
    """
    groups = _group_messages_for_replay(messages)
    if not groups:
        return
    interval = _LOAD_REPLAY_INTERVAL if len(groups) > 1 else 0.0
    for i, group in enumerate(groups):
        ui_adapter.display_messages(group, speed=0)
        if interval and i + 1 < len(groups):
            time.sleep(interval)


def _iter_session_summaries(persistence, limit: int):
    """逐条产出会话摘要（优先端口流式 ``iter_sessions``；异常安全）。

    端口未提供流式能力（自定义实现/测试桩）时回退 ``list_sessions`` 全量。
    超过 ``limit`` 的条目不再产出但**继续耗尽生成器**（触发底层缓存写入，
    与 ``list_sessions`` 的缓存语义一致）。
    """
    limit = int(limit)
    try:
        iterator = getattr(persistence, "iter_sessions", None)
        if callable(iterator):
            for index, item in enumerate(iterator()):
                if index >= limit:
                    continue
                yield item
            return
        summaries = list(getattr(persistence, "list_sessions", lambda: [])() or [])
    except Exception:
        _logger.debug("会话摘要读取失败", exc_info=True)
        return
    for item in summaries[:limit]:
        yield item


def _build_session_entries(
    persistence, *, limit: int = _SESSION_PREVIEW_LIMIT, on_entry=None,
) -> list:
    """构建会话浏览器条目（摘要 + 预览行）。

    ★ 2026-10-10（逐条增量加载）：``on_entry(entry, entries)`` 在**每构建出
    一条**（含预览行）后立即调用——视图据此把该条追加显示（加载成功一条即
    界面增加一条信息）；回调异常隔离，不影响后续构建。
    """
    out: list = []
    for s in _iter_session_summaries(persistence, limit):
        if not isinstance(s, dict):
            continue
        entry = dict(s)
        try:
            data = persistence.load_session(s.get("id"))
        except Exception:
            data = None
        entry["preview_lines"] = _session_preview(data)
        out.append(entry)
        if on_entry is not None:
            try:
                on_entry(entry, out)
            except Exception:
                _logger.debug("条目增量回调异常", exc_info=True)
    return out


#: 视图条目增量重绘节流间隔（秒）——逐条构建时合并渲染帧（界面持续增长，
#: 同时避免每一条都同步渲染一帧）。
_ENTRIES_REDRAW_INTERVAL = 0.15


class _RedrawThrottle:
    """重绘请求节流（后台线程逐条更新时合并渲染帧）。"""

    def __init__(self, notify, interval: float = _ENTRIES_REDRAW_INTERVAL):
        self._notify = notify
        self._interval = float(interval)
        self._last = 0.0
        self._pending = False

    def request(self) -> None:
        now = time.monotonic()
        if now - self._last >= self._interval:
            self._last = now
            self._pending = False
            self._notify()
        else:
            self._pending = True

    def flush(self) -> None:
        if not self._pending:
            return
        self._pending = False
        self._last = time.monotonic()
        self._notify()


def _insert_entry_sorted(entries: list, entry: dict) -> None:
    """按 ``saved_at`` 降序把条目插入列表（与 ``list_sessions`` 排序语义一致）。"""
    key = str(entry.get("saved_at", "") or "")
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if str(entries[mid].get("saved_at", "") or "") >= key:
            lo = mid + 1
        else:
            hi = mid
    entries.insert(lo, entry)


def _build_entries_async(persistence, state, notify=None) -> None:
    """后台构建会话条目并写回视图状态（完成后请求重绘，动态更新界面）。

    ★ 2026-10-10（逐条增量加载）：每构建出一条（含预览）即写入
    ``state.entries`` 并按 ``_ENTRIES_REDRAW_INTERVAL`` 节流重绘——界面随
    加载进度**逐条增加**（加载成功一条即增加一条信息），而非等全部完成才
    一次性出现；全部完成后复位 ``state.loading`` 并最终重绘一次。
    视图已关闭（``state.done``）时不再写回。
    """
    notify_fn = notify or _request_ui_redraw
    throttle = _RedrawThrottle(notify_fn)

    def _on_entry(entry, _entries):
        if getattr(state, "done", False):
            return
        new_list = list(state.entries)
        _insert_entry_sorted(new_list, entry)
        state.entries = new_list
        throttle.request()

    def _work() -> None:
        try:
            entries = _build_session_entries(persistence, on_entry=_on_entry)
            entries.sort(
                key=lambda e: str(e.get("saved_at", "") or ""), reverse=True,
            )
            error = ""
        except Exception as exc:
            _logger.debug("会话条目后台构建失败", exc_info=True)
            entries = []
            error = str(exc)
        if getattr(state, "done", False):
            return
        state.entries = entries
        state.loading = False
        state.loading_error = error
        throttle.flush()
        notify_fn()

    threading.Thread(
        target=_work, name="sessions-view-load", daemon=True,
    ).start()


def _open_sessions_ui(ctx) -> bool:
    """打开全屏会话浏览器视图（有 ChatUI 时）。返回是否已打开处理。

    ★ 2026-10-10（初始化性能异步加载）：会话条目（摘要 + 预览）在**后台
    线程**构建——视图先以「正在加载会话列表…」占位打开，数据就绪后写回
    entries 并请求重绘（动态更新界面）；重命名/删除后的刷新同样异步。
    """
    from ..adapters.ui_runtime import get_sessions_view_state_cls
    from ._view_opener import open_fullscreen_view

    _p = _resolve_persistence(ctx)
    applied_seq = {"v": 0}

    def setup(model, state):
        state.entries = []
        state.loading = True
        state.loading_error = ""

    def bg_setup(state, refresh):
        _build_entries_async(_p, state, notify=refresh)

    def tick(state) -> bool:
        if state.applied_seq > applied_seq["v"]:
            applied_seq["v"] = state.applied_seq
            report = state.applied or {}
            action = report.get("action")
            sid = str(report.get("id", "") or "")
            if action == "load":
                if _load_session_by_id(ctx, sid):
                    state.status_message = "已加载会话"
                    return True
                state.status_message = f"加载失败：{sid}"
            elif action == "rename":
                try:
                    ok = bool(_p.rename_session(sid, str(report.get("title", "") or "")))
                except Exception:
                    ok = False
                state.status_message = "已重命名" if ok else "重命名失败"
                state.loading = True
                _build_entries_async(_p, state)
            elif action == "delete":
                try:
                    ok = bool(_p.delete_session(sid))
                except Exception:
                    ok = False
                state.status_message = "已删除" if ok else "删除失败"
                state.loading = True
                _build_entries_async(_p, state)
                state.selected = 0
                state.delete_confirm = ""
        return False

    return open_fullscreen_view(
        ctx, view_id="sessions", state_attr="sessions_view",
        state_cls=get_sessions_view_state_cls(), setup=setup, bg_setup=bg_setup,
        on_tick=tick,
        close_hint="会话浏览器已关闭", timeout_hint="会话浏览器超时关闭",
    )


def _cmd_sessions(ctx):
    """列出所有保存的对话（有 ChatUI 时打开会话浏览器视图）。"""
    if _open_sessions_ui(ctx):
        return True
    return _sessions_text(ctx)


def _sessions_text(ctx):
    """文本列出所有保存的对话（无 ChatUI / 单次模式回退）。"""
    _p = _resolve_persistence(ctx)
    sessions = _p.list_sessions()
    if not sessions:
        _out.write(f"{YELLOW}  ! 没有保存的对话{RESET}", level="raw", source="cmd")
        return True
    _out.write(f"\n{DIM}  \u2500 已保存的对话{RESET}", level="raw", source="cmd")
    for s in sessions:
        msg_count = s.get("message_count", 0)
        title = s.get("title", "")
        if title:
            # 标题和ID并排显示，限制标题宽度
            _out.write(f"  {CYAN}  {s['id'][:8]}{RESET}  {DIM}{title}{RESET}", source="cmd")
            _out.write(f"  {DIM}     {s['model']}  {msg_count}条  {s['saved_at']}{RESET}", source="cmd")
        else:
            _out.write(f"  {CYAN}  {s['id']}{RESET}  {DIM}{s['model']}  {msg_count}条消息  {s['saved_at']}{RESET}", source="cmd")
    _out.write("", level="raw", source="cmd")
    return True


# ── CommandPlugin 子类 ──────────────────────────────
# 命令在此声明（declare_command_plugin）；注册由清单条目 / ctx.commands 服务按需触发。
# 注册时内部调用 register_command() 保持向后兼容。

from .base import CommandPlugin, CommandMeta, declare_command_plugin


class LoadCommand(CommandPlugin):
    """加载历史对话"""
    def __init__(self):
        self.meta = CommandMeta(name="load", description="加载保存的对话", group="data")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_load(ctx)


class SessionsCommand(CommandPlugin):
    """列出所有对话"""
    def __init__(self):
        self.meta = CommandMeta(name="sessions", description="列出所有保存的对话", group="data")

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_sessions(ctx)


class HelpCommand(CommandPlugin):
    """显示帮助"""
    def __init__(self):
        self.meta = CommandMeta(name="help", description="显示帮助", group="ui")

    def execute(self, ctx: CommandContext) -> bool:
        from ..internal.commands._command_core import _cmd_help
        return _cmd_help(ctx)


# ── 声明插件（注册由清单条目 / ctx.commands 服务按需触发） ──
declare_command_plugin(LoadCommand())
declare_command_plugin(SessionsCommand())
declare_command_plugin(HelpCommand())
