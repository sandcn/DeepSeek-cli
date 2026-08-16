"""trace — 轨迹视图数据构建（DSH 风格轨迹台账，2026-08-19）。

**数据源 = agent 消息列表**（``model.message_source`` 注入的真实会话消息：
system/user/assistant(+tool_calls)/tool 返回）——对齐 DSH：轨迹从 Session
消息组装业务记录，**不是** TUI 渲染过的聊天块（blocks）。未注入消息源时
（测试/无装配场景）回退块构建路径。

消息模型 → 记录（DSH TrajectoryCellKind 语义的 TUI 细分）：

  - **system 消息** → system 记录（每条一条；摘要 = 首行，检查器可读全文）
    ——显示系统提词（agent.messages 首部即 system 分片）；
  - **user 消息** → user 记录（新轮次分隔行）；
  - **assistant 消息** → 按内容拆分：reasoning_content → reasoning 记录
    （思考）/ content → content 记录（回答）/ tool_calls → tool 记录
    （每条调用一条：摘要 = 工具名 + 关键参数）；
  - **tool 消息（返回）** → 按 ``tool_call_id`` 与对应调用**合并成一条**
    （调用 + 返回：``result`` 首行预览 + 详情 = 调用行 + 返回行）；无匹配
    调用（异常/截断）→ 独立返回记录。

依赖约束：仅依赖 app 同层（model/_state_types/toolcard）与标准库；subagent
控制器、prompt_builder、param_formatter 函数内惰性 import（避免循环依赖）。
"""

from __future__ import annotations

import time as _time
from dataclasses import dataclass, field

__all__ = [
    "TraceRecord",
    "build_trace_records",
    "TRACE_KIND_ORDER",
    "block_detail_lines",
    "_records_from_messages",
    "_messages_fingerprint",
]

#: 记录种类（展示顺序/图标映射在 trace_view 消费）
TRACE_KIND_ORDER = ("system", "user", "reasoning", "content", "tool", "subagent", "context")

#: 系统提词 TTL 缓存时长（秒）——build_system_prompt 含文件读取 + git
#:   子进程调用，台账每帧重建时仅命中缓存；超时才重新构建（空模式切换等
#:   提示词变化在 TTL 内反映）。
_SYSTEM_PROMPT_TTL = 30.0
_system_prompt_cache: tuple[float, list] | None = None

#: 工具调用参数详情最大长度（消息模型 tool_calls arguments 防御截断——
#:   与 apply._append_assistant_rich 同阈值语义）
_TOOL_DETAIL_MAX_LEN = 80


@dataclass
class TraceRecord:
    """一条轨迹记录（台账行 + 检查器详情的数据源）。

    Attributes:
        index: 1-based 记录号（#N，与 DSH 台账索引语义一致）。
        kind: 记录种类（system/user/reasoning/content/tool/subagent/context）。
        summary: 单行摘要（台账行主文本；超宽由渲染层截断）。
        status: 状态（tool/subagent：running/done/fail/error；其余空串）。
        time_seconds: 耗时秒数；None=未知（运行中/无计时）。
        tokens: token 统计 dict（input/output/live_input/live_output）。
        result: 工具返回首行预览（tool 记录；台账行与调用合并显示）。
        lines: 详情行（纯文本）——仅 system/subagent 记录内联携带（小体积）；
            块记录详情由检查器按需经 ``block_detail_lines(source_block)``
            惰性提取（大块不随台账构建全量扫描）。
        source_block: 来源 ChatBlock（块记录；system/subagent 记录为 None）。
    """

    index: int = 0
    kind: str = "context"
    summary: str = ""
    status: str = ""
    time_seconds: float | None = None
    tokens: dict = field(default_factory=dict)
    result: str = ""
    lines: list = field(default_factory=list)
    source_block: object | None = None


#: 块种类 → 轨迹记录种类（separator 跳过；splash 品牌屏跳过——非业务记录）
_BLOCK_KIND_MAP = {
    "user": "user",
    "reasoning": "reasoning",
    "content": "content",
    "tool": "tool",
    "subagent": "subagent",
    "parse_info": "context",
    "notification": "context",
    "error": "system",
    "write_line": "system",
}


def _system_prompt_parts() -> list:
    """系统提词分片（TTL 缓存——构建含文件读取/git 子进程，不每帧重建）。

    Returns:
        list[str]——提示词分片；构建失败（无 prompts 文件/异常）返回空列表
        （台账不显示 system 记录，静默降级）。
    """
    global _system_prompt_cache
    now = _time.monotonic()
    if _system_prompt_cache is not None and now - _system_prompt_cache[0] < _SYSTEM_PROMPT_TTL:
        return _system_prompt_cache[1]
    try:
        from src.prompt_builder import build_system_prompt
        parts = build_system_prompt() or []
    except Exception:
        parts = []
    _system_prompt_cache = (now, parts)
    return parts


def _system_prompt_record(index: int) -> TraceRecord | None:
    """系统提词记录（台账首条；对齐 DSH SYSTEM 记录——完整提示词状态）。

    summary 取提示词首个非空行（如角色设定首句）；lines 为完整提示词
    （全部分片拼接拆行，检查器可读全文）。
    """
    parts = _system_prompt_parts()
    if not parts:
        return None
    text = "\n\n".join(parts)
    lines = [ln for ln in text.splitlines()]
    return TraceRecord(
        index=index,
        kind="system",
        summary=_first_text(lines) or "系统提词",
        lines=lines,
    )


def _block_plain_lines(block) -> list:
    """块内 AnsiLine 的纯文本行列表（防御：非 AnsiLine 行 str() 化）。"""
    out: list = []
    for line in getattr(block, "lines", None) or []:
        plain = getattr(line, "plain", None)
        out.append(plain if plain is not None else str(line))
    return out


def _first_plain_line(block) -> str:
    """块内首个非空纯文本行（摘要用；O(首行)——不扫描全块）。

    防御：非 AnsiLine 行 str() 化；全部空白返回空串。
    """
    for line in getattr(block, "lines", None) or []:
        plain = getattr(line, "plain", None)
        t = (plain if plain is not None else str(line)) or ""
        if t.strip():
            return t
    return ""


def _first_text(lines: list) -> str:
    """纯文本行列表的首个非空行（系统提词摘要用）。"""
    for ln in lines:
        t = (ln or "").strip()
        if t:
            return t
    return ""


def _strip_user_prefix(text: str) -> str:
    """剥离用户消息行首 ``> `` 前缀（build_user_line 结构）。"""
    return text[2:] if text.startswith("> ") else text


def _tool_status_data_index(block):
    """工具块状态数据行下标（``  ✔``/``  ✖``）；无则返回 None。

    与 toolcard._tool_status_index 同语义（该函数依赖 ink 层，trace 内联
    轻量实现避免跨层导入）；渲染期跳过该数据行（状态由标题行图标表达）。
    """
    idx = (getattr(block, "extra", None) or {}).get("_status_line_index")
    if idx is not None:
        return idx
    lines = getattr(block, "lines", None) or []
    if getattr(block, "closed", False) and lines:
        last = getattr(lines[-1], "plain", None)
        if last is not None and last.strip() in ("\u2714", "\u2716"):
            return len(lines) - 1
    return None


def block_detail_lines(block) -> list:
    """块详情行（纯文本）——检查器按需提取（选中记录才调用）。

    性能（2026-08-19）：build_trace_records 只提取**摘要行**（O(块数)）；
    完整详情行仅对选中记录惰性提取（大工具输出/长回答不随台账每帧全量
    扫描）。system/subagent 记录自带 lines（小体积），不走本函数。

    工具块（2026-08-19 合并语义）：详情 = **[调用行] + [返回输出行]**——
    调用行从 extra 重建（``⚡ 工具名 参数``），剔除原始标题行（``lines[0]``
    旧式 ``  · 工具 · 参数``）与状态数据行（``  ✔``/``  ✖``，状态由检查器
    标题行图标表达）——「工具调用跟返回合并成一条」的完整详情。
    """
    lines = _block_plain_lines(block)
    kind = getattr(block, "kind", "")
    if kind != "tool":
        return lines
    extra = getattr(block, "extra", None) or {}
    tool_name = extra.get("tool_name") or "工具"
    detail = extra.get("tool_detail", "")
    call_line = f"{tool_name} {detail}".strip() or tool_name
    status_idx = _tool_status_data_index(block)
    out = [call_line]
    for i, ln in enumerate(lines):
        if i == 0:
            continue  # 原始标题行（旧式 `  · 工具 · 参数`）——调用行已重建
        if i == status_idx:
            continue  # 状态数据行——检查器标题行已表达状态
        out.append(ln)
    return out


def _tool_result_preview(block) -> str:
    """工具返回首行预览（台账行与调用合并显示；O(首行)）。

    取工具块输出行（跳过原始标题行/状态数据行）的首个非空行——如 bash
    输出 ``总用量 4462 drwxrwxr-x...``；无输出返回空串（台账行仅显示调用）。
    """
    lines = getattr(block, "lines", None) or []
    status_idx = _tool_status_data_index(block)
    for i, line in enumerate(lines):
        if i == 0 or i == status_idx:
            continue
        plain = getattr(line, "plain", None)
        t = (plain if plain is not None else str(line)) or ""
        t = t.strip()
        if t:
            return t
    return ""


def _record_from_block(block, index: int) -> TraceRecord | None:
    """聊天块 → 轨迹记录（separator/splash/空块跳过返回 None）。

    仅提取摘要（首行/O(1)）——详情行由检查器按需经 ``block_detail_lines``
    惰性提取（大块不随台账构建全量扫描）。
    """
    kind = _BLOCK_KIND_MAP.get(getattr(block, "kind", ""))
    if kind is None:
        return None
    rec = TraceRecord(index=index, kind=kind, source_block=block)
    if kind == "user":
        rec.summary = _strip_user_prefix(_first_plain_line(block))
    elif kind == "tool":
        # ★ 2026-08-19（调用+返回合并一条）：摘要 = 调用（工具名+参数），
        #   result = 返回首行预览——台账行同时显示两者（合并为一条记录）。
        extra = getattr(block, "extra", None) or {}
        tool_name = extra.get("tool_name") or "工具"
        detail = extra.get("tool_detail", "")
        rec.summary = f"{tool_name} {detail}".strip()
        rec.result = _tool_result_preview(block)
        rec.status = extra.get("tool_status", "running")
        started = extra.get("_tool_started_at")
        if started is not None:
            duration = extra.get("_tool_duration")
            rec.time_seconds = (
                duration if duration is not None
                else max(0.0, _time.monotonic() - started)
            )
    else:
        rec.summary = _first_plain_line(block)
    return rec


def _messages_fingerprint(model) -> tuple:
    """消息列表指纹（use_memo deps，消息源模式）。

    覆盖流式/追加/编辑场景：列表身份 + 长度 + 末条消息（身份/角色/内容
    长度）——流式增长（content 变长）、新消息追加、/editmsg 替换尾消息均
    触发重建；中间消息编辑（罕见，随后续追加自然刷新）短暂陈旧可接受。
    """
    source = getattr(model, "message_source", None)
    if source is None:
        return ()
    try:
        messages = source()
    except Exception:
        return ()
    if not isinstance(messages, (list, tuple)):
        return ()
    if not messages:
        return (id(messages), 0)
    tail = messages[-1]
    if isinstance(tail, dict):
        tail_fp = (
            id(tail),
            tail.get("role", ""),
            len(str(tail.get("content", ""))),
            len(tail.get("tool_calls") or ()),
        )
    else:
        tail_fp = (id(tail), str(tail)[:40])
    return (id(messages), len(messages), tail_fp)


def _content_str(content) -> str:
    """消息 content 归一化为字符串（str/list blocks/None；含 ANSI 消毒）。

    委托 ``src.tui.pipeline.message_display._content_str``（apply 历史回放
    同源）——工具输出透传的转义序列会被剥除（残留 ANSI 破坏宽度测量/渲染）。
    """
    from src.tui.pipeline.message_display import _content_str as _src
    return _src(content)


def _tool_detail(name: str, args) -> str:
    """工具调用关键参数摘要（extract_key_params——与 apply 历史回放同源）。

    Args:
        name: 工具名。
        args: arguments（str JSON 或 dict）。

    Returns:
        关键参数值文本（如 Bash → ``pwd``）；未知工具/解析失败返回空串。
    """
    try:
        from src.core.param_formatter import extract_key_params
        detail = extract_key_params(name, args) or ""
    except Exception:
        detail = ""
    if len(detail) > _TOOL_DETAIL_MAX_LEN:
        detail = detail[:_TOOL_DETAIL_MAX_LEN] + "..."
    return detail


def _records_from_messages(messages) -> tuple:
    """agent 消息列表 → (records, rows)——轨迹视图主数据源。

    对齐 DSH（从 Session 消息组装业务记录，非 TUI 渲染内容）：
      - system → system 记录（显示系统提词）；
      - user → user 记录 + 轮次分隔行；
      - assistant → reasoning/content/tool_calls 拆分（思考 → 回答 → 调用）；
      - tool 返回 → 按 tool_call_id 与调用**合并成一条**（调用 + 返回）；
        无匹配调用（异常/截断）→ 独立返回记录。

    Args:
        messages: 消息列表（agent.messages 同构：dict 列表）。

    Returns:
        (records: list[TraceRecord], rows: list[TraceRecord | None])。
    """
    records: list = []
    rows: list = []
    index = 0
    # 等待返回合并的工具调用（tool_call_id → 记录）
    pending: dict = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role", "")
        if role == "system":
            text = _content_str(msg.get("content", "")).strip()
            if not text:
                continue
            lines = text.splitlines()
            index += 1
            rec = TraceRecord(
                index=index, kind="system",
                summary=_first_text(lines) or "系统提词", lines=lines,
            )
            records.append(rec)
            rows.append(rec)
        elif role == "user":
            text = _content_str(msg.get("content", "")).strip()
            if not text:
                continue
            rows.append(None)  # 轮次分隔行（新用户消息 = 新轮次）
            lines = text.splitlines()
            index += 1
            rec = TraceRecord(
                index=index, kind="user",
                summary=_first_text(lines), lines=lines,
            )
            records.append(rec)
            rows.append(rec)
        elif role == "assistant":
            reasoning = _content_str(msg.get("reasoning_content", "")).strip()
            if reasoning:
                lines = reasoning.splitlines()
                index += 1
                rec = TraceRecord(
                    index=index, kind="reasoning",
                    summary=_first_text(lines), lines=lines,
                )
                records.append(rec)
                rows.append(rec)
            content = _content_str(msg.get("content", "")).strip()
            if content:
                lines = content.splitlines()
                index += 1
                rec = TraceRecord(
                    index=index, kind="content",
                    summary=_first_text(lines), lines=lines,
                )
                records.append(rec)
                rows.append(rec)
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                name = (fn.get("name") or "") if isinstance(fn, dict) else ""
                args = fn.get("arguments", "") if isinstance(fn, dict) else ""
                detail = _tool_detail(name, args)
                call = f"{name} {detail}".strip() or (name or "工具")
                index += 1
                rec = TraceRecord(
                    index=index, kind="tool", summary=call, lines=[call],
                )
                records.append(rec)
                rows.append(rec)
                cid = tc.get("id") or ""
                if cid:
                    pending[cid] = rec
        elif role == "tool":
            text = _content_str(msg.get("content", "")).strip()
            if not text:
                continue
            cid = msg.get("tool_call_id") or ""
            rec = pending.pop(cid, None) if cid else None
            lines = text.splitlines()
            if rec is not None:
                # ★ 工具调用 + 返回合并一条：返回追加到调用记录
                rec.result = _first_text(lines)
                rec.lines = list(rec.lines) + lines
            else:
                # 无匹配调用（异常/截断会话）→ 独立返回记录
                index += 1
                rec = TraceRecord(
                    index=index, kind="tool", summary="工具返回",
                    result=_first_text(lines),
                    lines=["工具返回"] + lines,
                )
                records.append(rec)
                rows.append(rec)
    return records, rows


def _subagent_records(index_holder: list, out_records: list, rows: list) -> None:
    """subagent 槽位 → 轨迹记录（追加到块记录之后；仅块回退路径）。

    惰性 import SubAgentPanelController（app 层不依赖装配层）；控制器不存在
    （未装配/测试）时零成本跳过。
    """
    try:
        from src.tui.subagent import SubAgentPanelController
    except Exception:
        return
    try:
        controller = SubAgentPanelController.get_default()
        store = getattr(controller, "_store", None)
        if store is None:
            return
        with store._state_lock:
            order = list(getattr(store, "_order", None) or [])
            agents = dict(getattr(store, "_agents", None) or {})
    except Exception:
        return
    for label in order:
        slot = agents.get(label)
        if slot is None:
            continue
        status = getattr(slot, "status", "") or "running"
        desc = getattr(slot, "description", "") or ""
        model_phase = getattr(slot, "model_phase", "") or ""
        parse_info = getattr(slot, "parse_info", "") or ""
        summary = label
        if desc:
            summary = f"{label} · {desc}"
        elif model_phase:
            summary = f"{label} · {model_phase}"
        if status == "running" and parse_info:
            summary = f"{summary} · {parse_info}"
        start = getattr(slot, "start_time", 0.0) or 0.0
        end = getattr(slot, "end_time", 0.0) or 0.0
        time_sec = (end - start) if end > start else None
        tokens = {
            "input": int(getattr(slot, "input_tokens", 0) or 0),
            "output": int(getattr(slot, "output_tokens", 0) or 0)
            + int(getattr(slot, "live_output_tokens", 0) or 0),
            "live_input": int(getattr(slot, "live_input_tokens", 0) or 0),
            "live_output": int(getattr(slot, "live_output_tokens", 0) or 0),
        }
        detail: list = []
        result_text = getattr(slot, "result_text", "") or ""
        result_error = getattr(slot, "result_error", "") or ""
        if result_text:
            detail.append(result_text)
        if result_error:
            detail.append(f"错误: {result_error}")
        for rec in getattr(slot, "tool_history", None) or []:
            name = getattr(rec, "tool_name", "") or ""
            det = getattr(rec, "detail", "") or ""
            phase = getattr(rec, "phase", "") or ""
            r_start = getattr(rec, "start_time", 0.0) or 0.0
            r_end = getattr(rec, "end_time", 0.0) or 0.0
            dur = f" {r_end - r_start:.1f}s" if r_end > r_start else ""
            detail.append(f"{name} {det} · {phase}{dur}")
        index_holder[0] += 1
        rec = TraceRecord(
            index=index_holder[0],
            kind="subagent",
            summary=summary,
            status=status,
            time_seconds=time_sec,
            tokens=tokens,
            lines=detail,
        )
        out_records.append(rec)
        rows.append(rec)


def build_trace_records(model) -> tuple:
    """构建轨迹记录列表 + 台账行序列。

    台账行（rows）为渲染顺序：``TraceRecord`` 或 ``None``（轮次分隔行——
    分隔行文本由渲染层按「第 N 个分隔」生成）。records 为可选记录列表
    （分隔行不参与选择/导航）。

    **数据源（2026-08-19）**：
      - ``model.message_source`` 已注入（装配经
        ``_register_session_handlers`` 接线 agent.messages）→ **消息列表构建**
        （system/user/assistant+tool_calls/tool 返回——真实会话消息，非 TUI
        渲染内容；工具调用 + 返回合并一条）；
      - 未注入/消息为空/消息源异常 → 回退块构建路径（blocks + 系统提词
        TTL 构建 + subagent 槽位——测试/无装配场景）。

    Args:
        model: AppModel 实例（message_source/blocks/status）。

    Returns:
        (records: list[TraceRecord], rows: list[TraceRecord | None])。
    """
    # ── 主路径：agent 消息列表（真实会话消息） ──
    source = getattr(model, "message_source", None)
    if source is not None:
        try:
            messages = source()
            if isinstance(messages, (list, tuple)) and messages:
                records, rows = _records_from_messages(messages)
                if records:
                    return records, rows
        except Exception:
            pass  # 消息源异常 → 回退块路径（防御）
    # ── 回退路径：TUI 块构建（无消息源/空消息/异常） ──
    records: list = []
    rows: list = []
    index = 0
    # 系统提词（台账首条；对齐 DSH SYSTEM 记录）
    sys_rec = _system_prompt_record(index + 1)
    if sys_rec is not None:
        index += 1
        records.append(sys_rec)
        rows.append(sys_rec)
    # 聊天块记录（新用户消息 = 新轮次，插入分隔行）
    turn = 0
    for block in getattr(model, "blocks", None) or []:
        if getattr(block, "kind", "") == "user":
            turn += 1
            rows.append(None)  # 轮次分隔行（新用户消息 = 新轮次）
        rec = _record_from_block(block, index + 1)
        if rec is None:
            continue
        index += 1
        rec.index = index
        records.append(rec)
        rows.append(rec)
    # 子代理记录（追加于块记录之后，按状态存储顺序）
    _subagent_records([index], records, rows)
    return records, rows


__all__ = [
    "TraceRecord",
    "build_trace_records",
    "TRACE_KIND_ORDER",
    "block_detail_lines",
    "_record_from_block",
    "_system_prompt_record",
    "_records_from_messages",
    "_messages_fingerprint",
]
