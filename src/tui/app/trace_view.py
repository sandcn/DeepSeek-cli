"""trace_view — TraceView 轨迹视图组件（DSH 风格左台账 + 右检查器，2026-08-19）。

Ctrl+H（0x08）打开/关闭：App 在 ``model.trace_open`` 时**整屏只渲染本组件**
（消息区/顶部标题栏/状态栏/输入区全部不显示——「打开时其他 TUI 不显示，
只显示这个界面」），台账/检查器占满整个终端高度；Esc/Ctrl+H 关闭后恢复
完整聊天界面。

布局（React Ink 左右布局）：
  - 左栏「台账」：轮次分隔行 + 记录行（#N · 种类图标 · 摘要 · 右对齐耗时），
    选中行整行高亮（▶ 标记 + 背景色）；仅渲染可见窗口（虚拟窗口，行数 =
    终端高度自适应）；
  - 右栏「检查器」：选中记录详情（#N 种类 · 状态图标/耗时/token · 内容行，
    按栏宽换行 + 视口行数截断）。

记录数据：``build_trace_records``（agent 消息列表为主数据源；use_memo 指纹
缓存——消息/块内容变化才重建；详情行仅对选中记录惰性提取）。

键盘（use_input 路由，trace_open 期间激活）：
  - ↑↓ 选择 · PgUp/PgDn 翻页 · Home/End、g/G 首末 · Esc/Ctrl+H 关闭；
  - Enter/其余按键**放行**（无输入区显示；Enter 仍可提交消息——轨迹界面
    持续显示会话最新记录）。
"""

from __future__ import annotations

from src.tui._format import format_duration, format_tokens
from src.tui._input_layout import _wrap_by_width
from src.tui.app.trace import block_detail_lines, build_trace_records
from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input, use_memo
from src.tui.ink.helpers import truncate_runs

# ── 样式 ─────────────────────────────────────────────
_S_TITLE = Style(fg=45, bold=True)        # 视图标题前缀（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/分隔弱化（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 轮次分隔行（深灰）
_S_INDEX = Style(fg=242)                   # #N 记录号（暗灰）
_S_TIME = Style(fg=110)                    # 耗时（浅蓝）
_S_TEXT = Style(fg=252)                    # 摘要/内容文本（亮白）
_S_DIM = Style(fg=242)                     # 推理摘要/元信息（暗灰）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237，不呼吸）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）

#: 种类图标（台账行）与名称（检查器标题）——对齐既有角色头 emoji 语义
_KIND_ICON = {
    "system": "\u2699", "user": "\U0001F464", "reasoning": "\U0001F4AD",
    "content": "\U0001F4AC", "tool": "\u26A1", "subagent": "\U0001F916",
    "context": "\U0001F4C4",
}
_KIND_NAME = {
    "system": "系统", "user": "用户", "reasoning": "思考", "content": "回答",
    "tool": "工具", "subagent": "子代理", "context": "上下文",
}
#: 种类图标色（摘要文本 reasoning 用暗灰，其余亮白）
_KIND_FG = {
    "system": 110, "user": 39, "reasoning": 242, "content": 45, "tool": 214,
    "subagent": 75, "context": 110,
}
#: 状态图标与色（tool/subagent：● 运行中 / ✔ 完成 / ✖ 失败）
_STATUS_ICON = {"running": "\u25cf", "done": "\u2714", "fail": "\u2716", "error": "\u2716"}
_STATUS_FG = {"running": 208, "done": 41, "fail": 196, "error": 196}

#: 台账可见行数 = 终端高度 - 保留行（**全屏模式**：仅轨迹头 1 行——trace_open
#:   时消息区/顶部标题栏/状态栏/输入区全部不渲染，「打开时其他 TUI 不显示，
#:   只显示轨迹界面」，台账/检查器占满整个终端）
_VIEWPORT_RESERVED = 1
#: 检查器内容行预算下限（标题 + 元信息 + 省略提示占用后至少保留的行数）
_INSPECTOR_MIN_CONTENT = 4


def _viewport_rows() -> int:
    """台账可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, int(h) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _kind_fg(kind: str) -> int:
    return _KIND_FG.get(kind, 242)


def _status_fg(status: str) -> int:
    return _STATUS_FG.get(status, 242)


def _ledger_row_runs(rec, sel: bool, left_w: int) -> list:
    """台账行 runs（选中行整行背景高亮 + ▶ 标记；耗时右对齐；宽截断）。

    Args:
        rec: TraceRecord。
        sel: 是否选中。
        left_w: 左栏宽（>0 时截断；<=0 不截断防御）。
    """
    runs: list = []
    # 选择标记（2 列）
    if sel:
        runs.append(StyledRun("\u25b6 ", _S_SEL_MARK))
    else:
        runs.append(StyledRun("  ", None))
    runs.append(StyledRun(f"#{rec.index:>2} ", _S_INDEX))
    kind = getattr(rec, "kind", "context")
    icon = _KIND_ICON.get(kind, "\u00b7")
    runs.append(StyledRun(f"{icon} ", Style(fg=_kind_fg(kind))))
    # 状态图标（tool/subagent 才携带；running 呼吸色由 time_glow 负担过重，
    # 静态色——台账行不每帧重建）
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _STATUS_ICON.get(status, "\u00b7")
        runs.append(StyledRun(f"{sicon} ", Style(fg=_status_fg(status))))
    summary = getattr(rec, "summary", "") or "(空)"
    runs.append(StyledRun(summary, _S_DIM if kind == "reasoning" else _S_TEXT))
    # ★ 2026-08-19（工具调用+返回合并一条）：tool 记录在台账行追加返回首行
    #   预览（``· 返回…``，暗灰）——调用与返回同一条记录可见
    result = getattr(rec, "result", "") or ""
    if result and left_w > 0:
        budget = max(8, left_w // 3)
        prev_runs = truncate_runs([StyledRun(result, _S_DIM)], budget)
        if prev_runs:
            runs.append(StyledRun(" \u00b7 ", _S_HINT))
            runs.extend(prev_runs)
    # 耗时右对齐（尾列）
    t = ""
    ts = getattr(rec, "time_seconds", None)
    if ts is not None:
        t = format_duration(ts)
    if t and left_w > 0:
        used = sum(getattr(r, "width", 1) for r in runs)
        pad = left_w - used - len(t) - 1
        if pad > 0:
            runs.append(StyledRun(" " * pad, None))
        runs.append(StyledRun(t, _S_TIME))
    if sel:
        runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _sep_row_runs(n: int, left_w: int) -> list:
    """轮次分隔行 runs（``── 轮次 N ──``，深灰）。"""
    runs = [StyledRun(f"\u2500\u2500 轮次 {n} \u2500\u2500", _S_SEP_ROW)]
    return truncate_runs(runs, left_w) if left_w > 0 else runs


def _detail_lines_of(rec) -> list:
    """选中记录详情行（惰性提取：subagent 记录自带 lines；块记录经
    ``block_detail_lines`` 按需提取）。"""
    if rec is None:
        return []
    lines = getattr(rec, "lines", None) or []
    if lines:
        return lines
    block = getattr(rec, "source_block", None)
    if block is None:
        return []
    return block_detail_lines(block)


def _detail_deps(rec) -> tuple:
    """详情行 use_memo 依赖（块记录：行列表身份 + 行数；subagent：lines 身份）。"""
    if rec is None:
        return (None,)
    lines = getattr(rec, "lines", None) or []
    if lines:
        return (id(lines), getattr(rec, "index", 0))
    block = getattr(rec, "source_block", None)
    if block is None:
        return (None,)
    return (id(getattr(block, "lines", None)), len(getattr(block, "lines", None) or []))


def _inspector_children(rec, right_w: int, vh: int) -> list:
    """检查器子元素（标题 + 元信息 + 内容行；内容按栏宽换行 + 视口截断）。

    每行 TEXT 带**唯一 key**（``tinsp-*``）——修复 fiber 共享环（2026-08-19）：
    同层多个无 key TEXT 被调和器按派生 key（``host:text``）匹配到同一 fiber →
    同一 fiber 挂到多个位置（sibling 链环）→ ``find_input_fiber`` 全树 DFS
    无限循环（渲染线程卡死）。key 唯一后调和按 key 1:1 复用。

    Args:
        rec: 选中 TraceRecord（None = 空台账）。
        right_w: 右栏宽。
        vh: 视口行数预算（内容行数上限）。
    """
    if rec is None:
        return [h(TEXT, {
            "children": "无轨迹记录", "style": _S_HINT, "height": 1,
            "key": "tinsp-empty",
        })]
    children: list = []
    kind = getattr(rec, "kind", "context")
    title = f"#{getattr(rec, 'index', 0)} {_KIND_NAME.get(kind, kind)}"
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _STATUS_ICON.get(status, "\u00b7")
        title = f"{title} {sicon} {status}"
    children.append(h(TEXT, {
        "children": title, "style": _S_TITLE, "height": 1, "key": "tinsp-title",
    }))
    # 元信息（耗时 / token）
    meta: list = []
    ts = getattr(rec, "time_seconds", None)
    if ts is not None:
        meta.append(f"耗时 {format_duration(ts)}")
    tokens = getattr(rec, "tokens", None) or {}
    if tokens:
        meta.append(f"输入 {format_tokens(int(tokens.get('input', 0) or 0))}")
        meta.append(f"输出 {format_tokens(int(tokens.get('output', 0) or 0))}")
    if meta:
        children.append(h(TEXT, {
            "children": " · ".join(meta), "style": _S_DIM, "height": 1,
            "key": "tinsp-meta",
        }))
    # 内容行（按栏宽换行；视口行数截断 + 省略提示）
    budget = max(_INSPECTOR_MIN_CONTENT, vh - 2 - (1 if meta else 0))
    shown = 0
    truncated = False
    lines = getattr(rec, "_detail_lines", None)
    if lines is None:
        # 直接调用（测试/外部使用）未挂载惰性详情时回退记录内联 lines
        lines = getattr(rec, "lines", None) or []
    if right_w <= 0:
        right_w = 40  # 无栏宽上下文防御（_wrap_by_width max_width<=0 返回空）
    for line in lines:
        if not isinstance(line, str):
            line = str(line)
        for seg in _wrap_by_width(line, right_w):
            if shown >= budget:
                truncated = True
                break
            children.append(h(TEXT, {
                "children": seg if seg else " ",
                "style": _S_DIM if kind == "reasoning" else _S_TEXT,
                "height": 1,
                "key": f"tinsp-{shown}",
            }))
            shown += 1
        if truncated:
            break
    if truncated:
        children.append(h(TEXT, {
            "children": f"\u2026 后 {len(lines) - shown + 1} 行省略",
            "style": _S_HINT,
            "height": 1,
            "key": "tinsp-omitted",
        }))
    if not lines:
        children.append(h(TEXT, {
            "children": "(无内容)", "style": _S_HINT, "height": 1,
            "key": "tinsp-none",
        }))
    return children


def _block_fingerprint(model) -> tuple:
    """块指纹（use_memo deps）：块种类/行数/关闭态/工具状态——行数变化
    （流式追加）或状态变化才重建记录列表；时间基元素不入指纹（台账静态
    色，不随动画重建）。"""
    fp = []
    for b in getattr(model, "blocks", None) or []:
        extra = getattr(b, "extra", None) or {}
        fp.append((
            getattr(b, "kind", ""),
            len(getattr(b, "lines", None) or []),
            bool(getattr(b, "closed", False)),
            extra.get("tool_status", ""),
        ))
    return tuple(fp)


def _subagent_fingerprint() -> tuple:
    """subagent 槽位指纹（use_memo deps）：顺序 + 状态 + 工具历史长度。

    控制器不存在/未装配时返回空元组（零成本——无 subagent 记录）。
    """
    try:
        from src.tui.subagent import SubAgentPanelController
        controller = SubAgentPanelController.get_default()
        store = getattr(controller, "_store", None)
        if store is None:
            return ()
        with store._state_lock:
            order = list(getattr(store, "_order", None) or [])
            agents = getattr(store, "_agents", None) or {}
            fp = tuple(
                (label, getattr(agents.get(label), "status", ""),
                 len(getattr(agents.get(label), "tool_history", None) or []))
                for label in order
            )
        return fp
    except Exception:
        return ()


def _records_deps(model) -> tuple:
    """记录构建 use_memo 依赖（数据源自适应指纹）。

    消息源模式（装配注入 agent.messages）：``_messages_fingerprint``——流式
    增长/追加/编辑触发重建；块模式：块指纹 + subagent 指纹（内容变化才
    重建）。时间基元素不入指纹（台账静态色，不随动画重建）。
    """
    if getattr(model, "message_source", None) is not None:
        from src.tui.app.trace import _messages_fingerprint
        return (_messages_fingerprint(model),)
    return (_block_fingerprint(model), _subagent_fingerprint())


def _row_of_record(rows: list, sel: int, records: list) -> int:
    """记录 sel 在台账行（rows）中的下标（分隔行不计入选择）。"""
    if not (0 <= sel < len(records)):
        return 0
    target = records[sel]
    for i, row in enumerate(rows):
        if row is target:
            return i
    return 0


def TraceView(props) -> object:
    """轨迹视图组件（App 消息区替换渲染；Ctrl+H 开关）。

    Props:
        model: AppModel 实例（blocks/subagent_lines/trace_open/trace_selected）。
        width: 终端宽度（左右栏宽分配）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0

    # ── 数据（use_memo 指纹缓存：消息源/块/subagent 内容变化才重建） ──
    records, rows = use_memo(
        lambda: build_trace_records(model),
        _records_deps(model),
    )
    total = len(records)

    # ── 选中解析（-1 = 跟随尾部：渲染期解析为最新记录，流式追加自动跟进） ──
    sel = getattr(model, "trace_selected", -1)
    if total == 0:
        sel = 0
    elif sel == -1 or sel >= total:
        sel = total - 1

    # ── 视口 / 栏宽 ──
    vh = _viewport_rows()
    if width > 0:
        left_w = max(24, int(width * 0.45))
        if width - left_w - 1 < 20:
            left_w = max(20, width - 21)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 40, 40

    # ── 选中记录详情（惰性提取 + use_memo：行列表身份/行数变化才重建） ──
    rec = records[sel] if 0 <= sel < total else None
    detail_lines = use_memo(
        lambda: _detail_lines_of(rec),
        _detail_deps(rec),
    )
    if rec is not None:
        rec._detail_lines = detail_lines

    # ── 台账可见窗口（选中行置于窗口首行之下 1 行上下文；尾部跟随自然滑动） ──
    row_count = len(rows)
    sel_row = _row_of_record(rows, sel, records)
    offset = (
        max(0, min(sel_row - 1, row_count - vh)) if row_count > vh else 0
    )

    # ── 输入（trace_open 期间激活；Enter/其余按键放行——非模态） ──
    def _handle(event) -> bool:
        if not getattr(model, "trace_open", False):
            return False
        # 关闭类按键（Esc / Ctrl+H）——优先于导航
        if event.kind == "escape":
            model.trace_open = False
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            model.trace_open = False
            return True
        if total == 0:
            return False
        cur = getattr(model, "trace_selected", -1)
        if cur == -1 or cur >= total:
            cur = total - 1
        changed = False
        if event.kind == "arrow_up":
            if cur > 0:
                cur -= 1
                changed = True
        elif event.kind == "arrow_down":
            if cur < total - 1:
                cur += 1
                changed = True
        elif event.kind == "page_up":
            n = max(0, cur - vh)
            changed = n != cur
            cur = n
        elif event.kind == "page_down":
            n = min(total - 1, cur + vh)
            changed = n != cur
            cur = n
        elif event.kind == "home":
            changed = cur != 0
            cur = 0
        elif event.kind == "end":
            changed = cur != total - 1
            cur = total - 1
        elif event.kind == "char" and getattr(event, "char", "") in ("g", "G"):
            n = 0 if event.char == "g" else total - 1
            changed = cur != n
            cur = n
        else:
            return False  # Enter/字符/其余按键放行（提交消息/打字）
        if changed:
            model.trace_selected = cur  # 退出尾部跟随（写入具体索引）
        return True

    use_input(_handle, bool(getattr(model, "trace_open", False)))

    # ── 渲染 ──
    # 头部（静态色——轨迹视图为浏览界面，不呼吸，diff 零输出）
    turn_count = sum(1 for r in rows if r is None)
    header_runs = [
        StyledRun("\u258d轨迹 Trace", _S_TITLE),
        StyledRun(f" · {total} 条 · {turn_count} 轮", _S_HINT),
        StyledRun("  \u2191\u2193 选择 · PgUp/PgDn 翻页 · g/G 首末 · Enter 提交 · Esc/Ctrl+H 关闭", _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)

    # 左栏（台账）
    left_rows: list = []
    for i in range(offset, min(row_count, offset + vh)):
        row = rows[i]
        if row is None:
            n = sum(1 for r in rows[:i] if r is None) + 1  # 第 n 个分隔 = 轮次 n
            left_rows.append(h(TEXT, {
                "key": f"tsep-{i}", "styled": _sep_row_runs(n, left_w), "height": 1,
            }))
        else:
            is_sel = row is rec
            left_rows.append(h(TEXT, {
                "key": f"trow-{i}",
                "styled": _ledger_row_runs(row, is_sel, left_w),
                "height": 1,
            }))
    # 右栏（检查器）
    right_rows = _inspector_children(rec, right_w, vh)

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1}),
        h(Row, None, [
            h(Column, {"width": left_w}, left_rows),
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_rows),
        ]),
    ])


__all__ = ["TraceView", "_ledger_row_runs", "_inspector_children", "_viewport_rows"]
