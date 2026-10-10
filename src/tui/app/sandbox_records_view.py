"""sandbox_records_view — SandboxRecordsView 沙盒变更记录流水视图（2026-10）。

``/sandbox records``（或沙盒概览视图按 ``l``）打开：App 在
``model.fullscreen == "sandbox_records"`` 时经全屏视图注册表**整屏只渲染本
组件**，关闭后恢复完整聊天界面。视图按**记录**逐条浏览文件沙盒的全部修改
（每条 = 一次文件变更），右栏显示该次变更的差异；可按 ``x`` 回滚该文件。

布局（React Ink 左右布局）：
  - 左栏「记录流水」：``#seq [标签] 路径 (工具 · 消息 N · 时间)``，↑↓/jk 选择；
  - 右栏「差异」：选中记录的 diff（前后内容）。

键盘：
  - 左栏：↑↓/jk 选择 · Enter/l 查看 · g/G 首末 · ``s`` 排序（正序/倒序）；
  - 右栏：jk/↑↓ 滚动 · h 返回；
  - 操作：``x`` 回滚该文件（再按确认）· ``y`` 复制路径；
  - 通用：``/`` 搜索 · ``n``/``N``/``p`` 定位 · ``f`` 过滤 · ``?`` 帮助 ·
    Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.sandbox_records_view.entries``
（``core.commands._sandbox_cmd.build_record_history_entries``）；回滚经
``applied_seq`` 回传命令线程执行；渲染期经 ``model.sandbox_refresher`` 实时刷新。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_effect, use_memo
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve
from ._modal_view import (
    close_fullscreen_view,
    empty_modal_frame,
    is_modal_close_key,
    use_modal_scope,
)
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
from .sandbox_common import (
    S_HINT,
    S_INSP_BG,
    S_LABEL,
    S_MATCH_BG,
    S_MATCH_CUR_BG,
    S_META,
    S_OK,
    S_PATH,
    S_PROMPT,
    S_SEP,
    S_SEL_BG,
    S_SEL_MARK,
    S_STATUS,
    S_TITLE,
    S_WARN,
    change_tag_style,
    diff_rows,
    fmt_time,
    line_delta,
)

__all__ = ["SandboxRecordsView", "_record_search_text"]

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择记录"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看差异"},
    {"group": "浏览", "keys": "h", "desc": "返回记录列表"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末"},
    {"group": "浏览", "keys": "s", "desc": "排序（正序/倒序）"},
    {"group": "操作", "keys": "x", "desc": "回滚该文件（再按确认）"},
    {"group": "操作", "keys": "y", "desc": "复制文件路径"},
    {"group": "搜索", "keys": "/", "desc": "搜索（路径/工具）"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]


def _record_search_text(entry: dict) -> str:
    """记录条目搜索文本（路径 + 标签 + 工具）。"""
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        str(entry.get("path", "")),
        str(entry.get("change_label", "")),
        str(entry.get("tool", "")),
        f"消息 {entry.get('message_index', '')}",
    ])


def _record_deps(entry, right_w: int) -> tuple:
    if not isinstance(entry, dict):
        return (None, right_w)
    before, after = entry.get("before"), entry.get("after")
    return (
        str(entry.get("path", "")),
        str(entry.get("message_index", "")),
        hash(before) if isinstance(before, str) else id(before),
        hash(after) if isinstance(after, str) else id(after),
        right_w,
    )


def _record_detail_rows(entry: dict, right_w: int) -> list:
    """选中记录 → 差异内容行。"""
    if not isinstance(entry, dict):
        return [[StyledRun("无选中记录", S_HINT)]]
    rows: list = []
    path = str(entry.get("path", ""))
    label = str(entry.get("change_label", ""))
    header = [
        StyledRun(f"#{entry.get('seq', 0)} ", S_META),
        StyledRun(path, S_TITLE),
        StyledRun(f"  {label}", change_tag_style(label)),
    ]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    meta = [
        StyledRun(str(entry.get("tool", "") or "?"), S_LABEL),
        StyledRun(f"  消息 {entry.get('message_index', '')}", S_META),
        StyledRun(f"  {fmt_time(entry.get('time'))}", S_META),
        StyledRun(f"  {line_delta(entry.get('before'), entry.get('after'))}", S_META),
    ]
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), S_SEP)])
    rows.extend(diff_rows(path, entry.get("before"), entry.get("after"), right_w))
    return rows


def SandboxRecordsView(props) -> object:
    """沙盒变更记录流水视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    rv = getattr(model, "sandbox_records_view", None)
    visible = bool(rv is not None and rv.visible and not rv.done)
    raw_entries = list(getattr(rv, "entries", None) or []) if rv is not None else []
    help_open = bool(getattr(rv, "help_open", False)) if rv is not None else False
    search_mode = bool(getattr(rv, "search_mode", False)) if rv is not None else False
    sort_desc = bool(getattr(rv, "sort_desc", False)) if rv is not None else False
    pattern = (getattr(rv, "search_pattern", "") or "") if rv is not None else ""
    filter_on = bool(getattr(rv, "search_filter", False)) if rv is not None else False
    matches = list(getattr(rv, "search_matches", None) or []) if rv is not None else []
    status_message = (getattr(rv, "status_message", "") or "") if rv is not None else ""
    revert_confirm = (getattr(rv, "revert_confirm", "") or "") if rv is not None else ""

    ordered = list(reversed(raw_entries)) if sort_desc else list(raw_entries)
    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(ordered)) if allowed is None or i in allowed]
    items = [ordered[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(rv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if rv is not None and sel != getattr(rv, "selected", None):
        rv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(rv, "pane", "list") if rv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(38, 44))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, right_w, key_style=S_WARN, group_style=S_META,
            desc_style=S_PATH, sep_style=S_SEP,
        )
    else:
        content_rows = use_memo(
            lambda: _record_detail_rows(entry, right_w), _record_deps(entry, right_w),
        )
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(rv, "cursor", 0) or 0, getattr(rv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if rv is not None:
        rv.cursor = cursor
        rv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(rv, "cursor", 0) or 0,
        lambda v: setattr(rv, "cursor", v),
        lambda: getattr(rv, "scroll", 0) or 0,
        lambda v: setattr(rv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if rv is None:
            return
        rv.selected = row
        rv.cursor = 0
        rv.scroll = 0
        rv.status_message = ""

    def _handle(event) -> bool:
        if not visible or rv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, rv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            rv.search_mode = False
            rv.search_query = ""
            return True
        if verdict == "run":
            run_search(rv, ordered, getattr(rv, "search_query", "") or "",
                       _record_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        # ★ F11 是沙盒视图族开关：在任一沙盒视图内按 F11 即关闭。
        if getattr(event, "kind", "") == "f11":
            close_fullscreen_view(model, rv, "sandbox_records")
            return True
        if is_modal_close_key(event):
            if getattr(rv, "help_open", False):
                rv.help_open = False
                rv.pane = "list"
                rv.cursor = 0
                rv.scroll = 0
                return True
            close_fullscreen_view(model, rv, "sandbox_records")
            return True

        if event.kind == "char":
            if ch == "?":
                rv.help_open = not bool(rv.help_open)
                rv.pane = "detail" if rv.help_open else "list"
                rv.cursor = 0
                rv.scroll = 0
                return True
            if ch == "/":
                rv.search_mode = True
                rv.search_query = getattr(rv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(rv, "search_pattern", "") or ""):
                jump_match(rv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    rv.search_filter = not bool(rv.search_filter)
                    rv.status_message = (
                        f"过滤开启：{len(matches)} 项" if rv.search_filter else "过滤关闭"
                    )
                else:
                    rv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "s":
                rv.sort_desc = not sort_desc
                rv.selected = 0
                rv.cursor = 0
                rv.scroll = 0
                rv.status_message = "排序：倒序（最新在前）" if rv.sort_desc else "排序：正序"
                return True
            if ch == "x" and entry is not None:
                path = str(entry.get("path", ""))
                if revert_confirm == path:
                    rv.applied = {"action": "revert", "path": path}
                    rv.applied_seq += 1
                    rv.status_message = "正在回滚…"
                    rv.revert_confirm = ""
                else:
                    rv.revert_confirm = path
                    rv.status_message = f"再按 x 确认回滚「{path}」"
                return True
            if ch == "y" and entry is not None:
                from src.tui._screen import set_clipboard

                path = str(entry.get("path", ""))
                if path and set_clipboard(path):
                    rv.status_message = f"已复制路径：{path}"
                else:
                    rv.status_message = "复制失败"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                rv.pane = "detail"
                rv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(rv, "help_open", False):
                rv.help_open = False
                return True
            rv.pane = "list"
            return True
        if handle_nav(event, _pane_state, total_content, max(1, vh)):
            return True
        return False

    _refresher = getattr(model, "sandbox_refresher", None)
    use_effect(
        (lambda: _refresher()) if (callable(_refresher) and visible) else None,
        None,
    )
    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(rv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[rv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        label = str(e.get("change_label", ""))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", S_SEL_MARK if is_sel else None),
            StyledRun(f"#{e.get('seq', 0):<4}", S_META),
            StyledRun(f"[{label}] ", change_tag_style(label)),
            StyledRun(str(e.get("path", "")), S_PATH),
            StyledRun(f"  \u00b7 {e.get('tool', '')} \u00b7 消息 {e.get('message_index', '')}",
                      S_HINT),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if revert_confirm and str(e.get("path", "")) == revert_confirm:
            bg = S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = S_MATCH_BG
        elif is_sel:
            bg = S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"sr-{idx}"})

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
        line_runs = runs if runs else [StyledRun(" ", None)]
        if is_cur:
            line_runs = [
                StyledRun(r.text, (r.style or Style()).merge(S_INSP_BG)) for r in line_runs
            ]
        right_children.append(h(TEXT, {
            "styled": line_runs, "height": 1, "key": f"sr-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · x 回滚 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 差异 · s 排序 · x 回滚 · / 搜索 · ? 帮助 · Esc 关闭"
    segs = [f" · {total}/{len(raw_entries)} 条记录"]
    if sort_desc:
        segs.append(" · 倒序")
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(ordered)}")
    header_runs = build_header_runs(
        "\u258d\U0001f9fe 变更记录流水", S_TITLE, segs, hint, width,
        hint_style=S_HINT, sep_style=S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "sr-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(rv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=S_STATUS, message=status_message,
        message_style=S_WARN if ("确认" in status_message or "失败" in status_message) else S_OK,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "sr-status"}))
    if search_mode:
        q = getattr(rv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": S_PROMPT, "height": 1, "key": "sr-search",
        }))
    return h(Column, None, children)
