"""sessions_view — SessionsView 会话浏览器视图（模态全屏视图，2026-10）。

``/sessions`` 命令打开：App 在 ``model.fullscreen == "sessions"`` 时经全屏视图
注册表**整屏只渲染本组件**（消息区/顶部标题栏/状态栏/输入区全部不显示），
关闭后恢复完整聊天界面。视图浏览 ``.chat/msg_list`` 下保存的会话。

布局（React Ink 左右布局）：
  - 左栏「会话列表」：标题 + 模型 + 消息数 + 保存时间，↑↓/jk/PgUp/PgDn/
    Home/End/g/G 上下选择（ListView 标准控件——受控光标 + 虚拟滚动 +
    选中整行高亮）；
  - 右栏「会话预览」：选中会话的消息摘要（角色 + 单行文本），焦点在右栏时
    jk/↑↓ 滚动 + 当前行背景高亮（vim cursorline 语义）。

键盘：
  - 左栏：↑↓/jk 选择 · Enter 加载该会话 · l 进入右栏预览 · g/G 首末 ·
    Esc/Ctrl+H 关闭；
  - 右栏：jk/↑↓ 滚动 · h 返回左栏 · Esc/Ctrl+H 关闭；
  - 通用：``/`` 搜索（标题/模型/ID）；``n``/``N``/``p`` 切换匹配；``f`` 过滤；
    ``r`` 重命名（输入回车提交）；``d`` 删除（再按 ``d`` 确认）；``y`` 复制
    会话 ID；``?`` 帮助面板。

数据源：命令线程构建 ``model.sessions_view.entries``（持久化端口 list_sessions
+ 选中会话预览）；组件只读。加载 / 重命名 / 删除经 ``applied_seq`` 回传给
命令线程执行（保持数据层与 UI 线程分离）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
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

__all__ = ["SessionsView", "_session_search_text"]

# ── 样式（静态色——浏览界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_META = Style(fg=110)
_S_ID = Style(fg=75)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_OK = Style(fg=40, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_PROMPT = Style(fg=45, bold=True)
_S_ROLE = Style(fg=110, bold=True)
_S_BODY = Style(fg=252)

#: 会话浏览器键位速查（帮助面板数据源）。
_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择会话"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末会话"},
    {"group": "浏览", "keys": "l/h", "desc": "进入/返回预览"},
    {"group": "浏览", "keys": "Enter", "desc": "加载选中会话"},
    {"group": "操作", "keys": "r", "desc": "重命名会话"},
    {"group": "操作", "keys": "d", "desc": "删除会话（再按确认）"},
    {"group": "操作", "keys": "y", "desc": "复制会话 ID"},
    {"group": "搜索", "keys": "/", "desc": "搜索（标题/模型/ID）"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]


def _session_search_text(entry: dict) -> str:
    """会话条目搜索文本（标题 / ID / 模型）。"""
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        str(entry.get("title", "") or ""),
        str(entry.get("id", "") or ""),
        str(entry.get("model", "") or ""),
    ])


def _preview_rows(entry: dict, right_w: int) -> list:
    """选中会话 → 预览内容行（``list[list[StyledRun]]``）。"""
    if entry is None:
        return [[StyledRun("无选中会话", _S_HINT)]]
    rows: list = []
    header = [
        StyledRun(str(entry.get("title", "") or "(无标题)"), _S_TITLE),
    ]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    meta = [
        StyledRun(f"ID {entry.get('id', '')}", _S_ID),
        StyledRun(f"  {entry.get('model', '?')}", _S_META),
        StyledRun(f"  {entry.get('message_count', 0)} 条消息", _S_META),
        StyledRun(f"  {entry.get('saved_at', '')}", _S_META),
    ]
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    preview = entry.get("preview_lines") or []
    if not preview:
        rows.append([StyledRun("(无预览)", _S_HINT)])
    for line in preview:
        if isinstance(line, (list, tuple)) and len(line) >= 2:
            role, text = line[0], line[1]
        else:
            role, text = "", str(line)
        runs = [
            StyledRun(f"{role}: ", _S_ROLE),
            StyledRun(str(text), _S_BODY),
        ]
        for wl in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(wl.runs))
    return rows


def SessionsView(props) -> object:
    """会话浏览器视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（读 ``model.sessions_view`` / ``model.fullscreen``）。
        width: 终端宽度（左右栏宽分配）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    sv = getattr(model, "sessions_view", None)
    visible = bool(sv is not None and sv.visible and not sv.done)
    entries = list(getattr(sv, "entries", None) or []) if sv is not None else []
    help_open = bool(getattr(sv, "help_open", False)) if sv is not None else False
    search_mode = bool(getattr(sv, "search_mode", False)) if sv is not None else False
    rename_mode = bool(getattr(sv, "rename_mode", False)) if sv is not None else False
    pattern = (getattr(sv, "search_pattern", "") or "") if sv is not None else ""
    filter_on = bool(getattr(sv, "search_filter", False)) if sv is not None else False
    matches = list(getattr(sv, "search_matches", None) or []) if sv is not None else []
    status_message = (getattr(sv, "status_message", "") or "") if sv is not None else ""
    delete_confirm = (getattr(sv, "delete_confirm", "") or "") if sv is not None else ""

    # ── 过滤视图（``f``：只显示搜索匹配） ──
    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    view_entries = [
        (i, e) for i, e in enumerate(entries) if allowed is None or i in allowed
    ]
    items = [e for _, e in view_entries]
    index_map = [i for i, _ in view_entries]  # 视图位置 → 原始索引
    total = len(items)
    try:
        sel = max(0, min(int(getattr(sv, "selected", 0) or 0), total - 1)) if total else 0
    except (TypeError, ValueError):
        sel = 0
    if sv is not None and sel != getattr(sv, "selected", None):
        sv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(sv, "pane", "list") if sv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(32, 50))
    extra_rows = (1 if search_mode else 0) + (1 if rename_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（帮助 / 预览） ──
    if help_open:
        from ._view_common import help_panel_rows

        content_rows = help_panel_rows(
            _KEYMAP, right_w,
            key_style=_S_WARN, group_style=_S_META, desc_style=_S_NAME,
            sep_style=_S_SEP,
        )
    else:
        content_rows = _preview_rows(entry, right_w)
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

    def _select_row(row: int) -> None:
        if sv is None:
            return
        sv.selected = row
        sv.cursor = 0
        sv.scroll = 0
        sv.status_message = ""

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _run_search() -> None:
        run_search(sv, entries, getattr(sv, "search_query", "") or "",
                   _session_search_text,
                   on_first=lambda i: _select_row(_row_of(i)))

    def _jump(delta: int) -> None:
        jump_match(sv, delta, on_jump=lambda i: _select_row(_row_of(i)))

    def _apply(report: dict) -> None:
        if sv is None:
            return
        sv.applied = report
        sv.applied_seq += 1

    def _handle(event) -> bool:
        if not visible or sv is None:
            return False
        ch = char_of(event)

        # ── 重命名输入模式 ──
        if getattr(sv, "rename_mode", False):
            if event.kind == "escape":
                sv.rename_mode = False
                sv.status_message = "已取消重命名"
                return True
            if event.kind == "backspace":
                v = getattr(sv, "rename_value", "") or ""
                if v:
                    sv.rename_value = v[:-1]
                    sv.rename_cursor = max(0, len(sv.rename_value))
                return True
            if event.kind == "enter":
                new_title = (getattr(sv, "rename_value", "") or "").strip()
                if entry is not None and new_title:
                    _apply({"action": "rename", "id": entry.get("id", ""),
                            "title": new_title})
                    sv.status_message = "正在重命名…"
                else:
                    sv.status_message = "标题不能为空"
                sv.rename_mode = False
                return True
            if event.kind == "char":
                if ch and "\n" not in ch and "\r" not in ch:
                    v = getattr(sv, "rename_value", "") or ""
                    if len(v) < 120:
                        sv.rename_value = v + ch
                        sv.rename_cursor = len(sv.rename_value)
                return True
            return True

        # ── 搜索输入模式 ──
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
            if ch in ("n", "N", "p") and (getattr(sv, "search_pattern", "") or ""):
                _jump(1 if ch == "n" else -1)
                return True
            if ch == "f":
                if pattern and matches:
                    sv.search_filter = not bool(sv.search_filter)
                    sv.status_message = (
                        f"过滤开启：{len(matches)} 项" if sv.search_filter else "过滤关闭"
                    )
                else:
                    sv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "r" and entry is not None:
                sv.rename_mode = True
                sv.rename_value = str(entry.get("title", "") or "")
                sv.rename_cursor = len(sv.rename_value)
                return True
            if ch == "d" and entry is not None:
                sid = str(entry.get("id", ""))
                if delete_confirm == sid:
                    _apply({"action": "delete", "id": sid})
                    sv.status_message = "正在删除…"
                else:
                    sv.delete_confirm = sid
                    sv.status_message = f"再按 d 确认删除「{entry.get('title', '')}」"
                return True
            if ch == "y" and entry is not None:
                from src.tui._screen import set_clipboard

                sid = str(entry.get("id", ""))
                if sid and set_clipboard(sid):
                    sv.status_message = f"已复制 ID：{sid}"
                else:
                    sv.status_message = "复制失败"
                return True

        if pane == "list":
            if event.kind == "enter" and entry is not None:
                _apply({"action": "load", "id": entry.get("id", ""),
                        "title": entry.get("title", "")})
                sv.status_message = "正在加载…"
                return True
            if event.kind == "char" and ch == "l":
                sv.pane = "detail"
                sv.help_open = False
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

    # ── 左栏渲染 ──
    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(sv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[sv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        title = str(e.get("title", "") or "(无标题)")
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(title, _S_NAME),
        ]
        meta = f"{e.get('model', '?')} · {e.get('message_count', 0)}条"
        runs.append(StyledRun("  " + meta, _S_META))
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        eid = str(e.get("id", ""))
        if delete_confirm and eid == delete_confirm:
            bg = _S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = _S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = _S_MATCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"sv-{idx}"})

    def _on_navigate(idx: int) -> None:
        _select_row(int(idx))

    # 每个条目带上**原始索引**（搜索定位/匹配高亮用）——轻量浅拷贝，不影响原始数据。
    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": _on_navigate,
        "focus": visible and pane == "list" and not search_mode and not rename_mode,
    })

    # ── 右栏渲染 ──
    right_children: list = []
    window = content_rows[scroll:scroll + max(1, vh)]
    for i, runs in enumerate(window):
        abs_idx = scroll + i
        is_cur = pane == "detail" and abs_idx == cursor
        if is_cur:
            runs = [
                StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG))
                for r in runs
            ]
        right_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"sv-f-{abs_idx}",
        }))

    # ── 头部 ──
    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif rename_mode:
        hint = "  输入新标题 · Enter 提交 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · Enter 加载 · / 搜索 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 加载 · r 重命名 · d 删除 · / 搜索 · ? 帮助 · Esc 关闭"
    segs = [f" · {total} 个会话"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f4c2 会话浏览器", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "sv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]

    # ── 底部状态行 ──
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(sv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=_S_STATUS, message=status_message,
        message_style=_S_WARN if ("确认" in status_message or "失败" in status_message) else _S_OK,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "sv-status"}))
    if rename_mode:
        v = getattr(sv, "rename_value", "") or ""
        children.append(h(TEXT, {
            "children": f"重命名: {v}\u258f", "style": _S_PROMPT,
            "height": 1, "key": "sv-rename",
        }))
    if search_mode:
        q = getattr(sv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT,
            "height": 1, "key": "sv-search",
        }))
    return h(Column, None, children)
