"""theme_view — ThemeView 主题选择器视图（模态全屏视图，2026-10）。

``/theme`` 命令打开：App 在 ``model.fullscreen == "theme"`` 时经全屏视图注册
表**整屏只渲染本组件**，关闭后恢复完整聊天界面。

布局（React Ink 左右布局）：
  - 左栏「主题列表」：主题名 + 描述 + 当前标记（✓）；
  - 右栏「色板预览」：选中主题的语义色槽色块（accent/text/sep/…）——**实时
    预览**：↑↓ 移动即把该主题写入进程内配置并失效调色板缓存，全局即时
    生效；只有 Enter 确认时才持久化到 RC（Esc / 超时只回滚内存预览）。

键盘：
  - ↑↓/jk 选择（即时预览）· Enter 确认并关闭 · Esc 恢复打开时主题并关闭 ·
    g/G 首末 · / 搜索 · f 过滤 · n/N/p 匹配定位 · PgUp/PgDn 滚动 ·
    ? 帮助。

数据源：命令线程构建 ``model.theme_view.entries``（``ThemeRegistry`` 主题集 +
当前主题）；主题应用经 ``update_config`` + 调色板缓存失效（与 config_view
编辑 theme 同路径）。
"""

from __future__ import annotations

from src.tui._width import wcswidth_simple
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
    memo_key,
    pane_divider,
    run_search,
    split_panes,
    status_runs,
    viewport_rows,
)

__all__ = ["ThemeView", "_palette_preview_rows", "apply_theme", "_search_prompt"]

#: 帮助面板键字符集（半角 ``?`` + 全角 ``？``）——中文输入法下按 ``?`` 常得到
#: 全角问号（U+FF1F）。
_HELP_CHARS = ("?", "\uff1f")

# ── 兜底样式（活动 palette 不可用时的安全侧；正常路径见 _view_styles） ──
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

_FALLBACK_STYLES = {
    "title": _S_TITLE,
    "hint": _S_HINT,
    "sep": _S_SEP,
    "name": _S_NAME,
    "desc": _S_DESC,
    "ok": _S_OK,
    "sel_bg": _S_SEL_BG,
    "sel_mark": _S_SEL_MARK,
    "match_bg": _S_MATCH_BG,
    "match_cur_bg": _S_MATCH_CUR_BG,
    "status": _S_STATUS,
    "warn": _S_WARN,
    "prompt": _S_PROMPT,
    "slot": _S_SLOT,
}


def _view_styles() -> dict:
    """当前活动 palette 派生的视图样式（浅色/深色主题下均可读）。

    2026-10 修复：主题选择器此前使用固定 256 色（``fg=252`` / ``bg=237``
    等），切到 light 主题或浅色终端背景后未选中行接近白字浅底，可读性
    明显下降。主题选择器自身的 chrome 也应跟随活动 palette；取不到
    palette 时回退到原静态样式，保持极端环境可用。
    """
    try:
        from src.tui.core._theme import get_active_palette

        palette = get_active_palette()
    except Exception:
        return dict(_FALLBACK_STYLES)
    return {
        "title": palette.accent_bold,
        "hint": palette.dim,
        "sep": palette.sep,
        "name": palette.text,
        "desc": palette.dim,
        "ok": palette.tool_ok,
        "sel_bg": palette.selection_bg,
        "sel_mark": palette.accent_bold,
        # 匹配行用 inverse（终端默认前后景自动适配），当前匹配用选择背景。
        "match_bg": Style(inverse=True),
        "match_cur_bg": palette.selection_bg,
        "status": palette.token,
        "warn": palette.tool_fail,
        "prompt": palette.accent_bold,
        "slot": palette.time,
    }


_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择主题（即时预览）"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末主题"},
    {"group": "浏览", "keys": "PgUp/PgDn", "desc": "滚动右栏/帮助"},
    {"group": "操作", "keys": "Enter", "desc": "确认并关闭"},
    {"group": "操作", "keys": "Esc", "desc": "关闭帮助；列表中恢复原主题并关闭"},
    {"group": "搜索", "keys": "/", "desc": "搜索主题"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
]


def _theme_names() -> tuple[str, ...]:
    """当前注册表主题名（导入/读取异常返回空元组，调用方按安全侧处理）。"""
    try:
        from src.tui.core._theme import ThemeRegistry

        return tuple(ThemeRegistry.names())
    except Exception:
        return ()


def apply_theme(
    name: str, *, persist: bool = True, allow_unregistered: bool = False,
) -> bool:
    """应用主题：更新配置 + 失效调色板缓存（与 config_view 编辑 theme 同路径）。

    Args:
        persist: False 时只更新进程内配置（导航即时预览，避免每次按键写盘）；
            Enter 确认 / 命令切换时传 True 持久化到 RC。
        allow_unregistered: 允许写回注册表之外的名称。仅用于 **Esc / 超时恢复
            打开前主题**：配置里原本就是已禁用/已移除主题时，必须把内存值
            还原成该名称，才能让 palette 回退到打开前状态；普通切换/预览
            仍拒绝未注册主题。

    Returns:
        True — 配置更新成功（persist 时为持久化成功）；
        False — 主题名为空/未注册，或配置持久化失败。
    """
    if not name:
        return False
    names = _theme_names()
    if names and name not in names and not allow_unregistered:
        return False
    try:
        from src.config.loader import update_config

        written = update_config("theme", name, persist=persist)
        # 2026-10：update_config 现在返回 bool；写失败（只读 RC / 磁盘满）
        # 不能再被当成成功，否则视图会显示“已应用”但重启后丢失。
        if written is False:
            return False
    except Exception:
        # 预览 / 持久化都直接报错：config.set 兜底会再次走 update_config，
        # 既可能重复写盘，也会把写入失败（返回 False）误当成成功。
        return False
    try:
        from src.tui.core._theme import _invalidate_palette_cache

        _invalidate_palette_cache()
    except Exception:
        import logging

        logging.getLogger(__name__).debug("忽略异常（best-effort 路径）", exc_info=True)
    return True


def _valid_color(value):
    """Style.fg/bg 支持的合法颜色值（int 0-255 或 TrueColor），否则 None。"""
    from src.tui.core.color import TrueColor

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 255 else None
    if isinstance(value, TrueColor):
        return value
    return None


def _slot_color(style):
    """从调色板槽位 ``Style`` 取主色（fg 优先，fg 为空时回退 bg）。

    ``code_bg`` / ``selection_bg`` 等槽位只有背景色；修复前只读 fg 会把它们
    显示成 ``-``，用户无法预览代码背景/选中背景。
    """
    for attr in ("fg", "bg"):
        color = _valid_color(getattr(style, attr, None))
        if color is not None:
            return color
    return None


def _palette_preview_rows(name: str, right_w: int, styles: dict | None = None) -> list:
    """选中主题 → 色板预览行（每个语义槽一行：名称 + 色块）。"""
    from src.tui.core._theme import ThemeRegistry, _PALETTE_SLOTS

    st = styles or _view_styles()
    rows: list = []
    try:
        palette = ThemeRegistry.resolve(name)
    except Exception:
        # 插件提供的主题工厂异常时只让该主题的色块显示为“-”，不能让整个
        # 主题选择器渲染崩溃。
        palette = None
    rows.append([StyledRun(str(name), st["title"])])
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), st["sep"])])
    for slot in _PALETTE_SLOTS:
        style = getattr(palette, slot, None) if palette is not None else None
        color = _slot_color(style) if style is not None else None
        # █ 是实心块字符，终端用**前景色**绘制；只设 bg 会被实心块完全
        # 覆盖（所有色块显示成默认前景色，通常是白色）。色块必须设 fg。
        bar_style = Style(fg=color) if color is not None else Style(fg=238)
        rows.append([
            StyledRun(f"{slot:<16}", st["slot"]),
            StyledRun("  ", None),
            StyledRun("\u2588" * 8, bar_style),
            StyledRun("   " + (str(color) if color is not None else "-"), st["hint"]),
        ])
    wrapped: list = []
    for row in rows:
        if right_w > 0:
            wrapped.append(truncate_runs(row, right_w))
        else:
            wrapped.append(row)
    return wrapped


def _search_prompt(query: str, width: int) -> str:
    """搜索输入行（``/查询▏``）：超宽时保留尾部并用省略号提示。

    修复前直接渲染 ``f"/{q}▏"``，粘贴长查询（上限 200 字符）会超出终端宽度
    造成自动换行/布局漂移；这里按显示宽度裁剪并保留光标。
    """
    text = f"/{query}\u258f"
    if width <= 0 or wcswidth_simple(text) <= width:
        return text
    if width <= 2:
        return "/\u2026"[:width]
    # 预留 "/" 与光标列；被裁剪时再加一个省略号列。
    budget = max(0, width - 3)
    rev: list[str] = []
    used = 0
    for ch in reversed(str(query)):
        cw = wcswidth_simple(ch)
        if used + cw > budget:
            break
        rev.append(ch)
        used += cw
    tail = "".join(reversed(rev))
    return f"/\u2026{tail}\u258f"


def _clamp_int(value, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        number = low
    return max(low, min(number, high))


def _selected_index(tv, entries: list) -> int:
    """当前选择项在**完整 entries** 中的下标（统一坐标）。"""
    if not entries:
        return 0
    return _clamp_int(getattr(tv, "selected", 0) or 0, 0, len(entries) - 1)


def _filter_index_map(tv, entries: list) -> list[int]:
    """过滤后的「完整 entries 下标」列表（无有效过滤时返回全量）。"""
    total = len(entries)
    if not total:
        return []
    matches = [i for i in (getattr(tv, "search_matches", None) or [])
               if isinstance(i, int) and 0 <= i < total]
    search_filter = bool(getattr(tv, "search_filter", False))
    search_pattern = str(getattr(tv, "search_pattern", "") or "")
    if search_filter and search_pattern and matches:
        allowed = set(matches)
        return [i for i in range(total) if i in allowed]
    return list(range(total))


def _entry_name(entries: list, index: int) -> str:
    if not entries:
        return ""
    entry = entries[_clamp_int(index, 0, len(entries) - 1)]
    return str(entry.get("name", "")) if isinstance(entry, dict) else ""


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
    styles = _view_styles()

    # ── 选择坐标：selected 始终是完整 entries 下标；过滤只影响显示行 ──
    filter_active = bool(filter_on and pattern and matches)
    index_map = _filter_index_map(tv, entries) if tv is not None else list(range(len(entries)))
    items = [entries[i] for i in index_map]
    total = len(items)
    selected_index = _selected_index(tv, entries)
    if total:
        try:
            sel_row = index_map.index(selected_index)
        except ValueError:
            # 过滤后当前项不可见：显示首行；所有正常交互都会先同步选择。
            sel_row = 0
    else:
        sel_row = 0
    sel_name = _entry_name(entries, selected_index) if entries else original

    left_w, right_w = split_panes(width, fallback=(28, 40))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 内容行（**单一** use_memo：hook 调用必须无条件且数量恒定） ──
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=styles["warn"], group_style=styles["desc"],
                desc_style=styles["name"], sep_style=styles["sep"],
            )
        return _palette_preview_rows(sel_name, right_w, styles)

    content_rows = use_memo(
        _content_rows,
        (help_open, sel_name, right_w, memo_key(styles)),
    )
    scroll_attr = "help_scroll" if help_open else "scroll"
    scroll = _clamp_int(
        getattr(tv, scroll_attr, 0) if tv is not None else 0,
        0,
        max(0, len(content_rows) - vh),
    )
    if tv is not None and scroll != getattr(tv, scroll_attr, None):
        setattr(tv, scroll_attr, scroll)
    window = content_rows[scroll:scroll + max(1, vh)]

    # ── 当前搜索匹配（用于列表高亮） ──
    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(tv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[getattr(tv, "search_idx", -1)]

    def _row_entry(row) -> int | None:
        """当前渲染帧的 ListView 行号 → 完整 entries 下标。"""
        if not index_map:
            return None
        return index_map[_clamp_int(row, 0, len(index_map) - 1)]

    def _preview(index, *, persist: bool = False) -> bool:
        """应用一个**完整 entries 下标**对应的主题（默认仅内存即时预览）。"""
        if tv is None or not entries or index is None:
            return False
        index = _clamp_int(index, 0, len(entries) - 1)
        tv.selected = index
        name = _entry_name(entries, index)
        if not name:
            return False
        if not apply_theme(name, persist=persist):
            tv.status_message = f"应用失败：{name}"
            return False
        tv.status_message = f"预览：{name}"
        for e in entries:
            if isinstance(e, dict):
                e["active"] = (e.get("name") == name)
        return True

    def _scroll_by(delta: int) -> None:
        if tv is None:
            return
        attr = "help_scroll" if bool(getattr(tv, "help_open", False)) else "scroll"
        limit = max(0, len(content_rows) - vh)
        cur = _clamp_int(getattr(tv, attr, 0), 0, limit)
        setattr(tv, attr, _clamp_int(cur + delta, 0, limit))

    def _on_jump(index: int) -> None:
        _preview(index)

    def _handle(event) -> bool:
        if not visible or tv is None:
            return False
        ch = char_of(event)

        # ── 帮助面板是模态：除 ?/Esc 与翻页滚动外一律消费 ──
        if bool(getattr(tv, "help_open", False)):
            if is_modal_close_key(event) or (event.kind == "char" and ch in _HELP_CHARS):
                tv.help_open = False
                tv.cursor = 0
                tv.scroll = 0
                tv.help_scroll = 0
                return True
            if event.kind in ("page_up", "page_down"):
                _scroll_by((-1 if event.kind == "page_up" else 1) * max(1, vh - 1))
            return True

        # ── 搜索输入模式 ──
        verdict = handle_search_input(event, tv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            tv.search_mode = False
            tv.search_query = ""
            return True
        if verdict == "run":
            was_filter = bool(tv.search_filter)
            before = _selected_index(tv, entries) if entries else 0
            first_ok = {"ok": True}

            def _on_first(index: int) -> None:
                first_ok["ok"] = _preview(index)

            run_search(
                tv, entries, getattr(tv, "search_query", "") or "",
                lambda e: f"{e.get('name', '')} {e.get('desc', '')}",
                on_first=_on_first, label="主题",
            )
            if tv.search_matches and not first_ok["ok"]:
                # 首匹配应用失败：回到搜索前选择，避免“界面选中新项但实际
                # 主题仍是旧项”的静默不一致；过滤态也一并关闭。
                if entries:
                    tv.selected = before
                tv.search_filter = False
                tv.status_message = (
                    f"应用失败：{_entry_name(entries, tv.search_matches[0])}"
                )
                return True
            # 无匹配时过滤态必须复位，否则列表显示全量但 search_filter 仍为
            # True，f 无法关闭，后续搜索又会“意外”重新过滤。
            if not tv.search_matches:
                tv.search_filter = False
                if was_filter:
                    tv.status_message = f"{tv.status_message} · 过滤已关闭"
            return True
        if verdict == "consume":
            # 搜索输入中 Ctrl+H 也应能退出输入模式（Esc 已由 cancel 处理）。
            if is_modal_close_key(event):
                tv.search_mode = False
                tv.search_query = ""
            return True

        if is_modal_close_key(event):
            current = _entry_name(entries, _selected_index(tv, entries)) if entries else ""
            if original and current and current != original:
                # 预览只改内存；Esc 恢复原主题同样无需再次落盘（磁盘仍是原值）。
                # 原主题可能已被 Profile/Patch 禁用（不在注册表）——恢复路径
                # 允许写回该名称，否则用户会被困在选择器里无法取消。
                if not apply_theme(
                    original, persist=False, allow_unregistered=True,
                ):
                    tv.status_message = f"恢复失败：{original}"
                    return True
                tv.status_message = f"已恢复：{original}"
            tv.try_set_final("cancel")
            return True

        # 右栏 / 帮助内容滚动（页面未超出一屏时把 PageUp/PageDown 放给列表）。
        if event.kind in ("page_up", "page_down"):
            if len(content_rows) > vh:
                _scroll_by((-1 if event.kind == "page_up" else 1) * max(1, vh - 1))
                return True
            return False

        if event.kind == "char":
            if ch in _HELP_CHARS:
                tv.help_open = not bool(tv.help_open)
                tv.cursor = 0
                tv.scroll = 0
                tv.help_scroll = 0
                return True
            if ch == "/":
                tv.search_mode = True
                tv.search_query = getattr(tv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and bool(getattr(tv, "search_pattern", "")):
                jump_match(tv, 1 if ch == "n" else -1, on_jump=_on_jump)
                return True
            if ch == "f":
                live_matches = [
                    i for i in (getattr(tv, "search_matches", None) or [])
                    if isinstance(i, int) and 0 <= i < len(entries)
                ]
                if not live_matches:
                    if bool(tv.search_filter):
                        tv.search_filter = False
                        tv.status_message = "过滤已关闭（无匹配）"
                    else:
                        tv.status_message = "过滤需先搜索且有匹配"
                    return True
                if bool(tv.search_filter):
                    tv.search_filter = False
                    tv.status_message = "过滤关闭"
                    return True
                tv.search_filter = True
                selected_current = _selected_index(tv, entries)
                if selected_current not in live_matches:
                    applied = _preview(live_matches[0])
                    if not applied:
                        tv.selected = selected_current
                        tv.search_filter = False
                        tv.status_message = "过滤开启失败：应用主题失败"
                        return True
                tv.status_message = "过滤开启"
                return True

        if event.kind == "enter" and total:
            index = _selected_index(tv, entries)
            # Enter 是唯一持久化点：导航/搜索/过滤预览只改内存，避免每按一次
            # 方向键就整文件写 RC。
            if not _preview(index, persist=True):
                return True
            tv.status_message = f"已应用：{_entry_name(entries, index)}"
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
            StyledRun("\u25b6 " if is_sel else "  ", styles["sel_mark"] if is_sel else None),
            StyledRun(str(e.get("name", "")), styles["name"]),
        ]
        # ✓ 放在描述之前：描述较长时保留当前主题标记（修复前放在最后，
        # truncate_runs 会把 ✓ 整个裁掉）。
        if active:
            runs.append(StyledRun("  \u2713", styles["ok"]))
        runs.append(StyledRun("  " + str(e.get("desc", "")), styles["desc"]))
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if is_sel:
            bg = styles["sel_bg"]
        elif orig_idx == cur_match_entry:
            bg = styles["match_cur_bg"]
        elif orig_idx in matched_set:
            bg = styles["match_bg"]
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"tv-{idx}"})

    display_items = [{**e, "_index": index_map[idx]} for idx, e in enumerate(items)]

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel_row if total else 0,
        "renderItem": _render_left,
        "onNavigate": lambda idx: _preview(_row_entry(idx)),
        "focus": visible and not search_mode and not help_open,
    })

    right_children: list = []
    for i, runs in enumerate(window):
        right_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"tv-f-{i}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择（即时预览）· Enter 确认 · Esc 恢复原主题 · ? 帮助"
    segs = [f" · {len(entries)} 个主题"]
    if filter_active:
        segs.append(f" · 过滤 {total}")
    if original:
        segs.append(f" · 原主题 {original}")
    header_runs = build_header_runs(
        "\u258d\U0001f3a8 主题选择器", styles["title"], segs, hint, width,
        hint_style=styles["hint"], sep_style=styles["sep"],
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "tv-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), styles["sep"]),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    sruns = status_runs([], style=styles["status"], message=status_message,
                        message_style=styles["status"], width=width)
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "tv-status"}))
    if search_mode:
        q = getattr(tv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": _search_prompt(q, width), "style": styles["prompt"],
            "height": 1, "key": "tv-search",
        }))
    return h(Column, None, children)
