"""logs 命令 — ``/logs`` 会话日志 / 投影浏览器（全屏视图 / 文本回退）。

会话的事实源是**仅追加事件日志** ``SessionLog``（``src/core/session_log``）：
消息、轮次、步骤、结构化变更都以事件追加，模型历史由事件**投影**派生
（「模型可见即已记录」）。本命令把这份事实完整呈现：

  - **事件流**：全部会话事件（seq / 类型 / 时间 / 摘要）+ 事件 data 详情；
  - **模型历史投影**：``derive_messages()`` 派生的模型历史（每条消息摘要）；
  - **投影状态**：已注册投影单元（如 ``turnBoundary``）的增量折叠状态；
  - **一致性校验**：``SessionLog.verify(messages)``——模型可见与会话日志比对。

有活跃 ChatUI 时打开全屏「会话日志」视图（``model.fullscreen == "logs"``，
组件 ``src/tui/app/logs_view.py``，``r`` 刷新经 ``refresh_seq`` 回传重读）；
无 ChatUI（单次模式 / 测试桩）回退文本输出。

数据源不依赖任何模型调用：命令只读当前会话的 ``session.session_log`` 与
``session.messages``；投影单元状态优先取内核 ``ctx.session_projections``
服务，内核缺失时按内置投影声明本地构造（单元测试 / 独立调用同样可用）。
"""

from __future__ import annotations

import logging
from collections import Counter

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, GREEN, RESET, YELLOW
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()

#: 事件类型 → 中文标签（与视图侧图标映射同源文案；未知类型回退原类型名）。
_KIND_LABEL = {
    "turn/start": "轮次开始",
    "turn/end": "轮次结束",
    "step/start": "步骤开始",
    "step/end": "步骤结束",
    "system/message": "系统消息",
    "user/message": "用户消息",
    "assistant/message": "助手消息",
    "assistant/attempt": "助手尝试",
    "tool/result": "工具结果",
    "request/header": "请求头",
    "request/context": "请求上下文",
    "session/insert": "插入",
    "session/replace": "替换",
    "session/delete": "删除",
    "session/truncate": "截断",
    "session/reset": "重置",
}

#: 结构变更事件类型（摘要按位置/范围描述）。
_STRUCTURAL_KINDS = frozenset({
    "session/insert", "session/replace", "session/delete",
    "session/truncate", "session/reset",
})

#: 实时刷新器初始签名哨兵（与任何真实签名都不等 → 首帧强制构建）。
_REFRESHER_SENTINEL = object()


# ── 数据源 ────────────────────────────────────────────


def _session_log(ctx):
    """当前会话的事实源日志（无会话 / 非日志视图时 None）。"""
    return _session_log_of(getattr(ctx, "session", None))


def _session_log_of(session):
    """从 ChatSession 读取事实源日志（无会话 / 非日志视图时 None）。"""
    if session is None:
        return None
    try:
        return getattr(session, "session_log", None)
    except Exception:
        return None


def _messages_of(session) -> list:
    """ChatSession 的模型历史（列表快照；缺失回退空列表）。"""
    if session is None:
        return []
    try:
        return list(getattr(session, "messages", None) or [])
    except Exception:
        return []


def _raw_messages(ctx) -> list:
    """当前模型历史（列表视图；缺失回退空列表）。"""
    session = getattr(ctx, "session", None)
    messages = _messages_of(session)
    if messages:
        return messages
    return list(getattr(ctx, "messages", None) or [])


def _projection_service():
    """内核 ``session_projections`` 服务（无内核时 None）。"""
    try:
        from ...core.adapters.kernel_runtime import get_service

        return get_service("session_projections")
    except Exception:
        return None


def _local_projection_registry():
    """按内置投影声明本地构造投影注册表（无内核兜底）。"""
    from ...core.session_log import ProjectionRegistry
    from ...core.session_log.builtin_projections import (
        builtin_projection_names,
        builtin_projection_spec,
    )

    registry = ProjectionRegistry()
    for name in builtin_projection_names():
        initial, folder = builtin_projection_spec(name)
        registry.register(name, folder, initial=initial)
    return registry


# ── 文本提取辅助 ──────────────────────────────────────


def _text_of(value) -> str:
    """content（str / 多模态 content blocks）→ 纯文本。"""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        try:
            from ..multimodal import content_to_text

            return content_to_text(value)
        except Exception:
            parts = []
            for block in value:
                if isinstance(block, dict):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    elif block.get("type"):
                        parts.append(f"[{block.get('type')}]")
            return "".join(parts)
    return "" if value is None else str(value)


def _first_line(text: str, limit: int = 120) -> str:
    """文本首行（截断 + 省略号）。"""
    line = str(text or "").strip().split("\n", 1)[0]
    if len(line) > limit:
        return line[:limit] + "\u2026"
    return line


def _event_summary(kind: str, data) -> str:
    """事件 data → 单行摘要。"""
    if not isinstance(data, dict):
        return _first_line(str(data))
    if kind in _STRUCTURAL_KINDS:
        if kind == "session/delete":
            return f"删除 [{data.get('start', 0)}:{data.get('stop', 0)}]"
        if kind == "session/truncate":
            return f"截断至 {data.get('length', 0)} 条"
        if kind == "session/reset":
            return "重置（仅保留系统提词）"
        if kind == "session/replace":
            return f"替换 #{data.get('index', 0)}"
        return f"插入 @{data.get('index', 0)}"
    if "content" in data:
        summary = _first_line(_text_of(data.get("content")))
        if kind == "tool/result" and data.get("tool_call_id"):
            prefix = f"[{data.get('tool_call_id')}] "
            return (prefix + summary)[:160]
        return summary
    if data.get("tool_calls"):
        names = [
            str((call.get("function") or {}).get("name", ""))
            for call in data.get("tool_calls") or []
            if isinstance(call, dict)
        ]
        names = [n for n in names if n]
        if names:
            return "\u2699 " + ", ".join(names)
    if data:
        return ", ".join(f"{k}={_first_line(str(v), 40)}" for k, v in list(data.items())[:3])
    return "(无数据)"


def _message_summary(msg: dict) -> str:
    """模型历史单条消息 → 单行摘要。"""
    if not isinstance(msg, dict):
        return ""
    content = _first_line(_text_of(msg.get("content")))
    if not content and msg.get("tool_calls"):
        content = "\u2699 工具调用"
    return content


def _state_text(state) -> str:
    """投影状态 → 单行文本。"""
    if isinstance(state, dict):
        return "{" + ", ".join(f"{k}={v}" for k, v in state.items()) + "}"
    return str(state)


def _state_rows(state) -> list:
    """投影状态 → 逐项文本行（dict 分项；其它单行）。"""
    if isinstance(state, dict):
        return [f"{k}: {v}" for k, v in state.items()]
    return [str(state)]


# ── 数据构建（视图状态与文本回退共用） ────────────────


def build_log_entries(log) -> list:
    """会话事件 → 条目列表（seq / type / label / time / summary / data）。"""
    try:
        events = list(log.events())
    except Exception:
        return []
    entries: list = []
    for event in events:
        kind = str(getattr(event, "type", "") or "")
        data = getattr(event, "data", None)
        entries.append({
            "seq": int(getattr(event, "seq", 0) or 0),
            "type": kind,
            "label": _KIND_LABEL.get(kind, kind or "事件"),
            "time": float(getattr(event, "timestamp", 0.0) or 0.0),
            "summary": _event_summary(kind, data),
            "data": dict(data) if isinstance(data, dict) else {"data": data},
        })
    return entries


def build_log_messages(log) -> list:
    """模型历史投影 → 条目列表（index / role / summary / tool_calls）。"""
    try:
        raw = list(log.derive_messages())
    except Exception:
        return []
    out: list = []
    for i, msg in enumerate(raw):
        if not isinstance(msg, dict):
            continue
        out.append({
            "index": i,
            "role": str(msg.get("role", "")),
            "summary": _message_summary(msg),
            "tool_calls": list(msg.get("tool_calls") or []),
        })
    return out


def build_projection_entries(events) -> list:
    """投影单元状态 → 条目列表（name / state_text / state_rows）。"""
    snapshot = None
    names: list = []
    try:
        service = _projection_service()
        if service is not None:
            names = list(service.names())
            snapshot = service.snapshot(events)
        else:
            registry = _local_projection_registry()
            names = registry.names()
            snapshot = registry.snapshot(events)
    except Exception:
        return []
    if snapshot is None:
        return []
    out: list = []
    for name in names:
        state = snapshot.get(name)
        out.append({
            "name": str(name),
            "state_text": _state_text(state),
            "state_rows": _state_rows(state),
        })
    return out


def build_log_stats(events, messages) -> list:
    """统计条目列表（事件总数 / 消息数 / 各事件类型计数）。"""
    events = list(events or [])
    rows: list = [
        ("事件总数", str(len(events))),
        ("模型历史", str(len(messages or []))),
    ]
    by_kind = Counter(str(getattr(e, "type", "")) for e in events)
    for kind, count in sorted(by_kind.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append((_KIND_LABEL.get(kind, kind or "事件"), str(count)))
    return rows


def verify_log(log, messages) -> tuple:
    """一致性校验（模型历史 vs 日志投影）。

    Returns:
        ``(ok, text)``——``ok`` 为 True/False，不可校验时为 None。
    """
    try:
        ok = bool(log.verify(messages))
    except Exception:
        return None, "一致性校验不可用（会话日志或消息视图缺失）"
    if ok:
        return True, "模型历史与会话日志投影一致（模型可见即已记录）"
    return False, "模型历史与会话日志投影不一致——请检查会话日志"


def build_log_snapshot(ctx) -> dict | None:
    """构建视图状态数据（命令上下文版本；无会话日志返回 None）。"""
    return build_log_snapshot_from_session(getattr(ctx, "session", None))


def build_log_snapshot_from_session(session) -> dict | None:
    """构建视图状态数据（无会话日志返回 None）。"""
    log = _session_log_of(session)
    if log is None:
        return None
    try:
        events = list(log.events())
    except Exception:
        events = []
    messages = build_log_messages(log)
    ok, text = verify_log(log, _messages_of(session))
    return {
        "entries": build_log_entries(log),
        "messages": messages,
        "projections": build_projection_entries(events),
        "verify_ok": ok,
        "verify_text": text,
        "stats": build_log_stats(events, messages),
    }


# ── 视图 / 文本 ───────────────────────────────────────


def log_signature_from_session(session):
    """会话日志变化签名（事件数 / 末事件 seq / 消息数）；无日志返回 None。

    仅做轻量读取（不构建条目 dict），供**实时刷新**（视图渲染期刷新器 /
    命令线程轮询）检测日志增长——签名未变则不重建完整快照，签名变化
    （新事件 / 消息增删）即刷新视图。
    """
    log = _session_log_of(session)
    if log is None:
        return None
    try:
        count = int(len(log))
    except Exception:
        count = 0
    last_seq = 0
    try:
        events = log.events()
        if events:
            last_seq = int(getattr(events[-1], "seq", 0) or 0)
    except Exception:
        pass
    return (count, last_seq, len(_messages_of(session)))


def _log_signature(ctx):
    """命令上下文版本的签名（委托 ``log_signature_from_session``）。"""
    return log_signature_from_session(getattr(ctx, "session", None))


def make_logs_refresher(session, apply_state):
    """构建会话日志视图**实时刷新器**（签名变化时重建并写回状态）。

    Args:
        session: ``ChatSession``（读取 ``session_log`` / ``messages``）。
        apply_state: ``(data: dict) -> None``——把快照写入视图状态（由调用方
            绑定到 ``model.logs_view``；本函数不感知 UI 类型）。

    Returns:
        ``refresh(force: bool = False) -> bool``——是否发生重建：
        签名未变（且非 force）直接返回 False（零重建，渲染期每帧调用安全）；
        签名变化时重建快照、调用 ``apply_state`` 并返回 True。
    """
    probe = {"sig": _REFRESHER_SENTINEL}

    def _refresh(force: bool = False) -> bool:
        try:
            signature = log_signature_from_session(session)
            if signature is None:
                return False
            if not force and signature == probe["sig"]:
                return False
            data = build_log_snapshot_from_session(session)
            if data is None:
                return False
            probe["sig"] = signature
            apply_state(data)
            return True
        except Exception:
            _logger.debug("会话日志刷新器失败", exc_info=True)
            return False

    return _refresh


def _open_logs_ui(ctx) -> bool:
    """打开全屏会话日志视图（有 ChatUI 且会话日志可用时；实时刷新）。"""
    from ..adapters.ui_runtime import get_logs_view_state_cls
    from ._view_opener import open_fullscreen_view

    data = build_log_snapshot(ctx)
    if data is None:
        return False

    # 实时刷新：命令线程轮询（``open_fullscreen_view`` 每 0.05s 调 tick）按
    # 「会话日志签名」变化自动重建数据——事件流随对话实时增长（用户消息 /
    # 助手消息 / 工具结果 / 轮次步骤），视图无需手动刷新即跟进；``r`` 键经
    # refresh_seq 强制重建。
    probe = {"sig": _log_signature(ctx), "refresh": 0}

    def setup(model, state):
        for key, value in data.items():
            setattr(state, key, value)

    def tick(state) -> bool:
        current = _log_signature(ctx)
        forced = getattr(state, "refresh_seq", 0) > probe["refresh"]
        if forced:
            probe["refresh"] = state.refresh_seq
        if not forced and current == probe["sig"]:
            return False
        probe["sig"] = current
        fresh = build_log_snapshot(ctx)
        if fresh:
            for key, value in fresh.items():
                setattr(state, key, value)
        return False

    return open_fullscreen_view(
        ctx, view_id="logs", state_attr="logs_view",
        state_cls=get_logs_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="会话日志已关闭", timeout_hint="会话日志超时关闭",
    )


def _logs_text(ctx) -> bool:
    """文本列出会话日志（无 ChatUI / 单次模式回退）。"""
    log = _session_log(ctx)
    if log is None:
        _out.write(
            f"{DIM}  - 当前会话无会话日志（会话未初始化或非日志视图）{RESET}",
            level="raw", source="cmd",
        )
        return True
    try:
        events = list(log.events())
    except Exception:
        events = []
    entries = build_log_entries(log)
    messages = build_log_messages(log)
    stats = build_log_stats(events, messages)
    projections = build_projection_entries(events)
    ok, verify_text = verify_log(log, _raw_messages(ctx))

    _out.write(f"\n{DIM}  \u2500 会话日志{RESET}", level="raw", source="cmd")
    for label, value in stats:
        _out.write(f"    {label}: {value}", level="raw", source="cmd")
    _out.write(f"  {CYAN}\u25b8 事件流（{len(entries)}）{RESET}", level="raw", source="cmd")
    for entry in entries[-40:]:
        _out.write(
            f"    {CYAN}#{entry['seq']:<4}{RESET} {entry['label']}  "
            f"{DIM}{entry['summary'][:80]}{RESET}",
            level="raw", source="cmd",
        )
    if len(entries) > 40:
        _out.write(f"    {DIM}… 其余 {len(entries) - 40} 条见全屏视图{RESET}", level="raw", source="cmd")
    _out.write(f"  {CYAN}\u25b8 投影状态{RESET}", level="raw", source="cmd")
    for item in projections:
        _out.write(f"    {item['name']}: {item['state_text']}", level="raw", source="cmd")
    marker = GREEN if ok else (YELLOW if ok is None else "\x1b[31m")
    _out.write(f"  {marker}{verify_text}{RESET}", level="raw", source="cmd")
    return True


def _cmd_logs(ctx) -> bool:
    """打开会话日志视图（有 ChatUI）或文本列出（回退）。"""
    if _open_logs_ui(ctx):
        return True
    return _logs_text(ctx)


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class LogsCommand(CommandPlugin):
    """会话日志 / 投影浏览器（/logs）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="logs",
            description="会话日志 / 投影浏览器（事件流 / 模型历史 / 投影状态 / 一致性）",
            group="session",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_logs(ctx)


declare_command_plugin(LogsCommand())
