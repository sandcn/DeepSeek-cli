"""skill_view — SkillView 技能浏览器视图（模态全屏视图，2026-10）。

``/skill`` 命令打开：App 在 ``model.fullscreen == "skill"`` 时经全屏视图注册
表**整屏只渲染本组件**，关闭后恢复完整聊天界面。视图浏览 / 管理技能
（``src.skills.default_registry``）。

布局（React Ink 左右布局）：
  - 左栏「技能列表」：技能名 + 来源标签 + 调用面标记（M/U）；
  - 右栏「详情」：选中技能全部字段 + 指令正文（按栏宽换行）。

键盘：
  - 左栏：↑↓/jk 选择 · l/Enter 查看详情 · g/G 首末 · Esc/Ctrl+H 关闭；
  - 右栏：jk/↑↓ 滚动 · h 返回左栏；
  - 操作：``i`` 安装（输入 owner/repo）· ``u`` 更新选中 · ``x`` 卸载选中
    （再按确认）· ``r`` 刷新列表；
  - 通用：``/`` 搜索 · ``n``/``N``/``p`` 定位匹配 · ``f`` 过滤 · ``?`` 帮助。
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

__all__ = ["SkillView", "_skill_search_text", "_detail_rows"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_TAG = Style(fg=110)
_S_FIELD = Style(fg=75)
_S_VALUE = Style(fg=252)
_S_BODY = Style(fg=252)
_S_OK = Style(fg=40, bold=True)
_S_WARN = Style(fg=214, bold=True)
_S_DIMTAG = Style(fg=238)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_INSP_BG = Style(bg=237)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_PROMPT = Style(fg=45, bold=True)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择技能"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看详情"},
    {"group": "浏览", "keys": "h", "desc": "返回列表"},
    {"group": "操作", "keys": "i", "desc": "安装 owner/repo"},
    {"group": "操作", "keys": "u", "desc": "更新选中技能"},
    {"group": "操作", "keys": "x", "desc": "卸载选中（再按确认）"},
    {"group": "操作", "keys": "r", "desc": "刷新列表"},
    {"group": "搜索", "keys": "/", "desc": "搜索技能"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]

_MAX_DETAIL_ROWS = 3000


def _skill_search_text(entry: dict) -> str:
    if not isinstance(entry, dict):
        return ""
    return f"{entry.get('name', '')} {entry.get('description', '')} {entry.get('source', '')}"


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中技能 → 详情行（含正文）。"""
    if entry is None:
        return [[StyledRun("无选中技能", _S_HINT)]]
    rows: list = []
    header = [StyledRun(str(entry.get("name", "")), _S_TITLE)]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    fields = [
        ("来源", entry.get("source", "")),
        ("提供方", entry.get("provider", "")),
        ("路径", entry.get("path", "") or "(运行时)"),
        ("模型可调用", "是" if entry.get("model_invocable") else "否"),
        ("用户可调用", "是" if entry.get("user_invocable") else "否"),
        ("何时使用", entry.get("when_to_use", "") or "-"),
        ("描述", entry.get("description", "")),
    ]
    for label, value in fields:
        runs = [
            StyledRun(f"{label}: ", _S_FIELD),
            StyledRun(str(value), _S_VALUE),
        ]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    content = _skill_content(str(entry.get("name", "")))
    if not content:
        rows.append([StyledRun("(无正文)", _S_HINT)])
    else:
        for seg in content.split("\n"):
            runs = [StyledRun(seg, _S_BODY if seg else None)]
            for line in wrap_runs_by_width(runs, max(1, right_w)):
                rows.append(list(line.runs))
            if len(rows) > _MAX_DETAIL_ROWS:
                rows = rows[:_MAX_DETAIL_ROWS]
                rows.append([StyledRun("\u2026 内容过长，已截断", _S_HINT)])
                break
    return rows


def _skill_content(name: str) -> str:
    """按名加载技能正文（失败返回空串）。"""
    if not name:
        return ""
    try:
        from src.skills import default_registry

        definition = default_registry().get(name)
        return str(getattr(definition, "content", "") or "") if definition else ""
    except Exception:
        return ""


def SkillView(props) -> object:
    """技能浏览器视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    sv = getattr(model, "skill_view", None)
    visible = bool(sv is not None and sv.visible and not sv.done)
    entries = list(getattr(sv, "entries", None) or []) if sv is not None else []
    help_open = bool(getattr(sv, "help_open", False)) if sv is not None else False
    search_mode = bool(getattr(sv, "search_mode", False)) if sv is not None else False
    pattern = (getattr(sv, "search_pattern", "") or "") if sv is not None else ""
    filter_on = bool(getattr(sv, "search_filter", False)) if sv is not None else False
    matches = list(getattr(sv, "search_matches", None) or []) if sv is not None else []
    status_message = (getattr(sv, "status_message", "") or "") if sv is not None else ""
    input_mode = (getattr(sv, "input_mode", "") or "") if sv is not None else ""
    input_value = (getattr(sv, "input_value", "") or "") if sv is not None else ""
    busy = bool(getattr(sv, "busy", False)) if sv is not None else False

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(sv, "selected", 0) or 0), total - 1))
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

    left_w, right_w = split_panes(width, fallback=(34, 48))
    extra_rows = (1 if search_mode else 0) + (1 if input_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → ``HookStateError``（视图渲染异常）。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_TAG,
                desc_style=_S_VALUE, sep_style=_S_SEP,
            )
        return _detail_rows(entry, right_w)

    content_rows = use_memo(_content_rows, (help_open, (id(entry), right_w)))
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

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if sv is None:
            return
        sv.selected = row
        sv.cursor = 0
        sv.scroll = 0

    def _apply(report: dict) -> None:
        if sv is None:
            return
        sv.applied = report
        sv.applied_seq += 1

    def _handle(event) -> bool:
        if not visible or sv is None:
            return False
        ch = char_of(event)

        if busy:
            # 操作进行中：仅允许 Esc 关闭
            if is_modal_close_key(event):
                sv.try_set_final("cancel")
                return True
            return True

        # ── 输入模式（安装 owner/repo） ──
        if getattr(sv, "input_mode", ""):
            if event.kind == "escape":
                sv.input_mode = ""
                sv.status_message = "已取消"
                return True
            if event.kind == "backspace":
                v = getattr(sv, "input_value", "") or ""
                if v:
                    sv.input_value = v[:-1]
                return True
            if event.kind == "enter":
                spec = (getattr(sv, "input_value", "") or "").strip()
                mode = getattr(sv, "input_mode", "")
                if spec:
                    _apply({"action": mode, "spec": spec})
                    sv.status_message = "正在处理…"
                else:
                    sv.status_message = "输入不能为空"
                sv.input_mode = ""
                return True
            if event.kind == "char":
                if ch and "\n" not in ch and "\r" not in ch:
                    v = getattr(sv, "input_value", "") or ""
                    if len(v) < 120:
                        sv.input_value = v + ch
                return True
            return True

        verdict = handle_search_input(event, sv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            sv.search_mode = False
            sv.search_query = ""
            return True
        if verdict == "run":
            run_search(sv, entries, getattr(sv, "search_query", "") or "",
                       _skill_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
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
                jump_match(sv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    sv.search_filter = not bool(sv.search_filter)
                    sv.status_message = "过滤开启" if sv.search_filter else "过滤关闭"
                else:
                    sv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "i":
                sv.input_mode = "install"
                sv.input_value = ""
                return True
            if ch == "u" and entry is not None:
                _apply({"action": "update", "target": str(entry.get("name", ""))})
                sv.status_message = "正在更新…"
                return True
            if ch == "x" and entry is not None:
                target = str(entry.get("name", ""))
                if getattr(sv, "delete_confirm", "") == target:
                    _apply({"action": "remove", "target": target})
                    sv.status_message = "正在卸载…"
                else:
                    sv.delete_confirm = target
                    sv.status_message = f"再按 x 确认卸载「{target}」"
                return True
            if ch == "r":
                sv.refresh_seq += 1
                sv.status_message = "已刷新"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
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

    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(sv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[sv.search_idx]
    delete_confirm = (getattr(sv, "delete_confirm", "") or "") if sv is not None else ""

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        marks = ""
        if e.get("model_invocable"):
            marks += "M"
        if e.get("user_invocable"):
            marks += "U"
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(str(e.get("name", "")), _S_NAME),
            StyledRun("  " + str(e.get("source", "")), _S_TAG),
            StyledRun("  " + marks, _S_DIMTAG),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if delete_confirm and str(e.get("name", "")) == delete_confirm:
            bg = _S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = _S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = _S_MATCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"skv-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _select_row(int(idx)),
        "focus": visible and pane == "list" and not search_mode and not input_mode,
    })

    right_children: list = []
    window = content_rows[scroll:scroll + max(1, vh)]
    for i, runs in enumerate(window):
        abs_idx = scroll + i
        is_cur = pane == "detail" and abs_idx == cursor
        if is_cur:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG)) for r in runs]
        right_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"skv-f-{abs_idx}",
        }))

    if busy:
        hint = "  处理中… 请稍候"
    elif input_mode:
        hint = "  输入仓库（owner/repo）· Enter 提交 · Esc 取消"
    elif search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · i 安装 · u 更新 · x 卸载 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 详情 · i 安装 · u 更新 · x 卸载 · r 刷新 · / 搜索 · ? 帮助"
    segs = [f" · {total} 个技能"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f9e9 技能浏览器", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "skv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
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
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "skv-status"}))
    if input_mode:
        children.append(h(TEXT, {
            "children": f"安装: {input_value}\u258f", "style": _S_PROMPT,
            "height": 1, "key": "skv-input",
        }))
    if search_mode:
        q = getattr(sv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "skv-search",
        }))
    return h(Column, None, children)
