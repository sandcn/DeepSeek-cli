"""notify_view — NotifyView 通知 / 事件日志视图（模态全屏视图，2026-10）。

``/notify`` 命令打开：App 在 ``model.fullscreen == "notify"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图展示运行期事件
（已发送的桌面通知 / 通知消息 / 错误）——数据源 ``src.notifications.history``。

布局（React Ink 左右布局）：
  - 左栏「事件列表」：时间 + 类型 + 标题，↑↓/jk 选择（ListView 标准控件）；
  - 右栏「详情」：完整正文（按栏宽换行）+ 类型/级别/时间。

键盘：
  - ↑↓/jk 选择 · g/G 首末 · Enter/l 查看详情 · h 返回列表；
  - ``r`` 刷新（重读日志缓冲）· ``c`` 清空日志 · ``/`` 搜索 · ``n``/``N``/
    ``p`` 定位匹配 · ``f`` 过滤 · ``?`` 帮助面板 · Esc/Ctrl+H 关闭。
"""

from __future__ import annotations

import time

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

__all__ = ["NotifyView", "_notify_search_text", "_detail_rows", "format_time"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_TIME = Style(fg=110)
_S_BODY = Style(fg=252)
_S_ERR = Style(fg=196, bold=True)
_S_WARN = Style(fg=214)
_S_NOTIFY = Style(fg=45)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_OK = Style(fg=40, bold=True)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择事件"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看详情"},
    {"group": "浏览", "keys": "h", "desc": "返回列表"},
    {"group": "操作", "keys": "r", "desc": "刷新日志"},
    {"group": "操作", "keys": "c", "desc": "清空日志"},
    {"group": "搜索", "keys": "/", "desc": "搜索日志"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 类型标签与图标。
_KIND_LABEL = {
    "notify": ("\U0001f514", "桌面通知"),
    "notice": ("\u25b8", "通知"),
    "error": ("\u2716", "错误"),
}


def format_time(ts) -> str:
    """时间戳 → ``HH:MM:SS``（异常回退空串）。"""
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return ""


def _kind_icon(entry: dict) -> str:
    return _KIND_LABEL.get(str(entry.get("kind", "")), ("\u2022", "事件"))[0]


def _kind_label(entry: dict) -> str:
    return _KIND_LABEL.get(str(entry.get("kind", "")), ("\u2022", "事件"))[1]


def _level_style(entry: dict) -> Style:
    if str(entry.get("level", "")) == "error" or str(entry.get("kind", "")) == "error":
        return _S_ERR
    if str(entry.get("kind", "")) == "notify":
        return _S_NOTIFY
    return _S_NAME


def _notify_search_text(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        str(entry.get("title", "")), str(entry.get("body", "")),
        str(entry.get("kind", "")),
    ])


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中事件 → 详情行。"""
    if entry is None:
        return [[StyledRun("无选中事件", _S_HINT)]]
    rows: list = []
    header = [StyledRun(f"{_kind_icon(entry)} {entry.get('title', '')}", _S_TITLE)]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    meta = [
        StyledRun(f"类型 {_kind_label(entry)}", _S_TIME),
        StyledRun(f"  级别 {entry.get('level', 'info')}", _S_TIME),
        StyledRun(f"  时间 {format_time(entry.get('time'))}", _S_TIME),
    ]
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])
    body = str(entry.get("body", "") or "")
    if not body:
        rows.append([StyledRun("(无正文)", _S_HINT)])
    for seg in body.split("\n"):
        runs = [StyledRun(seg, _S_BODY if seg else None)]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    return rows


def _refresh_entries() -> list:
    """重读日志缓冲（最新在前）。"""
    try:
        from src.notifications.history import entries as _entries

        return list(reversed(_entries()))
    except Exception:
        return []


def NotifyView(props) -> object:
    """通知 / 事件日志视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    nv = getattr(model, "notify_view", None)
    visible = bool(nv is not None and nv.visible and not nv.done)
    entries = list(getattr(nv, "entries", None) or []) if nv is not None else []
    help_open = bool(getattr(nv, "help_open", False)) if nv is not None else False
    search_mode = bool(getattr(nv, "search_mode", False)) if nv is not None else False
    pattern = (getattr(nv, "search_pattern", "") or "") if nv is not None else ""
    filter_on = bool(getattr(nv, "search_filter", False)) if nv is not None else False
    matches = list(getattr(nv, "search_matches", None) or []) if nv is not None else []
    status_message = (getattr(nv, "status_message", "") or "") if nv is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(nv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if nv is not None and sel != getattr(nv, "selected", None):
        nv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(nv, "pane", "list") if nv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(36, 46))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → ``HookStateError``（视图渲染异常）。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_TIME,
                desc_style=_S_NAME, sep_style=_S_SEP,
            )
        return _detail_rows(entry, right_w)

    content_rows = use_memo(_content_rows, (help_open, (id(entry), right_w)))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(nv, "cursor", 0) or 0, getattr(nv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if nv is not None:
        nv.cursor = cursor
        nv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(nv, "cursor", 0) or 0,
        lambda v: setattr(nv, "cursor", v),
        lambda: getattr(nv, "scroll", 0) or 0,
        lambda v: setattr(nv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if nv is None:
            return
        nv.selected = row
        nv.cursor = 0
        nv.scroll = 0

    def _handle(event) -> bool:
        if not visible or nv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, nv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            nv.search_mode = False
            nv.search_query = ""
            return True
        if verdict == "run":
            run_search(nv, entries, getattr(nv, "search_query", "") or "",
                       _notify_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(nv, "help_open", False):
                nv.help_open = False
                nv.pane = "list"
                nv.cursor = 0
                nv.scroll = 0
                return True
            nv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                nv.help_open = not bool(nv.help_open)
                nv.pane = "detail" if nv.help_open else "list"
                nv.cursor = 0
                nv.scroll = 0
                return True
            if ch == "/":
                nv.search_mode = True
                nv.search_query = getattr(nv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(nv, "search_pattern", "") or ""):
                jump_match(nv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    nv.search_filter = not bool(nv.search_filter)
                    nv.status_message = "过滤开启" if nv.search_filter else "过滤关闭"
                else:
                    nv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "r":
                nv.entries = _refresh_entries()
                nv.selected = 0
                nv.status_message = f"已刷新：{len(nv.entries)} 条"
                return True
            if ch == "c":
                try:
                    from src.notifications.history import clear as _clear

                    _clear()
                    nv.entries = []
                    nv.selected = 0
                    nv.status_message = "日志已清空"
                except Exception:
                    nv.status_message = "清空失败"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                nv.pane = "detail"
                nv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(nv, "help_open", False):
                nv.help_open = False
                return True
            nv.pane = "list"
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
    if matched_set and 0 <= getattr(nv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[nv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        icon = _kind_icon(e)
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"{format_time(e.get('time'))} ", _S_TIME),
            StyledRun(f"{icon} ", _level_style(e)),
            StyledRun(str(e.get("title", "")), _level_style(e)),
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
        return h(TEXT, {"styled": runs, "height": 1, "key": f"nv-{idx}"})

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
            "styled": runs, "height": 1, "key": f"nv-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · r 刷新 · c 清空 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 详情 · r 刷新 · c 清空 · / 搜索 · ? 帮助 · Esc"
    segs = [f" · {total} 条事件"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f4ec 通知 / 事件日志", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "nv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(nv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(parts, style=_S_STATUS, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "nv-status"}))
    if search_mode:
        q = getattr(nv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "nv-search",
        }))
    return h(Column, None, children)
