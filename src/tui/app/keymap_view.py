"""keymap_view — KeymapView 键位自定义编辑器视图（模态全屏视图，2026-10）。

``/keymap`` 命令打开：App 在 ``model.fullscreen == "keymap"`` 时经全屏视图
注册表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图查看并修改 TUI 的
Ctrl 组合键绑定（``src.tui._keybindings`` 注册表）。

布局（React Ink 左右布局）：
  - 左栏「绑定列表」：组合键 + 动作 + 说明，↑↓/jk 选择（ListView 标准控件）；
  - 右栏「详情」：绑定 id / 当前键 / 默认键 / 动作 / 说明。

键盘：
  - 左栏：↑↓/jk 选择 · Enter/e 改键 · g/G 首末 · Esc/Ctrl+H 关闭；
  - 改键输入：输入组合键文本（如 ``ctrl+k``）回车提交 · Esc 取消；
  - 通用：``/`` 搜索 · ``n``/``N``/``p`` 定位 · ``?`` 帮助面板。

数据源：命令线程构建 ``model.keymap_view.entries``（``active_keybindings``）；
改键经 ``applied_seq`` 回传给命令线程执行（注册覆盖 + 持久化到配置
``keybindings_overrides``，重启后仍生效）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_memo
from src.tui.ink.widgets.listview import ListView

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

__all__ = ["KeymapView", "_detail_rows", "_keymap_search_text"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_KEY = Style(fg=214, bold=True)
_S_ACTION = Style(fg=252)
_S_DESC = Style(fg=110)
_S_ID = Style(fg=75)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_OK = Style(fg=40, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择绑定"},
    {"group": "操作", "keys": "Enter/e", "desc": "改键"},
    {"group": "操作", "keys": "Esc", "desc": "取消改键 / 关闭视图"},
    {"group": "搜索", "keys": "/", "desc": "搜索绑定"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
]


def _keymap_search_text(entry: dict) -> str:
    """绑定条目搜索文本（id / 组合键 / 动作 / 说明）。"""
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        str(entry.get("combo", "")), str(entry.get("action", "")),
        str(entry.get("id", "")), str(entry.get("description", "")),
    ])


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中绑定 → 详情行。"""
    if entry is None:
        return [[StyledRun("无选中绑定", _S_HINT)]]
    rows: list = []
    header = [StyledRun(str(entry.get("combo", "")), _S_TITLE)]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    for label, value in (
        ("id", entry.get("id", "")),
        ("当前键", entry.get("combo", "")),
        ("默认键", entry.get("default_combo", "")),
        ("动作", entry.get("action", "")),
        ("说明", entry.get("description", "")),
    ):
        runs = [
            StyledRun(f"{label}: ", _S_ID),
            StyledRun(str(value), _S_ACTION),
        ]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])
    rows.append([StyledRun("按 Enter 或 e 修改该绑定的组合键", _S_HINT)])
    return rows


def KeymapView(props) -> object:
    """键位自定义编辑器视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    kv = getattr(model, "keymap_view", None)
    visible = bool(kv is not None and kv.visible and not kv.done)
    entries = list(getattr(kv, "entries", None) or []) if kv is not None else []
    help_open = bool(getattr(kv, "help_open", False)) if kv is not None else False
    search_mode = bool(getattr(kv, "search_mode", False)) if kv is not None else False
    edit_mode = bool(getattr(kv, "edit_mode", False)) if kv is not None else False
    edit_value = (getattr(kv, "edit_value", "") or "") if kv is not None else ""
    pattern = (getattr(kv, "search_pattern", "") or "") if kv is not None else ""
    filter_on = bool(getattr(kv, "search_filter", False)) if kv is not None else False
    matches = list(getattr(kv, "search_matches", None) or []) if kv is not None else []
    status_message = (getattr(kv, "status_message", "") or "") if kv is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(kv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if kv is not None and sel != getattr(kv, "selected", None):
        kv.selected = sel
    entry = items[sel] if total else None

    left_w, right_w = split_panes(width, fallback=(36, 46))
    extra_rows = (1 if search_mode else 0) + (1 if edit_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 内容行（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → ``HookStateError``（视图渲染异常）。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_DESC,
                desc_style=_S_ACTION, sep_style=_S_SEP,
            )
        return _detail_rows(entry, right_w)

    content_rows = use_memo(_content_rows, (help_open, (id(entry), right_w)))
    window = content_rows[:max(1, vh)]

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _apply(report: dict) -> None:
        if kv is None:
            return
        kv.applied = report
        kv.applied_seq += 1

    def _handle(event) -> bool:
        if not visible or kv is None:
            return False
        ch = char_of(event)

        # ── 改键输入模式 ──
        if getattr(kv, "edit_mode", False):
            if event.kind == "escape":
                kv.edit_mode = False
                kv.status_message = "已取消改键"
                return True
            if event.kind == "backspace":
                v = getattr(kv, "edit_value", "") or ""
                if v:
                    kv.edit_value = v[:-1]
                return True
            if event.kind == "enter":
                combo = (getattr(kv, "edit_value", "") or "").strip()
                if entry is not None and combo:
                    _apply({"id": entry.get("id", ""), "combo": combo})
                    kv.status_message = "正在改键…"
                else:
                    kv.status_message = "请输入组合键（如 ctrl+k）"
                kv.edit_mode = False
                return True
            if event.kind == "char":
                if ch and "\n" not in ch and "\r" not in ch:
                    v = getattr(kv, "edit_value", "") or ""
                    if len(v) < 40:
                        kv.edit_value = v + ch
                return True
            return True

        verdict = handle_search_input(event, kv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            kv.search_mode = False
            kv.search_query = ""
            return True
        if verdict == "run":
            run_search(kv, entries, getattr(kv, "search_query", "") or "",
                       _keymap_search_text, on_first=lambda i: setattr(kv, "selected", _row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(kv, "help_open", False):
                kv.help_open = False
                kv.cursor = 0
                kv.scroll = 0
                return True
            kv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                kv.help_open = not bool(kv.help_open)
                kv.cursor = 0
                kv.scroll = 0
                return True
            if ch == "/":
                kv.search_mode = True
                kv.search_query = getattr(kv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(kv, "search_pattern", "") or ""):
                jump_match(kv, 1 if ch == "n" else -1,
                           on_jump=lambda i: setattr(kv, "selected", _row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    kv.search_filter = not bool(kv.search_filter)
                    kv.status_message = "过滤开启" if kv.search_filter else "过滤关闭"
                else:
                    kv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "e" and entry is not None:
                kv.edit_mode = True
                kv.edit_value = ""
                return True

        if event.kind == "enter" and entry is not None:
            kv.edit_mode = True
            kv.edit_value = str(entry.get("combo", ""))
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(kv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[kv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(f"{str(e.get('combo', '')):<10}", _S_KEY),
            StyledRun(str(e.get("action", "")), _S_ACTION),
            StyledRun("  " + str(e.get("description", "")), _S_DESC),
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
        return h(TEXT, {"styled": runs, "height": 1, "key": f"kv-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: setattr(kv, "selected", int(idx)),
        "focus": visible and not search_mode and not edit_mode,
    })

    right_children: list = []
    for i, runs in enumerate(window):
        right_children.append(h(TEXT, {"styled": runs, "height": 1, "key": f"kv-f-{i}"}))

    if edit_mode:
        hint = "  输入组合键（如 ctrl+k）· Enter 提交 · Esc 取消"
    elif search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter/e 改键 · / 搜索 · ? 帮助 · Esc 关闭"
    header_runs = build_header_runs(
        "\u258d\u2328 键位编辑器", _S_TITLE, [f" · {total} 个绑定"], hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "kv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    sruns = status_runs([], style=_S_STATUS, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "kv-status"}))
    if edit_mode:
        children.append(h(TEXT, {
            "children": f"新组合键: {edit_value}\u258f", "style": _S_PROMPT,
            "height": 1, "key": "kv-edit",
        }))
    if search_mode:
        q = getattr(kv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "kv-search",
        }))
    return h(Column, None, children)
