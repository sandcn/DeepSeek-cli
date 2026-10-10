"""theme_view — ThemeView 主题选择器视图（模态全屏视图，2026-10）。

``/theme`` 命令打开：App 在 ``model.fullscreen == "theme"`` 时经全屏视图注册
表**整屏只渲染本组件**，关闭后恢复完整聊天界面。

布局（React Ink 左右布局）：
  - 左栏「主题列表」：主题名 + 描述 + 当前标记（✓）；
  - 右栏「色板预览」：选中主题的语义色槽色块（accent/text/sep/…）——**实时
    预览**：↑↓ 移动即把该主题写入配置并失效调色板缓存，全局即时生效。

键盘：
  - ↑↓/jk 选择（即时预览）· Enter 确认并关闭 · Esc 恢复打开时主题并关闭 ·
    g/G 首末 · / 过滤 · ? 帮助。

数据源：命令线程构建 ``model.theme_view.entries``（``ThemeRegistry`` 主题集 +
当前主题）；主题应用经 ``update_config`` + 调色板缓存失效（与 config_view
编辑 theme 同路径）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs
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

__all__ = ["ThemeView", "_palette_preview_rows", "apply_theme"]

_S_TITLE = Style(fg=45, bold=True)
_S_HINT = Style(fg=242)
_S_SEP = Style(fg=238)
_S_NAME = Style(fg=252)
_S_DESC = Style(fg=110)
_S_OK = Style(fg=40, bold=True)
_S_SEL_BG = Style(bg=237)
_S_SEL_MARK = Style(fg=45, bold=True)
_S_MATCH_BG = Style(bg=236)
_S_MATCH_CUR_BG = Style(bg=25)
_S_STATUS = Style(fg=221)
_S_WARN = Style(fg=214, bold=True)
_S_PROMPT = Style(fg=45, bold=True)
_S_SLOT = Style(fg=75)

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择主题（即时预览）"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末主题"},
    {"group": "操作", "keys": "Enter", "desc": "确认并关闭"},
    {"group": "操作", "keys": "Esc", "desc": "恢复原主题并关闭"},
    {"group": "搜索", "keys": "/", "desc": "过滤主题"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
]


def apply_theme(name: str) -> bool:
    """应用主题：写配置 + 失效调色板缓存（与 config_view 编辑 theme 同路径）。"""
    if not name:
        return False
    try:
        from src.config.loader import update_config

        update_config("theme", name)
    except Exception:
        try:
            from src.config.proxy import config

            config.set("theme", name)
        except Exception:
            return False
    try:
        from src.tui.core._theme import _invalidate_palette_cache

        _invalidate_palette_cache()
    except Exception:
        pass
    return True


def _slot_color(style) -> object:
    """从调色板槽位 ``Style`` 取主色（fg 优先，无则 None）。"""
    return getattr(style, "fg", None)


def _palette_preview_rows(name: str, right_w: int) -> list:
    """选中主题 → 色板预览行（每个语义槽一行：名称 + 色块）。"""
    from src.tui.core._theme import ThemeRegistry, _PALETTE_SLOTS

    rows: list = []
    palette = ThemeRegistry.resolve(name)
    rows.append([StyledRun(str(name), _S_TITLE)])
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP)])
    for slot in _PALETTE_SLOTS:
        style = getattr(palette, slot, None)
        color = _slot_color(style) if style is not None else None
        bar_style = Style(bg=color) if color is not None else Style(bg=238)
        rows.append([
            StyledRun(f"{slot:<16}", _S_SLOT),
            StyledRun("  ", None),
            StyledRun("\u2588" * 8, bar_style),
            StyledRun("   " + (str(color) if color is not None else "-"), _S_HINT),
        ])
    wrapped: list = []
    for row in rows:
        if right_w > 0:
            wrapped.append(truncate_runs(row, right_w))
        else:
            wrapped.append(row)
    return wrapped


def ThemeView(props) -> object:
    """主题选择器视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    tv = getattr(model, "theme_view", None)
    visible = bool(tv is not None and tv.visible and not tv.done)
    entries = list(getattr(tv, "entries", None) or []) if tv is not None else []
    original = (getattr(tv, "original", "") or "") if tv is not None else ""
    help_open = bool(getattr(tv, "help_open", False)) if tv is not None else False
    search_mode = bool(getattr(tv, "search_mode", False)) if tv is not None else False
    pattern = (getattr(tv, "search_pattern", "") or "") if tv is not None else ""
    filter_on = bool(getattr(tv, "search_filter", False)) if tv is not None else False
    matches = list(getattr(tv, "search_matches", None) or []) if tv is not None else []
    status_message = (getattr(tv, "status_message", "") or "") if tv is not None else ""

    allowed = None
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(tv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if tv is not None and sel != getattr(tv, "selected", None):
        tv.selected = sel
    entry = items[sel] if total else None
    sel_name = str(entry.get("name", "")) if entry else original

    left_w, right_w = split_panes(width, fallback=(28, 40))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 内容行（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → ``HookStateError``（视图渲染异常）。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=_S_WARN, group_style=_S_DESC,
                desc_style=_S_NAME, sep_style=_S_SEP,
            )
        return _palette_preview_rows(sel_name, right_w)

    content_rows = use_memo(_content_rows, (help_open, (sel_name, right_w)))
    window = content_rows[:max(1, vh)]

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _preview(row: int) -> None:
        if tv is None or not total:
            return
        row = max(0, min(int(row), total - 1))
        tv.selected = row
        name = str(items[row].get("name", ""))
        if name and apply_theme(name):
            tv.status_message = f"预览：{name}"
            for e in entries:
                if isinstance(e, dict):
                    e["active"] = (e.get("name") == name)

    def _handle(event) -> bool:
        if not visible or tv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, tv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            tv.search_mode = False
            tv.search_query = ""
            return True
        if verdict == "run":
            run_search(tv, entries, getattr(tv, "search_query", "") or "",
                       lambda e: f"{e.get('name', '')} {e.get('desc', '')}",
                       on_first=lambda i: _preview(_row_of(i)), label="主题")
            return True
        if verdict == "consume":
            return True

        if is_modal_close_key(event):
            if getattr(tv, "help_open", False):
                tv.help_open = False
                tv.cursor = 0
                tv.scroll = 0
                return True
            cur = str(items[sel].get("name", "")) if total else ""
            if original and cur and cur != original:
                apply_theme(original)
                tv.status_message = f"已恢复：{original}"
            tv.try_set_final("cancel")
            return True

        if event.kind == "char":
            if ch == "?":
                tv.help_open = not bool(tv.help_open)
                tv.cursor = 0
                tv.scroll = 0
                return True
            if ch == "/":
                tv.search_mode = True
                tv.search_query = getattr(tv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(tv, "search_pattern", "") or ""):
                jump_match(tv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _preview(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    tv.search_filter = not bool(tv.search_filter)
                    tv.status_message = "过滤开启" if tv.search_filter else "过滤关闭"
                else:
                    tv.status_message = "过滤需先搜索且有匹配"
                return True

        if event.kind == "enter" and total:
            _preview(sel)
            tv.status_message = f"已应用：{str(items[sel].get('name', ''))}"
            tv.try_set_final("done")
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        active = bool(e.get("active"))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(str(e.get("name", "")), _S_NAME),
            StyledRun("  " + str(e.get("desc", "")), _S_DESC),
        ]
        if active:
            runs.append(StyledRun("  \u2713", _S_OK))
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if is_sel:
            bg = _S_SEL_BG
        elif orig_idx in (set(matches) if (pattern and matches) else set()):
            bg = _S_MATCH_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"tv-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _preview(int(idx)),
        "focus": visible and not search_mode,
    })

    right_children: list = []
    for i, runs in enumerate(window):
        right_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"tv-f-{i}",
        }))

    if search_mode:
        hint = "  输入过滤词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择（即时预览）· Enter 确认 · Esc 恢复原主题 · ? 帮助"
    segs = [f" · {total} 个主题", f" · 原主题 {original}" if original else ""]
    header_runs = build_header_runs(
        "\u258d\U0001f3a8 主题选择器", _S_TITLE, segs, hint, width,
        hint_style=_S_HINT, sep_style=_S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "tv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), _S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    sruns = status_runs([], style=_S_STATUS, message=status_message,
                        message_style=_S_OK, width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "tv-status"}))
    if search_mode:
        q = getattr(tv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_PROMPT, "height": 1, "key": "tv-search",
        }))
    return h(Column, None, children)
