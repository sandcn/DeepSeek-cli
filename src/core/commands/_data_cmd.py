"""数据命令 — 文件读写/会话管理相关命令处理函数"""

import logging

from ..constants import GREEN, YELLOW, DIM, RESET, CYAN, filter_system, filter_non_system
from ..adapters.output import get_default_output_port
from ...config import MODEL
from ..sandbox_manager import get_sandbox_manager
from ..internal.commands._command_core import CommandContext

_out = get_default_output_port()
_logger = logging.getLogger(__name__)

_SESSION_ID_TRUNCATE = 12     # 会话ID显示截断长度


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
    """
    arg = str(session_id or "").strip()
    if not arg:
        return False

    # ── 第1步：自动保存当前会话（如有非 system 消息） ──────────
    non_system_current = filter_non_system(ctx.messages)
    _p = _resolve_persistence(ctx)
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

    # ── 第3步：加载目标会话 ──────────────────────────────────
    data = _p.load_session(arg)
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

    # 显示恢复的消息摘要（用项目流式渲染器回放）
    non_system = filter_non_system(ctx.messages)
    if ctx.ui_adapter is not None:
        ctx.ui_adapter.display_messages(non_system, speed=1000)

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


def _build_session_entries(persistence, *, limit: int = _SESSION_PREVIEW_LIMIT) -> list:
    """构建会话浏览器条目（摘要 + 预览行）。"""
    try:
        sessions = list(persistence.list_sessions() or [])
    except Exception:
        sessions = []
    out: list = []
    for s in sessions[: int(limit)]:
        if not isinstance(s, dict):
            continue
        entry = dict(s)
        try:
            data = persistence.load_session(s.get("id"))
        except Exception:
            data = None
        entry["preview_lines"] = _session_preview(data)
        out.append(entry)
    return out


def _open_sessions_ui(ctx) -> bool:
    """打开全屏会话浏览器视图（有 ChatUI 时）。返回是否已打开处理。"""
    from ..adapters.ui_runtime import get_sessions_view_state_cls
    from ._view_opener import open_fullscreen_view

    _p = _resolve_persistence(ctx)
    applied_seq = {"v": 0}

    def setup(model, state):
        state.entries = _build_session_entries(_p)

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
                state.entries = _build_session_entries(_p)
            elif action == "delete":
                try:
                    ok = bool(_p.delete_session(sid))
                except Exception:
                    ok = False
                state.status_message = "已删除" if ok else "删除失败"
                state.entries = _build_session_entries(_p)
                state.selected = 0
                state.delete_confirm = ""
        return False

    return open_fullscreen_view(
        ctx, view_id="sessions", state_attr="sessions_view",
        state_cls=get_sessions_view_state_cls(), setup=setup, on_tick=tick,
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
