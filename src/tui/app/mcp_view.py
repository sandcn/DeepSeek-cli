"""mcp_view — McpView MCP 服务器管理视图（模态全屏视图，2026-10）。

``/mcp`` 命令打开：App 在 ``model.fullscreen == "mcp"`` 时经全屏视图注册表
**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图展示 MCP 服务器配置与
连接状态（``src.mcp.config`` / ``src.mcp.manager``）。

布局（React Ink 左右布局）：
  - 左栏「服务器列表」：连接状态点 + 名称 + transport；
  - 右栏「详情」：命令/URL / 描述 / 允许的 agent / 工具清单 / 错误。

键盘：
  - ↑↓/jk 选择 · l/Enter 详情 · h 返回；
  - ``r`` 刷新状态 · ``c`` 重连全部服务器 · ``/`` 搜索 · ``?`` 帮助 ·
    Esc/Ctrl+H 关闭。
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

__all__ = ["McpView", "_mcp_search_text", "_detail_rows"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_TAG = Style(fg=110)
_S_FIELD = Style(fg=75)
_S_VALUE = Style(fg=252)
_S_ON = Style(fg=40, bold=True)
_S_OFF = Style(fg=242)
_S_ERR = Style(fg=196, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择服务器"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看详情"},
    {"group": "浏览", "keys": "h", "desc": "返回列表"},
    {"group": "操作", "keys": "r", "desc": "刷新状态"},
    {"group": "操作", "keys": "c", "desc": "重连全部服务器"},
    {"group": "搜索", "keys": "/", "desc": "搜索服务器"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]


def _mcp_search_text(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    return f"{entry.get('name', '')} {entry.get('transport', '')} {entry.get('command', '')}"


def _status_style(entry: dict) -> Style:
    if entry.get("error"):
        return _S_ERR
    if entry.get("connected"):
        return _S_ON
    if entry.get("enabled"):
        return _S_WARN
    return _S_OFF


def _status_text(entry: dict) -> str:
    if entry.get("error"):
        return "错误"
    if entry.get("connected"):
        return "已连接"
    if entry.get("enabled"):
        return "未连接"
    return "已禁用"


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中服务器 → 详情行。"""
    if entry is None:
        return [[StyledRun("无选中服务器", _S_HINT)]]
    rows: list = []
    header = [StyledRun(str(entry.get("name", "")), _S_TITLE)]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    fields = [
        ("状态", _status_text(entry)),
        ("传输", entry.get("transport", "")),
        ("启用", "是" if entry.get("enabled") else "否"),
        ("命令/URL", entry.get("command", "") or "-"),
        ("说明", entry.get("description", "") or "-"),
        ("允许 Agent", ", ".join(entry.get("agents") or []) or "-"),
    ]
    for label, value in fields:
        runs = [StyledRun(f"{label}: ", _S_FIELD), StyledRun(str(value), _S_VALUE)]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    tools = entry.get("tools") or []
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    rows.append([StyledRun(f"工具（{len(tools)}）", _S_FIELD)])
    if not tools:
        rows.append([StyledRun("  (无已连接工具)", _S_HINT)])
    for tool in tools:
        runs = [StyledRun(f"  \u2022 {tool}", _S_VALUE)]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    err = entry.get("error")
    if err:
        rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
        for seg in str(err).split("\n"):
            runs = [StyledRun(seg, _S_ERR if seg else None)]
            for line in wrap_runs_by_width(runs, max(1, right_w)):
                rows.append(list(line.runs))
    return rows


def McpView(props) -> object:
    """MCP 服务器管理视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    mv = getattr(model, "mcp_view", None)
    visible = bool(mv is not None and mv.visible and not mv.done)
    entries = list(getattr(mv, "entries", None) or []) if mv is not None else []
    help_open = bool(getattr(mv, "help_open", False)) if mv is not None else False
    search_mode = bool(getattr(mv, "search_mode", False)) if mv is not None else False
    pattern = (getattr(mv, "search_pattern", "") or "") if mv is not None else ""
    filter_on = bool(getattr(mv, "search_filter", False)) if mv is not None else False
    matches = list(getattr(mv, "search_matches", None) or []) if mv is not None else []
    status_message = (getattr(mv, "status_message", "") or "") if mv is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(mv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if mv is not None and sel != getattr(mv, "selected", None):
        mv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(mv, "pane", "list") if mv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(34, 48))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_TAG,
            desc_style=_S_VALUE, sep_style=_S_SEP,
        )
    else:
        content_rows = use_memo(lambda: _detail_rows(entry, right_w), (id(entry), right_w))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(mv, "cursor", 0) or 0, getattr(mv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if mv is not None:
        mv.cursor = cursor
        mv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(mv, "cursor", 0) or 0,
        lambda v: setattr(mv, "cursor", v),
        lambda: getattr(mv, "scroll", 0) or 0,
        lambda v: setattr(mv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if mv is None:
            return
        mv.selected = row
        mv.cursor = 0
        mv.scroll = 0

    def _handle(event) -> bool:
        if not visible or mv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, mv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            mv.search_mode = False
            mv.search_query = ""
            return True
        if verdict == "run":
            run_search(mv, entries, getattr(mv, "search_query", "") or "",
                       _mcp_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(mv, "help_open", False):
                mv.help_open = False
                mv.pane = "list"
                mv.cursor = 0
                mv.scroll = 0
                return True
            mv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                mv.help_open = not bool(mv.help_open)
                mv.pane = "detail" if mv.help_open else "list"
                mv.cursor = 0
                mv.scroll = 0
                return True
            if ch == "/":
                mv.search_mode = True
                mv.search_query = getattr(mv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(mv, "search_pattern", "") or ""):
                jump_match(mv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    mv.search_filter = not bool(mv.search_filter)
                    mv.status_message = "过滤开启" if mv.search_filter else "过滤关闭"
                else:
                    mv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "r":
                mv.refresh_seq += 1
                mv.status_message = "已刷新"
                return True
            if ch == "c":
                mv.applied_seq += 1
                mv.applied = {"action": "reconnect"}
                mv.status_message = "正在重连…"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                mv.pane = "detail"
                mv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(mv, "help_open", False):
                mv.help_open = False
                return True
            mv.pane = "list"
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
    if matched_set and 0 <= getattr(mv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[mv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        dot = "\u25cf" if e.get("connected") else "\u25cb"
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(dot + " ", _status_style(e)),
            StyledRun(str(e.get("name", "")), _S_NAME),
            StyledRun("  " + str(e.get("transport", "")), _S_TAG),
        ]
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
        return h(TEXT, {"styled": runs, "height": 1, "key": f"mv-{idx}"})

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
            "styled": runs, "height": 1, "key": f"mv-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · r 刷新 · c 重连 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 详情 · r 刷新 · c 重连 · / 搜索 · ? 帮助 · Esc"
    segs = [f" · {total} 个服务器"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f50c MCP 服务器", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "mv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(mv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=_S_STATUS, message=status_message,
        message_style=_S_WARN if "失败" in status_message else _S_ON,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "mv-status"}))
    if search_mode:
        q = getattr(mv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "mv-search",
        }))
    return h(Column, None, children)
