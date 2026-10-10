"""search_view — SearchView 对话内全文搜索视图（模态全屏视图，2026-10）。

``/search`` 命令打开：App 在 ``model.fullscreen == "search"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图在整条会话历史中
全文搜索关键词并跳转定位。

布局（React Ink 左右布局）：
  - 左栏「结果列表」：角色 + 命中片段，↑↓/jk 选择（ListView 标准控件）；
  - 右栏「消息全文」：选中结果的完整消息内容（按栏宽换行）。

键盘：
  - ``/`` 输入关键词 · Enter 执行 · Esc 取消（输入态）；
  - ↑↓/jk 选择结果 · Enter 跳转（回显定位并关闭）· y 复制消息全文；
  - ``n``/``N``/``p`` 在结果间环绕 · ``?`` 帮助面板 · Esc/Ctrl+H 关闭。

数据源：命令线程注入 ``model.search_view.messages``（会话消息摘要）；搜索结果
由组件按关键词计算；跳转经 ``jump_seq`` / ``jump_target`` 回传给命令线程。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_memo
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from ._view_common import (
    SEARCH_QUERY_MAX,
    build_header_runs,
    char_of,
    handle_search_input,
    pane_divider,
    split_panes,
    status_runs,
    viewport_rows,
)

__all__ = ["SearchView", "_detail_rows", "search_messages"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_ROLE = Style(fg=110, bold=True)
_S_SNIP = Style(fg=252)
_S_BODY = Style(fg=252)
_S_MATCH = Style(fg=0, bg=221)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_STATUS = Style(fg=221)
_S_OK = Style(fg=40, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "搜索", "keys": "/", "desc": "输入关键词"},
    {"group": "搜索", "keys": "Enter", "desc": "执行搜索 / 跳转"},
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择结果"},
    {"group": "浏览", "keys": "l/h", "desc": "进入/返回详情"},
    {"group": "浏览", "keys": "n/N/p", "desc": "下一/上一结果"},
    {"group": "操作", "keys": "y", "desc": "复制消息全文"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 每条消息片段截断长度。
_SNIPPET_LEN = 100


def search_messages(messages: list, query: str) -> list:
    """在消息列表中搜索关键词，返回结果列表。

    每条结果 ``{"msg_index", "role", "snippet"}``；``msg_index`` 为消息在
    ``messages`` 中的下标。
    """
    q = str(query or "").strip().lower()
    if not q:
        return []
    out: list = []
    for i, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("text", "") or "")
        pos = text.lower().find(q)
        if pos < 0:
            continue
        start = max(0, pos - _SNIPPET_LEN // 2)
        snippet = text[start:start + _SNIPPET_LEN].replace("\n", " ")
        out.append({
            "msg_index": int(msg.get("index", i)),
            "role": str(msg.get("role", "")),
            "snippet": snippet,
        })
    return out


def _highlight(text: str, query: str) -> list:
    """在文本中高亮关键词（大小写不敏感，保序拆分）。"""
    q = str(query or "").strip()
    if not q:
        return [StyledRun(text, _S_BODY)]
    low = text.lower()
    low_q = q.lower()
    out: list = []
    pos = 0
    while True:
        idx = low.find(low_q, pos)
        if idx < 0:
            break
        if idx > pos:
            out.append(StyledRun(text[pos:idx], _S_BODY))
        out.append(StyledRun(text[idx:idx + len(q)], _S_MATCH))
        pos = idx + len(q)
    if pos < len(text):
        out.append(StyledRun(text[pos:], _S_BODY))
    return out or [StyledRun(text, _S_BODY)]


def _detail_rows(results: list, messages: list, sel: int, query: str, right_w: int) -> list:
    """选中结果 → 消息全文行。"""
    if not results or sel < 0 or sel >= len(results):
        return [[StyledRun("(无选中结果)", _S_HINT)]]
    res = results[sel]
    msg_index = res.get("msg_index")
    text = ""
    role = res.get("role", "")
    for m in messages or []:
        if int(m.get("index", -1)) == int(msg_index):
            text = str(m.get("text", "") or "")
            role = str(m.get("role", role))
            break
    rows: list = [
        [StyledRun(f"\u25b8 消息 #{msg_index}  ", _S_TITLE), StyledRun(role, _S_ROLE)],
        [StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)],
    ]
    if not text:
        rows.append([StyledRun("(空消息)", _S_HINT)])
        return rows
    for seg in text.split("\n"):
        runs = _highlight(seg, query) if seg else [StyledRun(" ", None)]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    return rows


def SearchView(props) -> object:
    """对话内全文搜索视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    sv = getattr(model, "search_view", None)
    visible = bool(sv is not None and sv.visible and not sv.done)
    messages = list(getattr(sv, "messages", None) or []) if sv is not None else []
    results = list(getattr(sv, "results", None) or []) if sv is not None else []
    pattern = (getattr(sv, "search_pattern", "") or "") if sv is not None else ""
    search_mode = bool(getattr(sv, "search_mode", False)) if sv is not None else False
    help_open = bool(getattr(sv, "help_open", False)) if sv is not None else False
    status_message = (getattr(sv, "status_message", "") or "") if sv is not None else ""

    total = len(results)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(sv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if sv is not None and sel != getattr(sv, "selected", None):
        sv.selected = sel

    pane = getattr(sv, "pane", "list") if sv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(38, 46))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → ``HookStateError``（视图渲染异常）。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_ROLE,
                desc_style=_S_BODY, sep_style=_S_SEP,
            )
        return _detail_rows(results, messages, sel, pattern, right_w)

    content_rows = use_memo(_content_rows, (
        help_open, (id(results), sel, pattern, right_w),
    ))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(sv, "cursor", 0) or 0, getattr(sv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if sv is not None:
        sv.cursor = cursor
        sv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(sv, "cursor", 0) or 0,
        lambda v: setattr(sv, "cursor", v),
        lambda: getattr(sv, "scroll", 0) or 0,
        lambda v: setattr(sv, "scroll", v),
    )

    def _run_search() -> None:
        q = (getattr(sv, "search_query", "") or "").strip()
        sv.search_mode = False
        sv.search_pattern = q
        found = search_messages(messages, q)
        sv.results = found
        sv.selected = 0
        sv.cursor = 0
        sv.scroll = 0
        sv.status_message = f"{len(found)} 条匹配" if found else (f"无匹配：{q}" if q else "")

    def _jump(row: int) -> None:
        if not results or row < 0 or row >= len(results):
            return
        sv.selected = row
        sv.jump_target = results[row].get("msg_index")
        sv.jump_seq += 1
        sv.status_message = "跳转中…"

    def _handle(event) -> bool:
        if not visible or sv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, sv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            sv.search_mode = False
            sv.search_query = ""
            return True
        if verdict == "run":
            _run_search()
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(sv, "help_open", False):
                sv.help_open = False
                sv.pane = "list"
                sv.cursor = 0
                sv.scroll = 0
                return True
            sv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                sv.help_open = not bool(sv.help_open)
                sv.pane = "detail" if sv.help_open else "list"
                sv.cursor = 0
                sv.scroll = 0
                return True
            if ch == "/":
                sv.search_mode = True
                sv.search_query = getattr(sv, "search_pattern", "") or ""
                return True
            if ch == "y" and total:
                res = results[sel]
                text = ""
                for m in messages:
                    if int(m.get("index", -1)) == int(res.get("msg_index", -1)):
                        text = str(m.get("text", "") or "")
                        break
                from src.tui._screen import set_clipboard

                if text and set_clipboard(text):
                    sv.status_message = "已复制消息全文"
                else:
                    sv.status_message = "复制失败"

        if pane == "list":
            if event.kind == "char" and ch == "l":
                sv.pane = "detail"
                sv.help_open = False
                return True
            if (event.kind == "enter" or (event.kind == "char" and ch in ("n", "N", "p"))) and total:
                if event.kind == "enter":
                    _jump(sel)
                else:
                    step = 1 if ch == "n" else -1
                    _jump((sel + step) % total)
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(sv, "help_open", False):
                sv.help_open = False
                return True
            sv.pane = "list"
            return True
        if handle_nav(event, _pane_state, total_content, max(1, vh)):
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    def _render_left(item, idx, is_sel):
        res = item if isinstance(item, dict) else {}
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"#{res.get('msg_index', '')} ", _S_ROLE),
            StyledRun(str(res.get("role", "")), _S_ROLE),
            StyledRun("  " + str(res.get("snippet", "")), _S_SNIP),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"se-{idx}"})

    ledger = h(ListView, {
        "items": results,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: setattr(sv, "selected", int(idx)),
        "focus": visible and pane == "list" and not search_mode,
    })

    right_children: list = []
    window = content_rows[scroll:scroll + max(1, vh)]
    for i, runs in enumerate(window):
        abs_idx = scroll + i
        is_cur = pane == "detail" and abs_idx == cursor
        if is_cur:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG)) for r in runs]
        right_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"se-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入关键词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · / 搜索 · y 复制 · Enter 跳转 · Esc 关闭"
    else:
        hint = "  / 搜索 · ↑↓/jk 选择 · Enter 跳转 · y 复制 · ? 帮助 · Esc 关闭"
    segs = [f" · {len(messages)} 条消息", f" · {total} 条匹配" if pattern else ""]
    header_runs = build_header_runs(
        "\u258d\U0001f50d 对话内搜索", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "se-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    sruns = status_runs([], style=_S_STATUS, message=status_message,
                        message_style=_S_WARN if "无匹配" in status_message else _S_OK,
                        width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "se-status"}))
    if search_mode:
        q = getattr(sv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "se-search",
        }))
    return h(Column, None, children)
