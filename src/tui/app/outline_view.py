"""outline_view — OutlineView 消息大纲 / 导航视图（模态全屏视图，2026-10）。

``/outline`` 命令打开：App 在 ``model.fullscreen == "outline"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图以大纲形式列出
会话中的全部消息节点，支持快速跳转定位。

布局（React Ink 左右布局）：
  - 左栏「消息大纲」：``#序号 角色 摘要``，↑↓/jk 选择（ListView 标准控件）；
  - 右栏「消息全文」：选中节点的完整消息内容（按栏宽换行）。

键盘：
  - ↑↓/jk 选择 · Enter 跳转（回显定位并关闭）· l/h 进入/返回详情 ·
    y 复制消息全文 · ``/`` 搜索 · ``n``/``N``/``p`` 定位 · ``?`` 帮助 ·
    Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.outline_view.entries``（``ctx.messages`` 摘要）；
跳转经 ``jump_seq`` / ``jump_target`` 回传给命令线程。
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
    jump_match,
    pane_divider,
    run_search,
    split_panes,
    status_runs,
    viewport_rows,
)

__all__ = ["OutlineView", "_outline_search_text", "_detail_rows"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_INDEX = Style(fg=75)
_S_ROLE_USER = Style(fg=214, bold=True)
_S_ROLE_ASST = Style(fg=110, bold=True)
_S_ROLE_TOOL = Style(fg=68)
_S_ROLE_OTHER = Style(fg=242)
_S_SUMMARY = Style(fg=252)
_S_BODY = Style(fg=252)
_S_TOOLS = Style(fg=110)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_OK = Style(fg=40, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择消息"},
    {"group": "浏览", "keys": "Enter", "desc": "跳转定位"},
    {"group": "浏览", "keys": "l/h", "desc": "进入/返回详情"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末消息"},
    {"group": "操作", "keys": "y", "desc": "复制消息全文"},
    {"group": "搜索", "keys": "/", "desc": "搜索摘要"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 角色标签（大纲显示）。
_ROLE_LABEL = {"user": "用户", "assistant": "助手", "tool": "工具", "system": "系统"}


def _role_style(role: str) -> Style:
    return {
        "user": _S_ROLE_USER,
        "assistant": _S_ROLE_ASST,
        "tool": _S_ROLE_TOOL,
    }.get(role, _S_ROLE_OTHER)


def _outline_search_text(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    return f"{entry.get('summary', '')} {entry.get('role', '')} {' '.join(entry.get('tools') or [])}"


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中节点 → 消息全文行。"""
    if entry is None:
        return [[StyledRun("(无选中消息)", _S_HINT)]]
    rows: list = [
        [
            StyledRun(f"\u25b8 消息 #{entry.get('index', '')}  ", _S_TITLE),
            StyledRun(str(_ROLE_LABEL.get(entry.get("role", ""), entry.get("role", ""))), _role_style(entry.get("role", ""))),
        ],
        [StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)],
    ]
    tools = entry.get("tools") or []
    if tools:
        rows.append([StyledRun("工具: " + ", ".join(str(t) for t in tools), _S_TOOLS)])
    text = str(entry.get("text", "") or "")
    if not text:
        rows.append([StyledRun("(空消息)", _S_HINT)])
        return rows
    for seg in text.split("\n"):
        runs = [StyledRun(seg, _S_BODY if seg else None)]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    return rows


def OutlineView(props) -> object:
    """消息大纲 / 导航视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    ov = getattr(model, "outline_view", None)
    visible = bool(ov is not None and ov.visible and not ov.done)
    entries = list(getattr(ov, "entries", None) or []) if ov is not None else []
    help_open = bool(getattr(ov, "help_open", False)) if ov is not None else False
    search_mode = bool(getattr(ov, "search_mode", False)) if ov is not None else False
    pattern = (getattr(ov, "search_pattern", "") or "") if ov is not None else ""
    filter_on = bool(getattr(ov, "search_filter", False)) if ov is not None else False
    matches = list(getattr(ov, "search_matches", None) or []) if ov is not None else []
    status_message = (getattr(ov, "status_message", "") or "") if ov is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(ov, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if ov is not None and sel != getattr(ov, "selected", None):
        ov.selected = sel
    entry = items[sel] if total else None

    pane = getattr(ov, "pane", "list") if ov is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(36, 46))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_ROLE_ASST,
            desc_style=_S_BODY, sep_style=_S_SEP,
        )
    else:
        content_rows = use_memo(lambda: _detail_rows(entry, right_w), (id(entry), right_w))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(ov, "cursor", 0) or 0, getattr(ov, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if ov is not None:
        ov.cursor = cursor
        ov.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(ov, "cursor", 0) or 0,
        lambda v: setattr(ov, "cursor", v),
        lambda: getattr(ov, "scroll", 0) or 0,
        lambda v: setattr(ov, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if ov is None:
            return
        ov.selected = row
        ov.cursor = 0
        ov.scroll = 0

    def _jump(row: int) -> None:
        if not items or row < 0 or row >= total:
            return
        ov.selected = row
        ov.jump_target = items[row].get("index")
        ov.jump_seq += 1
        ov.status_message = "跳转中…"

    def _handle(event) -> bool:
        if not visible or ov is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, ov, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            ov.search_mode = False
            ov.search_query = ""
            return True
        if verdict == "run":
            run_search(ov, entries, getattr(ov, "search_query", "") or "",
                       _outline_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(ov, "help_open", False):
                ov.help_open = False
                ov.pane = "list"
                ov.cursor = 0
                ov.scroll = 0
                return True
            ov.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                ov.help_open = not bool(ov.help_open)
                ov.pane = "detail" if ov.help_open else "list"
                ov.cursor = 0
                ov.scroll = 0
                return True
            if ch == "/":
                ov.search_mode = True
                ov.search_query = getattr(ov, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(ov, "search_pattern", "") or ""):
                jump_match(ov, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    ov.search_filter = not bool(ov.search_filter)
                    ov.status_message = "过滤开启" if ov.search_filter else "过滤关闭"
                else:
                    ov.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "y" and entry is not None:
                from src.tui._screen import set_clipboard

                text = str(entry.get("text", "") or "")
                if text and set_clipboard(text):
                    ov.status_message = "已复制消息全文"
                else:
                    ov.status_message = "复制失败"

        if pane == "list":
            if event.kind == "char" and ch == "l":
                ov.pane = "detail"
                ov.help_open = False
                return True
            if event.kind == "enter" and total:
                _jump(sel)
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(ov, "help_open", False):
                ov.help_open = False
                return True
            ov.pane = "list"
            return True
        if handle_nav(event, _pane_state, total_content, max(1, vh)):
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(ov, "search_idx", -1) < len(matches):
        cur_match_entry = matches[ov.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        role = str(e.get("role", ""))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"#{e.get('index', ''):>3} ", _S_INDEX),
            StyledRun(_ROLE_LABEL.get(role, role).ljust(4), _role_style(role)),
            StyledRun(str(e.get("summary", "")), _S_SUMMARY),
        ]
        tools = e.get("tools") or []
        if tools:
            runs.append(StyledRun(f"  ⚙{len(tools)}", _S_TOOLS))
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if orig_idx == cur_match_entry:
            bg = _S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = _S_MATCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"ol-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _select_row(int(idx)),
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
            "styled": runs, "height": 1, "key": f"ol-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · Enter 跳转 · y 复制 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 跳转 · l 详情 · / 搜索 · ? 帮助 · Esc 关闭"
    segs = [f" · {total} 个节点"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f9ed 消息大纲", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "ol-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(ov, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(parts, style=_S_STATUS, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "ol-status"}))
    if search_mode:
        q = getattr(ov, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "ol-search",
        }))
    return h(Column, None, children)
