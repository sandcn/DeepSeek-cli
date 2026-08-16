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

**subagent 轨迹嵌套（2026-08-16 用户需求）**：主轨迹中选中 subagent 记录
按 Enter → 进入该 subagent 的轨迹 Trace（``build_subagent_trace_records``
——数据源 = SubAgent 完整消息列表（``slot.messages``，SubAgent.messages
实时引用，运行中/已完成均可用），经 ``_records_from_messages`` 构建——
显示内容与 mainagent 完全一致（system/user/思考/回答/工具调用+返回合并，
左台账 + 右检查器）；messages 缺失（未注册/异常）时回退槽位活动记录
（提词 + 工具历史 + 结果）。主轨迹（消息源模式）同样追加 subagent 记录
（``subagent_label`` 填充，Enter 下钻数据源）。

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
    "_live_records",
    "_live_fingerprint",
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
        subagent_label: subagent 记录关联的 subagent label（Enter 进入其
            轨迹 Trace 用；非 subagent 记录为空串）。
        tool_call_id: 工具调用唯一 ID（tool_call_id；tool 记录专用——主轨迹
            台账按此把 subagent 记录合并到对应的 dispatch_agent 工具调用
            记录；块回退路径/无 ID 为空串）。
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
    subagent_label: str = ""
    tool_call_id: str = ""


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
        # ★ 2026-08-19（用户需求：轨迹 Trace 正在生成的内容动态显示）：
        #   块回退路径开放块（流式生成中的思考/回答，未关闭）标记
        #   running——台账 ● 图标动态显示正在生成的内容（消息源模式经
        #   ``_live_records`` 另行处理，此处覆盖无消息源的块回退场景）。
        if kind in ("reasoning", "content") and not getattr(block, "closed", False):
            rec.status = "running"
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


def _block_content_len(b) -> int:
    """开放块行内容总长（增量统计缓存）。

    ★ 性能（2026-08-19 优化）：``_live_fingerprint`` 每帧计算（流式期间
    live 内容逐帧增长）——块 lines 为 append-only（渲染管线只追加），缓存
    ``(id(lines), len, total)`` 到块上后仅统计**新增行**长度，避免每帧全量
    重扫已统计行（修复前流式内容越长每帧扫描越久，累积 O(n²)）。行数倒退 /
    lines 引用变化（非 append-only 异常）→ 缓存条件不满足 → 全量重算。
    统计语义与全量一致（``sum(len(plain))``），指纹精确稳定。
    """
    lines = getattr(b, "lines", None) or []
    cache = getattr(b, "_live_len_cache", None)
    if cache is not None:
        cid, clen, ctotal = cache
        if cid == id(lines) and len(lines) >= clen:
            add = 0
            for ln in lines[clen:]:
                plain = getattr(ln, "plain", None)
                add += len(plain if plain is not None else str(ln))
            b._live_len_cache = (cid, len(lines), ctotal + add)
            return ctotal + add
    total = 0
    for ln in lines:
        plain = getattr(ln, "plain", None)
        total += len(plain if plain is not None else str(ln))
    b._live_len_cache = (id(lines), len(lines), total)
    return total


def _live_fingerprint(model) -> tuple:
    """实时生成内容指纹（use_memo deps，消息源模式）。

    流式生成期间 agent.messages **不变化**（assistant 消息完成才追加）——
    仅靠消息指纹无法触发轨迹台账重建 → 台账不显示正在生成的内容。叠加
    实时元素：
      - 开放块（reasoning/content 未关闭）：种类 + 行数 + 内容总长度——
        流式增长（行追加/内容变长）即时触发重建（内容总长经
        ``_block_content_len`` 增量统计，不每帧全量重扫）；
      - 运行中工具（tool_boxes 未关闭 box）：tool_id + 状态 + 输出行数——
        工具输出刷新即时触发重建。

    时间基元素不入指纹（台账静态色，不随动画重建）。流式完成（块关闭/
    工具 box pop）后实时元素消失 → 指纹回退基线（消息指纹接管，无重复
    重建）。
    """
    fp: list = []
    for b in getattr(model, "blocks", None) or []:
        if getattr(b, "closed", False):
            continue
        if getattr(b, "kind", "") not in ("reasoning", "content"):
            continue
        lines = getattr(b, "lines", None) or []
        fp.append((getattr(b, "kind", ""), len(lines), _block_content_len(b)))
    for key, box in (getattr(model, "tool_boxes", None) or {}).items():
        if getattr(box, "closed", False):
            continue
        extra = getattr(box, "extra", None) or {}
        lines = getattr(box, "lines", None) or []
        fp.append((key, extra.get("tool_status", ""), len(lines)))
    return tuple(fp)


def _live_records(model, index_holder: list, out_records: list, rows: list,
                  merged_tool_ids: set = None) -> None:
    """实时生成记录（消息源模式：轨迹 Trace 正在生成的内容动态显示）。

    消息源（agent.messages）只在**流式完成后**才追加 assistant 消息/工具
    返回——模型正在生成期间（思考/回答/工具执行中）消息记录看不到进行中
    内容。本函数从 TUI 模型提取「正在生成」内容，追加为 running 记录
    （台账尾部；trace_selected=-1 跟随尾部自动展示最新生成内容）：

      - 开放 reasoning/content 块（``reasoning_block_index`` /
        ``content_block_index`` 指向、未关闭）→ reasoning/content 记录
        （摘要 = 当前已生成首行；lines = 当前内容快照；● running）；
      - 运行中工具（``tool_boxes`` 未关闭 box）→ tool 记录（摘要 = 调用，
        result = 当前输出首行预览，耗时 = 已运行时长；● running）。

    流式完成（块关闭 / 工具 box pop）后记录**自然消失**——assistant 消息
    随后出现在消息源 → 消息记录接管（无重复：实时记录仅覆盖未关闭块）。
    消息源路径专用；块回退路径开放块已由 ``_record_from_block`` 表达
    （本函数不重复调用——防御：调用方仅消息源模式）。

    Args:
        merged_tool_ids: 已合并进消息源 tool 记录的 dispatch_agent
            tool_call_id 集合（``_subagent_records`` 合并返回）——这些
            dispatch box 运行中不再追加重复 running tool 记录（用户需求
            2026-08-17：agent 内容合并到 dispatch_agent 后运行期也只显示
            一条「⚡ ● dispatch_agent … · agent-N …」，修复前显示两条相同
            调用行）；None/空 = 无合并（独立 subagent 场景）。
    """
    if model is None:
        return
    blocks = getattr(model, "blocks", None) or []
    # ── 1. 开放推理/内容块（流式生成中的思考/回答） ──
    for attr in ("reasoning_block_index", "content_block_index"):
        idx = getattr(model, attr, -1)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(blocks)):
            continue
        block = blocks[idx]
        if getattr(block, "closed", False):
            continue
        kind = getattr(block, "kind", "")
        if kind not in ("reasoning", "content"):
            continue
        lines = _block_plain_lines(block)
        if not lines:
            continue
        summary = _first_text(lines)
        if not summary:
            summary = "（正在思考…）" if kind == "reasoning" else "（正在生成…）"
        index_holder[0] += 1
        rec = TraceRecord(
            index=index_holder[0],
            kind=kind,
            summary=summary,
            status="running",
            lines=list(lines),
            # ★ 2026-08-17（用户需求：回答/思考用流式 markdown 显示在右边）：
            #   live 记录挂 source_block → 检查器直接复用块渲染输出（AnsiLine
            #   已带 markdown 样式：标题青色粗体/代码 pygments 高亮等）——流式
            #   生成中动态显示正在生成的 markdown 格式内容；不二次解析（渲染
            #   输出行二次 markdown 解析会把代码块标题行误判）。
            source_block=block,
        )
        out_records.append(rec)
        rows.append(rec)
    # ── 2. 运行中的工具（tool_boxes 未关闭 box） ──
    # key = tool_call_id（tool_boxes 按 tool_id 存取，见 _tool_output_mixin）
    for key, box in (getattr(model, "tool_boxes", None) or {}).items():
        if getattr(box, "closed", False):
            continue
        # ★ 2026-08-17（用户需求：agent 内容合并到 dispatch_agent）：
        #   已合并进消息源 tool 记录的 dispatch box 跳过——运行期不重复
        #   追加 running tool 记录（一条记录表达 dispatch 调用 + agent
        #   状态；修复前运行期显示两条相同调用行）
        if merged_tool_ids and key in merged_tool_ids:
            continue
        extra = getattr(box, "extra", None) or {}
        tool_name = extra.get("tool_name") or "工具"
        detail = extra.get("tool_detail", "")
        call = f"{tool_name} {detail}".strip() or tool_name
        lines = _block_plain_lines(box)
        # 输出首行预览（跳过标题行 lines[0]——与消息记录 result 语义一致；
        # strip 去除工具输出行前缀，与 ``_tool_result_preview`` 对齐）
        result = ""
        for i, ln in enumerate(lines):
            if i == 0:
                continue
            if ln.strip():
                result = ln.strip()
                break
        started = extra.get("_tool_started_at")
        time_sec = None
        if started is not None:
            try:
                time_sec = max(0.0, _time.monotonic() - float(started))
            except (TypeError, ValueError):
                time_sec = None
        index_holder[0] += 1
        # 详情 = 调用行 + 当前输出行（剔除原始标题行——与
        # ``block_detail_lines`` 合并语义一致）
        detail_lines = [call] + [ln for i, ln in enumerate(lines) if i != 0]
        rec = TraceRecord(
            index=index_holder[0],
            kind="tool",
            summary=call,
            status="running",
            result=result or "（运行中…）",
            lines=detail_lines,
            time_seconds=time_sec,
        )
        out_records.append(rec)
        rows.append(rec)


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
                cid = tc.get("id") or ""
                index += 1
                rec = TraceRecord(
                    index=index, kind="tool", summary=call, lines=[call],
                    # ★ 2026-08-17（用户需求：agent 内容合并到 dispatch_agent）：
                    #   保存 tool_call_id——dispatch_agent 调用记录凭此与
                    #   subagent 槽位（dispatch_label = tool_call_id）匹配合并
                    #   （同轮并行多次 dispatch 时精确关联各自 agent）。
                    tool_call_id=cid,
                )
                records.append(rec)
                rows.append(rec)
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


def _subagent_label_order(order: list, archive: dict) -> list:
    """subagent 标签遍历顺序（store 未注册槽位在前 + 存档去重合并）。

    供 ``_subagent_records``（主轨迹记录构建）与
    ``trace_view._subagent_fingerprint``（use_memo 指纹）**复用**——两处
    数据源与遍历顺序保持单一实现（review 方向：避免记录/指纹逻辑漂移）。
    顺序语义：面板 store 中未注册槽位（异常路径）优先，随后按注册顺序
    追加轨迹存档槽位（注册过的全部：运行中 + 已完成保留）。
    """
    labels: list = []
    for label in order:
        if label not in archive:
            labels.append(label)
    for label in archive:
        if label not in labels:
            labels.append(label)
    return labels


def _subagent_tool_targets(out_records: list) -> dict:
    """tool 记录 tool_call_id → TraceRecord（dispatch_agent 合并目标表）。

    收集消息源路径构建的 tool 记录（含 ``tool_call_id``；块回退路径的 tool
    记录无 tool_call_id 不参与匹配）。subagent 槽位的 ``dispatch_label``
    凭此找到对应的 dispatch_agent 调用记录进行合并（同轮并行多次 dispatch
    时按 tool_call_id 精确关联，不串位）。
    """
    targets: dict = {}
    for rec in out_records:
        if getattr(rec, "kind", "") != "tool":
            continue
        cid = getattr(rec, "tool_call_id", "") or ""
        if cid:
            targets[cid] = rec
    return targets


def _subagent_slot_summary(slot, label: str, status: str) -> str:
    """subagent 槽位摘要（``label · desc`` / model_phase / parse_info）。

    独立 subagent 记录与合并进 tool 记录的摘要共用（单一实现——避免两处
    摘要逻辑漂移）。
    """
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
    return summary


def _subagent_slot_metrics(slot) -> tuple:
    """subagent 槽位（耗时, tokens）提取（独立记录与合并路径共用）。"""
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
    return time_sec, tokens


def _subagent_slot_detail(slot) -> list:
    """subagent 槽位详情行（结果文本/错误 + 工具历史摘要）。"""
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
    return detail


def _merge_subagent_into_tool_record(rec, slot, label: str) -> None:
    """把 subagent 内容合并进 dispatch_agent 工具调用记录（不分两条）。

    ★ 2026-08-17（用户需求：agent 的内容合并到 dispatch_agent，不要分
    两个）：主轨迹台账中 dispatch_agent 派生的 subagent 记录**合并到对应
    工具调用记录**——一条记录同时表达「dispatch 调用 + agent 完成」
    （修复前同一次 dispatch 显示两条：⚡ dispatch_agent 行 + 🤖 agent-N
    行）。合并后：
      - ``status``/``time_seconds``/``tokens`` 用 subagent 槽位值（agent
        生命周期：● 运行中 / ✔ 完成 / ✖ 失败）；
      - ``result`` = subagent 摘要（``agent-N · desc``）——台账行预览显示
        agent 内容（``· agent-N · desc`` 暗灰，对齐「工具调用+返回合并」）；
      - ``lines`` = 原调用行 + 原返回行 + subagent 详情（结果/错误/工具
        历史）——检查器完整表达两次动作；
      - ``subagent_label`` = label——Enter 仍可下钻查看 subagent 轨迹。

    本函数仅更新 rec（tool 记录已在 records/rows 中，索引不新增）。
    """
    status = getattr(slot, "status", "") or "running"
    rec.status = status
    time_sec, tokens = _subagent_slot_metrics(slot)
    rec.time_seconds = time_sec
    rec.tokens = tokens
    rec.result = _subagent_slot_summary(slot, label, status)
    rec.subagent_label = label
    detail = list(rec.lines) if rec.lines else [rec.summary]
    detail.append(rec.result)
    detail.extend(_subagent_slot_detail(slot))
    rec.lines = detail


def _subagent_records(index_holder: list, out_records: list, rows: list) -> set:
    """subagent 槽位 → 轨迹记录（追加到块记录之后）。

    惰性 import SubAgentPanelController（app 层不依赖装配层）；控制器不存在
    （未装配/测试）时零成本跳过。

    ★ 2026-08-17（用户需求：已完成 subagent 仍可查看轨迹）：数据源 = 面板
    store（未注册槽位，异常路径）+ **轨迹存档**（``controller._trace_archive``
    ——``register_subagent`` 注册过的全部槽位，含运行中与已完成）。``stop()``
    清空 store 后存档保留 → 主轨迹仍显示已完成 subagent 记录（Enter 可进入
    查看完整轨迹）；遍历顺序：store 未注册槽位（异常路径）在前，存档
    （注册顺序）在后。

    ★ 2026-08-17（用户需求：agent 的内容合并到 dispatch_agent，不要分
    两个）：槽位带 ``dispatch_label``（AgentAddedEvent 传入的 dispatch_agent
    tool_call_id）且消息源存在匹配 tool 记录（``tool_call_id``）时，subagent
    内容**合并进该 tool 记录**（``_merge_subagent_into_tool_record``，不再
    生成独立 subagent 记录——主轨迹一条记录表达 dispatch 调用 + agent
    完成）；无关联 dispatch（独立执行/历史恢复槽位）仍生成独立 subagent
    记录（零回归）。

    Returns:
        set[str]——已合并进 tool 记录的 dispatch_agent tool_call_id 集合
        （供 ``_live_records`` 跳过这些 box 的重复 running 记录，运行期
        也只显示一条）；无合并返回空 set。
    """
    try:
        from src.tui.subagent import SubAgentPanelController
    except Exception:
        return set()
    try:
        controller = SubAgentPanelController.get_default()
        store = getattr(controller, "_store", None)
        if store is None:
            return set()
        with store._state_lock:
            order = list(getattr(store, "_order", None) or [])
            agents = dict(getattr(store, "_agents", None) or {})
            archive = dict(getattr(controller, "_trace_archive", None) or {})
    except Exception:
        return set()
    labels = _subagent_label_order(order, archive)
    # ★ 2026-08-17：dispatch_agent 合并目标表（tool_call_id → tool 记录）
    targets = _subagent_tool_targets(out_records)
    merged_tool_ids: set = set()
    for label in labels:
        slot = agents.get(label) or archive.get(label)
        if slot is None:
            continue
        # ★ 合并路径：槽位关联 dispatch_agent 且消息源存在匹配 tool 记录
        dispatch_label = getattr(slot, "dispatch_label", "") or ""
        target = targets.get(dispatch_label) if dispatch_label else None
        # ★ P3-1（review）：target 已被其他槽位合并（异常数据：多个槽位共享
        #   同一 dispatch_label）时，当前槽位**降级为独立 subagent 记录**——
        #   不覆盖已合并内容（第一个 agent 不丢失），自身也保持可见。
        if target is not None and not getattr(target, "subagent_label", ""):
            _merge_subagent_into_tool_record(target, slot, label)
            merged_tool_ids.add(target.tool_call_id)
            continue
        status = getattr(slot, "status", "") or "running"
        summary = _subagent_slot_summary(slot, label, status)
        time_sec, tokens = _subagent_slot_metrics(slot)
        detail = _subagent_slot_detail(slot)
        index_holder[0] += 1
        rec = TraceRecord(
            index=index_holder[0],
            kind="subagent",
            summary=summary,
            status=status,
            time_seconds=time_sec,
            tokens=tokens,
            lines=detail,
            subagent_label=label,
        )
        out_records.append(rec)
        rows.append(rec)
    return merged_tool_ids


def _subagent_live_records(index_holder: list, out_records: list, rows: list,
                           slot) -> None:
    """subagent 轨迹动态部分（运行中内容，对齐 mainagent ``_live_records`` 语义）。

    SubAgent 模型调用为**流式管线**（silent=True 也发布 ReasoningChunkEvent/
    ContentChunkEvent，label = subagent label）——``SubAgentPanelController``
    把 chunk 累积到 ``slot.live_reasoning``/``slot.live_content``（新阶段
    thinking/answering 开始重置）。本函数把「正在生成」内容追加为 running
    记录（与 mainagent 轨迹的 ● running 记录同语义：实际内容动态显示，
    整轮完成后由 messages 记录接管，无重复）：

      - 运行中工具（``tool_history`` phase=running/parsing）→ running tool
        记录（摘要 = 调用，耗时 = 已运行时长；● running）——对齐 mainagent
        ``_live_records`` 的运行中工具分支；
      - 运行中模型阶段（``slot.status == "running"``）→ 流式**实际内容**
        running 记录：thinking → reasoning（live_reasoning 逐帧增长）；
        answering → reasoning（本轮若有）+ content（live_content）——
        与 mainagent 同时显示 reasoning/content 开放块语义一致；无流式
        内容（阶段事件已到但 chunk 未到）不追加记录（对齐 mainagent
        开放块无行跳过）。

    时间基元素（耗时）不入指纹（台账静态色，不随动画重建）；阶段/工具
    phase/流式内容长度变化由 ``_subagent_trace_deps`` 的 live 指纹驱动重建。
    """
    if slot is None:
        return
    now = _time.time()
    # ── 1. 运行中工具（对齐 mainagent _live_records 运行中工具分支） ──
    for rec in getattr(slot, "tool_history", None) or []:
        phase = getattr(rec, "phase", "") or ""
        if phase not in ("running", "parsing"):
            continue
        name = getattr(rec, "tool_name", "") or ""
        det = getattr(rec, "detail", "") or ""
        call = f"{name} {det}".strip() or (name or "工具")
        start = getattr(rec, "start_time", 0.0) or 0.0
        time_sec = max(0.0, now - start) if start > 0 else None
        index_holder[0] += 1
        tool_rec = TraceRecord(
            index=index_holder[0], kind="tool", summary=call,
            status="running", result="（运行中…）",
            time_seconds=time_sec, lines=[call],
        )
        out_records.append(tool_rec)
        rows.append(tool_rec)
    # ── 2. 运行中模型阶段（流式实际内容——与 mainagent 开放块同语义） ──
    if (getattr(slot, "status", "") or "") != "running":
        return
    phase = getattr(slot, "model_phase", "") or ""
    live_reasoning = (getattr(slot, "live_reasoning", "") or "").strip()
    live_content = (getattr(slot, "live_content", "") or "").strip()
    if phase in ("thinking", "reasoning"):
        # 思考阶段：显示正在生成的思考（实际流式内容，逐帧增长）
        if live_reasoning:
            lines = live_reasoning.splitlines()
            index_holder[0] += 1
            rec = TraceRecord(
                index=index_holder[0], kind="reasoning",
                summary=_first_text(lines) or "（正在思考…）",
                status="running", lines=lines,
            )
            out_records.append(rec)
            rows.append(rec)
    elif phase in ("generating", "answering", "batch"):
        # 回答阶段：显示思考（若本轮已有）+ 回答（正在生成的实际内容）——
        # 与 mainagent 同时显示 reasoning/content 记录语义一致（SubAgent
        # messages 整轮完成后才追加，动态部分先行展示实际生成内容）
        if live_reasoning:
            lines = live_reasoning.splitlines()
            index_holder[0] += 1
            rec = TraceRecord(
                index=index_holder[0], kind="reasoning",
                summary=_first_text(lines) or "（正在思考…）",
                status="running", lines=lines,
            )
            out_records.append(rec)
            rows.append(rec)
        if live_content:
            lines = live_content.splitlines()
            index_holder[0] += 1
            rec = TraceRecord(
                index=index_holder[0], kind="content",
                summary=_first_text(lines) or "（正在生成…）",
                status="running", lines=lines,
            )
            out_records.append(rec)
            rows.append(rec)


def _subagent_slot(label: str):
    """按 label 获取 subagent 槽位（面板 store / 轨迹存档）；控制器不存在/
    未装配返回 None。

    ★ 2026-08-17（用户需求：已完成 subagent 仍可查看轨迹）：优先查面板
    store（运行中/新批次槽位——窗口期存档未覆盖时读最新），store 未命中
    再查**轨迹存档**（``controller._trace_archive``——``register_subagent``
    注册过的槽位，``stop()`` 清空 store 后仍保留 → 已完成 subagent 轨迹
    可构建）。
    """
    if not label:
        return None
    try:
        from src.tui.subagent import SubAgentPanelController
        controller = SubAgentPanelController.get_default()
        store = getattr(controller, "_store", None)
        if store is None:
            return None
        with store._state_lock:
            slot = store._agents.get(label)
            if slot is not None:
                return slot
            archive = dict(getattr(controller, "_trace_archive", None) or {})
            return archive.get(label)
    except Exception:
        return None


def _subagent_fallback_records(label: str, slot) -> tuple:
    """subagent 槽位活动记录（无 messages 时的回退路径）。

    messages 缺失（异常/未注册）时，从槽位构建「活动轨迹」：
      - user 记录（初始提词）；
      - tool 记录（工具历史：调用 + 状态 + 耗时）；
      - content 记录（结果文本/错误）。
    """
    records: list = []
    rows: list = []
    index = 0
    prompt = (getattr(slot, "prompt", "") or "").strip()
    if prompt:
        lines = prompt.splitlines()
        index += 1
        rec = TraceRecord(
            index=index, kind="user",
            summary=_first_text(lines) or label,
            lines=lines,
        )
        records.append(rec)
        rows.append(rec)
    for rec in getattr(slot, "tool_history", None) or []:
        name = getattr(rec, "tool_name", "") or ""
        det = getattr(rec, "detail", "") or ""
        phase = getattr(rec, "phase", "") or ""
        status = {"done": "done", "fail": "fail", "running": "running",
                  "parsing": "running"}.get(phase, "")
        r_start = getattr(rec, "start_time", 0.0) or 0.0
        r_end = getattr(rec, "end_time", 0.0) or 0.0
        time_sec = (r_end - r_start) if r_end > r_start else None
        call = f"{name} {det}".strip() or (name or "工具")
        index += 1
        tool_rec = TraceRecord(
            index=index, kind="tool", summary=call,
            status=status, time_seconds=time_sec, lines=[call],
        )
        records.append(tool_rec)
        rows.append(tool_rec)
    result_error = (getattr(slot, "result_error", "") or "").strip()
    result_text = (getattr(slot, "result_text", "") or "").strip()
    if result_error:
        lines = [f"错误: {result_error}"]
        index += 1
        rec = TraceRecord(
            index=index, kind="content", summary="错误", lines=lines,
        )
        records.append(rec)
        rows.append(rec)
    elif result_text:
        lines = result_text.splitlines()
        index += 1
        rec = TraceRecord(
            index=index, kind="content",
            summary=_first_text(lines) or "结果",
            lines=lines,
        )
        records.append(rec)
        rows.append(rec)
    return records, rows


def build_subagent_trace_records(label: str, model=None) -> tuple:
    """构建单个 subagent 的轨迹记录（台账 + 检查器，显示内容与 mainagent 一致）。

    主轨迹中选中 subagent 记录按 Enter 进入（嵌套 TraceView）：数据源为
    SubAgent 完整消息列表（``slot.messages``——SubAgent.messages 实时引用，
    运行中/已完成均可用），经 ``_records_from_messages`` 构建——与 mainagent
    轨迹完全同构（system/user/思考/回答/工具调用+返回合并一条）。messages
    缺失（未注册/异常）时回退槽位活动记录（提词 + 工具历史 + 结果）。

    Args:
        label: subagent 标识（如 "agent-1"）。
        model: AppModel 实例（备用，当前未使用——保留签名一致性）。

    Returns:
        (records: list[TraceRecord], rows: list[TraceRecord | None])。
    """
    slot = _subagent_slot(label)
    if slot is not None:
        messages = getattr(slot, "messages", None) or []
        if isinstance(messages, (list, tuple)) and messages:
            records, rows = _records_from_messages(messages)
            if records:
                # ★ 2026-08-16（用户需求：subagent 动态部分显示也跟 mainagent
                #   一样）：追加运行中内容（运行中工具 / 正在思考/生成占位）
                #   ——● running 记录动态显示 subagent 正在生成的内容，完成后
                #   由消息记录接管（无重复）。
                _subagent_live_records([len(records)], records, rows, slot)
                return records, rows
    # 回退：槽位活动记录（无 messages / 消息为空 / 槽位不存在）
    if slot is not None:
        records, rows = _subagent_fallback_records(label, slot)
        _subagent_live_records([len(records)], records, rows, slot)
        return records, rows
    return [], []


def _block_fallback_records(model) -> tuple:
    """回退块路径记录构建（无消息源/消息为空/消息源异常——测试/无装配）。

    系统提词（TTL 缓存）+ 聊天块记录（新用户消息插轮次分隔行）+ subagent
    槽位记录。开放块（流式生成中的思考/回答）已由 ``_record_from_block``
    标记 running——调用方**不得**再追加 live 记录（防重复）。
    """
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
    for block in getattr(model, "blocks", None) or []:
        if getattr(block, "kind", "") == "user":
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


def _messages_payload(model) -> tuple:
    """消息源模式 payload——消息+subagent 记录（**不含 live 记录**）。

    ★ 性能（2026-08-19 优化）：消息记录与 live 记录**拆分缓存**——流式
    生成期间 agent.messages **不变**（assistant 消息/工具返回完成才追加），
    live 内容逐帧增长只重建 live 段（``_with_live_records``），历史消息
    记录（大 system 提示词全文/工具调用参数解析/ANSI 消毒）零重建（修复前
    live 指纹变化驱动整树重建，长会话每帧 O(全部消息内容)）。

    返回语义（供 use_memo 缓存 + ``_with_live_records`` 消费）：
      - ``is_message_path=True``：消息列表构建成功（records/rows 为消息+
        subagent 记录，merged_tool_ids 为已合并进 tool 记录的 dispatch
        tool_call_id 集合——live 追加时跳过这些 box 的重复 running 记录）；
      - ``is_message_path=False``：回退块路径（消息为空/异常/无消息源）——
        records/rows 为块记录，开放块已表达 running，调用方不得追加 live。

    Returns:
        (records, rows, merged_tool_ids, is_message_path)。
    """
    source = getattr(model, "message_source", None)
    if source is not None:
        try:
            messages = source()
            if isinstance(messages, (list, tuple)) and messages:
                records, rows = _records_from_messages(messages)
                if records:
                    # ★ 2026-08-16（轨迹 Trace 嵌套）：消息源模式下也追加
                    #   subagent 记录（主轨迹可选中按 Enter 进入 subagent 轨迹；
                    #   修复前 subagent 记录仅回退路径存在——装配场景看不到）。
                    #   ★ 2026-08-17（用户需求：agent 内容合并到 dispatch_agent）：
                    #   _subagent_records 返回已合并进 tool 记录的 dispatch
                    #   tool_call_id 集合——live 段据此跳过这些 box 的重复
                    #   running 记录（运行期也只显示一条）。
                    merged_tool_ids = _subagent_records(
                        [len(records)], records, rows,
                    )
                    return records, rows, merged_tool_ids, True
        except Exception:
            pass  # 消息源异常 → 回退块路径（防御）
    records, rows = _block_fallback_records(model)
    return records, rows, set(), False


def _with_live_records(payload, model) -> tuple:
    """在消息 payload 上追加 live 记录（消息源模式流式动态显示）。

    ★ 性能（2026-08-19 优化）：浅拷贝 records/rows 再追加——不污染 payload
    缓存（use_memo 复用）。live 指纹变化（内容逐帧增长）仅重建 live 段，
    payload 缓存命中时历史消息记录零重建。

    Args:
        payload: ``_messages_payload`` 返回值
            ``(records, rows, merged_tool_ids, is_message_path)``。
        model: AppModel 实例。

    Returns:
        (records, rows)——完整记录（消息+subagent+live）。块回退路径
        （is_message_path=False）原样返回（开放块已表达 running，不重复
        追加 live）。
    """
    records, rows, merged_tool_ids, is_message_path = payload
    if not is_message_path:
        return records, rows
    records2, rows2 = list(records), list(rows)
    # ★ 2026-08-19（用户需求：轨迹 Trace 正在生成的内容也要动态显示）：
    #   消息源（agent.messages）仅在流式完成后才追加 assistant 消息/工具
    #   返回——模型生成期间（思考/回答/工具执行中）从 TUI 模型提取「正在
    #   生成」内容追加为 running 记录（台账尾部；trace_selected=-1 跟随
    #   尾部自动展示最新生成内容；完成后记录消失由消息记录接管，无重复）。
    _live_records(model, [len(records2)], records2, rows2,
                  merged_tool_ids=merged_tool_ids)
    return records2, rows2


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

    实现（★ 性能 2026-08-19）：委托 ``_messages_payload``（消息+subagent
    记录）+ ``_with_live_records``（live 记录追加）——TraceView 两段
    use_memo 与外部调用共用同一实现（不漂移）。

    Args:
        model: AppModel 实例（message_source/blocks/status）。

    Returns:
        (records: list[TraceRecord], rows: list[TraceRecord | None])。
    """
    return _with_live_records(_messages_payload(model), model)


__all__ = [
    "TraceRecord",
    "build_trace_records",
    "build_subagent_trace_records",
    "TRACE_KIND_ORDER",
    "block_detail_lines",
    "_record_from_block",
    "_system_prompt_record",
    "_records_from_messages",
    "_messages_fingerprint",
    "_live_records",
    "_live_fingerprint",
    "_block_content_len",
    "_block_fallback_records",
    "_messages_payload",
    "_with_live_records",
    "_subagent_live_records",
    "_subagent_slot",
    "_subagent_label_order",
    "_subagent_tool_targets",
    "_merge_subagent_into_tool_record",
]
