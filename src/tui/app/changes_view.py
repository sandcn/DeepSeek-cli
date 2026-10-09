"""changes_view — ChangesView 文件变更审查器视图（模态全屏视图，2026-10）。

``/changes`` 命令打开：App 在 ``model.fullscreen == "changes"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图审查文件沙盒中被
修改过的文件（diff + 单文件回滚）。

布局（React Ink 左右布局）：
  - 左栏「文件列表」：变更标签（新建/删除/修改/无变化）+ 路径 + 修改次数；
  - 右栏「差异」：选中文件的 ANSI diff（``render_diff_to_ansi`` 产出，经
    ``_ansi_runs`` 转 StyledRun——增/删/上下文分色），焦点在右栏时 jk/↑↓
    滚动 + 当前行背景高亮（vim cursorline 语义）。

键盘：
  - 左栏：↑↓/jk 选择 · Enter/l 查看差异 · x 回滚（再按确认）· y 复制路径 ·
    g/G 首末 · Esc/Ctrl+H 关闭；
  - 右栏：jk/↑↓ 滚动 · h 返回左栏 · Esc/Ctrl+H 关闭；
  - 通用：``/`` 搜索（路径）· ``n``/``N``/``p`` 定位匹配 · ``f`` 过滤 ·
    ``?`` 帮助面板。

数据源：命令线程构建 ``model.changes_view.entries``（``SandboxManager`` 的
文件变更记录分组）；回滚经 ``applied_seq`` 回传给命令线程执行（恢复文件内容
+ 记录回滚动作保持沙盒一致）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_memo
from src.tui.ink.widgets.listview import ListView

from ._ansi_runs import ansi_text_to_rows
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

__all__ = ["ChangesView", "_change_search_text", "_detail_rows", "_detail_deps"]

# ── 样式 ──
_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_PATH = Style(fg=252)
_S_TAG = Style(fg=110)
_S_ADD = Style(fg=40)
_S_DEL = Style(fg=196)
_S_META = Style(fg=75)
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
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择文件"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看差异"},
    {"group": "浏览", "keys": "h", "desc": "返回文件列表"},
    {"group": "操作", "keys": "x", "desc": "回滚该文件（再按确认）"},
    {"group": "操作", "keys": "y", "desc": "复制文件路径"},
    {"group": "搜索", "keys": "/", "desc": "搜索路径"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

#: 差异渲染最大行数（超长 diff 截断，避免巨型文件拖慢渲染）。
_MAX_DIFF_ROWS = 4000


def _change_search_text(entry: dict) -> str:
    """文件条目搜索文本（路径 + 变更标签）。"""
    if not isinstance(entry, dict):
        return ""
    return f"{entry.get('path', '')} {entry.get('change_label', '')}"


def _detail_deps(entry, right_w: int) -> tuple:
    """``_detail_rows`` 的 useMemo 依赖（**值驱动**）。

    ★ P2 修复（review）：修复前 deps 为 ``(id(entry), right_w)``——``id()`` 是
    对象内存地址；entry 被替换（回滚后命令线程重建 entries）且旧对象被 GC 时，
    新对象可能复用同一地址 → ``_object_is`` 判定依赖未变 → 复用**陈旧**的差异
    预览（回滚后仍显示回滚前内容）。改为影响输出的字段值（路径 / 变更标签 /
    修改次数 / 消息序号 + before/after 内容指纹）：同内容保持缓存命中（性能
    不退化），内容变化必然重算。
    """
    if not isinstance(entry, dict):
        return (None, right_w)
    before = entry.get("before")
    after = entry.get("after")
    return (
        str(entry.get("path", "")),
        str(entry.get("change_label", "")),
        str(entry.get("records", "")),
        str(entry.get("message_index", "")),
        hash(before) if isinstance(before, str) else id(before),
        hash(after) if isinstance(after, str) else id(after),
        right_w,
    )


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中文件 → 差异内容行（``list[list[StyledRun]]``）。"""
    if entry is None:
        return [[StyledRun("无选中文件", _S_HINT)]]
    rows: list = []
    path = str(entry.get("path", ""))
    label = str(entry.get("change_label", ""))
    header = [
        StyledRun(path, _S_TITLE),
        StyledRun(f"  {label}", _S_TAG),
    ]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    meta = [
        StyledRun(f"{entry.get('records', 0)} 次修改", _S_META),
        StyledRun(f"  消息 {entry.get('message_index', '')}", _S_META),
    ]
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])

    before, after = entry.get("before"), entry.get("after")
    if before == after:
        rows.append([StyledRun("(内容无变化)", _S_HINT)])
        return rows
    from src.tui._diff_renderer import render_diff_to_ansi

    try:
        ansi = render_diff_to_ansi(path, before or "", after or "")
    except Exception:
        ansi = ""
    if not ansi:
        rows.append([StyledRun("(无差异可显示)", _S_HINT)])
        return rows
    for runs in ansi_text_to_rows(ansi):
        if not runs:
            rows.append([StyledRun(" ", None)])
            continue
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
        if len(rows) > _MAX_DIFF_ROWS:
            rows = rows[:_MAX_DIFF_ROWS]
            rows.append([StyledRun("\u2026 差异过长，已截断", _S_HINT)])
            break
    return rows


def ChangesView(props) -> object:
    """文件变更审查器视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    cv = getattr(model, "changes_view", None)
    visible = bool(cv is not None and cv.visible and not cv.done)
    entries = list(getattr(cv, "entries", None) or []) if cv is not None else []
    help_open = bool(getattr(cv, "help_open", False)) if cv is not None else False
    search_mode = bool(getattr(cv, "search_mode", False)) if cv is not None else False
    pattern = (getattr(cv, "search_pattern", "") or "") if cv is not None else ""
    filter_on = bool(getattr(cv, "search_filter", False)) if cv is not None else False
    matches = list(getattr(cv, "search_matches", None) or []) if cv is not None else []
    status_message = (getattr(cv, "status_message", "") or "") if cv is not None else ""
    revert_confirm = (getattr(cv, "revert_confirm", "") or "") if cv is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(cv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if cv is not None and sel != getattr(cv, "selected", None):
        cv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(cv, "pane", "list") if cv is not None else "list"
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
            desc_style=_S_PATH, sep_style=_S_SEP,
        )
    else:
        content_rows = use_memo(
            lambda: _detail_rows(entry, right_w),
            _detail_deps(entry, right_w),
        )
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(cv, "cursor", 0) or 0, getattr(cv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if cv is not None:
        cv.cursor = cursor
        cv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(cv, "cursor", 0) or 0,
        lambda v: setattr(cv, "cursor", v),
        lambda: getattr(cv, "scroll", 0) or 0,
        lambda v: setattr(cv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if cv is None:
            return
        cv.selected = row
        cv.cursor = 0
        cv.scroll = 0
        cv.status_message = ""

    def _apply(report: dict) -> None:
        if cv is None:
            return
        cv.applied = report
        cv.applied_seq += 1

    def _handle(event) -> bool:
        if not visible or cv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, cv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            cv.search_mode = False
            cv.search_query = ""
            return True
        if verdict == "run":
            run_search(cv, entries, getattr(cv, "search_query", "") or "",
                       _change_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(cv, "help_open", False):
                cv.help_open = False
                cv.pane = "list"
                cv.cursor = 0
                cv.scroll = 0
                return True
            cv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                cv.help_open = not bool(cv.help_open)
                cv.pane = "detail" if cv.help_open else "list"
                cv.cursor = 0
                cv.scroll = 0
                return True
            if ch == "/":
                cv.search_mode = True
                cv.search_query = getattr(cv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(cv, "search_pattern", "") or ""):
                jump_match(cv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    cv.search_filter = not bool(cv.search_filter)
                    cv.status_message = (
                        f"过滤开启：{len(matches)} 项" if cv.search_filter else "过滤关闭"
                    )
                else:
                    cv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "x" and entry is not None:
                path = str(entry.get("path", ""))
                if revert_confirm == path:
                    _apply({"action": "revert", "path": path})
                    cv.status_message = "正在回滚…"
                else:
                    cv.revert_confirm = path
                    cv.status_message = f"再按 x 确认回滚「{path}」"
                return True
            if ch == "y" and entry is not None:
                from src.tui._screen import set_clipboard

                path = str(entry.get("path", ""))
                if path and set_clipboard(path):
                    cv.status_message = f"已复制路径：{path}"
                else:
                    cv.status_message = "复制失败"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                cv.pane = "detail"
                cv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(cv, "help_open", False):
                cv.help_open = False
                return True
            cv.pane = "list"
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
    if matched_set and 0 <= getattr(cv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[cv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        path = str(e.get("path", ""))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"[{e.get('change_label', '')}] ", _S_TAG),
            StyledRun(path, _S_PATH),
            StyledRun(f"  ×{e.get('records', 0)}", _S_HINT),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if revert_confirm and path == revert_confirm:
            bg = _S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = _S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = _S_MATCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"cv-{idx}"})

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
            "styled": runs, "height": 1, "key": f"cv-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · x 回滚 · / 搜索 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 差异 · x 回滚 · y 复制路径 · / 搜索 · ? 帮助 · Esc"
    segs = [f" · {total} 个文件"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f4dd 文件变更审查", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "cv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]

    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(cv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=_S_STATUS, message=status_message,
        message_style=_S_WARN if ("确认" in status_message or "失败" in status_message) else _S_OK,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "cv-status"}))
    if search_mode:
        q = getattr(cv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "cv-search",
        }))
    return h(Column, None, children)
