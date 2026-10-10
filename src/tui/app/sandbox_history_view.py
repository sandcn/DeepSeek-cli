"""sandbox_history_view — SandboxHistoryView 沙盒消息维度历史视图（2026-10）。

``/sandbox history``（或沙盒概览视图按 ``h``）打开：App 在
``model.fullscreen == "sandbox_history"`` 时经全屏视图注册表**整屏只渲染本
组件**，关闭后恢复完整聊天界面。视图按**消息索引**浏览文件沙盒的变更历史
——每条消息索引一行（消息 N · K 次修改 · M 个文件 · 时间），右栏汇总该消息
下全部文件变更（逐文件 diff）；可按 ``R`` 回滚到该消息索引的文件状态。

布局（React Ink 左右布局）：
  - 左栏「消息列表」：``消息 N  K 次  M 文件  时间``，↑↓/jk 选择；
  - 右栏「变更详情」：该消息下全部文件变更（标签 + 路径 + 工具 + diff）。

键盘：
  - 左栏：↑↓/jk 选择 · Enter/l 查看 · g/G 首末；
  - 右栏：jk/↑↓ 滚动 · h 返回；
  - 操作：``R`` 回滚到该消息索引（再按确认）；
  - 通用：``/`` 搜索 · ``n``/``N``/``p`` 定位 · ``f`` 过滤 · ``?`` 帮助 ·
    Esc/Ctrl+H 关闭。

数据源：命令线程构建 ``model.sandbox_history_view.entries``
（``core.commands._sandbox_cmd.build_message_entries``）；回滚经 ``applied_seq``
回传命令线程执行；渲染期经 ``model.sandbox_refresher`` 实时刷新。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs
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
    S_MATCH_BG,
    S_MATCH_CUR_BG,
    S_META,
    S_OK,
    S_PROMPT,
    S_SEP,
    S_SEL_BG,
    S_SEL_MARK,
    S_STATUS,
    S_TAG,
    S_TITLE,
    S_WARN,
    as_int,
    fmt_time,
    is_help_char,
    message_detail_rows,
    message_search_text,
    message_signature,
    sync_search_matches,
)

__all__ = ["SandboxHistoryView", "_message_search_text", "_message_deps"]

_KEYMAP = [
    {"group": "浏览", "keys": "↑↓/jk", "desc": "选择消息"},
    {"group": "浏览", "keys": "l/Enter", "desc": "查看变更详情"},
    {"group": "浏览", "keys": "h", "desc": "返回消息列表"},
    {"group": "浏览", "keys": "g/G", "desc": "首/末"},
    {"group": "操作", "keys": "R", "desc": "回滚到该消息（再按确认）"},
    {"group": "搜索", "keys": "/", "desc": "搜索消息"},
    {"group": "搜索", "keys": "n/N/p", "desc": "下一/上一匹配"},
    {"group": "搜索", "keys": "f", "desc": "切换过滤"},
    {"group": "通用", "keys": "?", "desc": "帮助面板"},
    {"group": "通用", "keys": "Esc", "desc": "关闭视图"},
]


def _message_search_text(entry: dict) -> str:
    """消息条目搜索文本（消息索引 + 文件路径 + 工具）。

    单一真源在 ``sandbox_common.message_search_text``（本别名保持历史引用面，
    与 ``changes_view`` 共用同一实现，避免两处漂移）。
    """
    return message_search_text(entry)


def _message_deps(entry, right_w: int) -> tuple:
    """``message_detail_rows`` 的缓存依赖（值驱动，含子记录内容签名）。

    ★ 修复（2026-10）：此前依赖仅 ``(index, 变更条数, 右栏宽)``——同一消息
    下文件内容变化但条数不变（回滚同一文件、内容被再次修改等）时缓存不失效，
    右栏 diff 显示陈旧内容。
    """
    if not isinstance(entry, dict):
        return (None, right_w)
    return (message_signature(entry), right_w)


def SandboxHistoryView(props) -> object:
    """沙盒消息维度历史视图组件（模态全屏视图）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    hv = getattr(model, "sandbox_history_view", None)
    visible = bool(hv is not None and hv.visible and not hv.done)
    entries = list(getattr(hv, "entries", None) or []) if hv is not None else []
    help_open = bool(getattr(hv, "help_open", False)) if hv is not None else False
    search_mode = bool(getattr(hv, "search_mode", False)) if hv is not None else False
    pattern = (getattr(hv, "search_pattern", "") or "") if hv is not None else ""
    filter_on = bool(getattr(hv, "search_filter", False)) if hv is not None else False
    matches = list(getattr(hv, "search_matches", None) or []) if hv is not None else []
    status_message = (getattr(hv, "status_message", "") or "") if hv is not None else ""
    # ★ 修复（2026-10）：此前 ``int(... or -1)`` 在 ``restore_confirm == 0``
    #   （消息索引 0 是合法值）时被 ``or`` 吞成 -1 → 无法二次确认回滚到消息 0，
    #   左栏确认高亮同样丢失。``as_int`` 显式区分「无待确认 (-1)」与「0」。
    restore_confirm = as_int(getattr(hv, "restore_confirm", -1), -1) if hv is not None else -1

    allowed = None
    # ★ 修复（2026-10）：数据实时刷新会重建基准列表 → 旧匹配下标失效，渲染期
    #   同步一次（见 ``sandbox_common.sync_search_matches``）。
    matches = sync_search_matches(hv, entries, _message_search_text)
    filter_on = bool(getattr(hv, "search_filter", False)) if hv is not None else False
    if filter_on and pattern and matches:
        allowed = set(matches)
    index_map = [i for i in range(len(entries)) if allowed is None or i in allowed]
    items = [entries[i] for i in index_map]
    total = len(items)
    sel = 0
    if total:
        try:
            sel = max(0, min(int(getattr(hv, "selected", 0) or 0), total - 1))
        except (TypeError, ValueError):
            sel = 0
    if hv is not None and sel != getattr(hv, "selected", None):
        hv.selected = sel
    entry = items[sel] if total else None

    pane = getattr(hv, "pane", "list") if hv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open:
        pane = "detail"

    left_w, right_w = split_panes(width, fallback=(34, 48))
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, viewport_rows() - extra_rows)

    # ── 右栏内容（**单一** use_memo：hook 调用必须无条件且数量恒定）──
    # ★ 修复（2026-10）：修复前帮助面板分支跳过 ``use_memo``——按 ``?`` 打开
    #   帮助时 hook 序列变化 → 违反 Rules of Hooks（``HookStateError``）→
    #   视图渲染异常（「按 ? 没有帮助面板」）。现把 help_open 纳入依赖。
    def _content_rows() -> list:
        if help_open:
            from ._view_common import help_panel_rows

            return help_panel_rows(
                _KEYMAP, right_w, key_style=S_WARN, group_style=S_TAG,
                desc_style=S_TITLE, sep_style=S_SEP,
            )
        return message_detail_rows(entry, right_w)

    content_rows = use_memo(_content_rows, (help_open, _message_deps(entry, right_w)))
    total_content = len(content_rows)
    cursor, scroll = resolve(
        getattr(hv, "cursor", 0) or 0, getattr(hv, "scroll", 0) or 0,
        total_content, max(1, vh),
    )
    if hv is not None:
        hv.cursor = cursor
        hv.scroll = scroll

    _pane_state = PaneState(
        lambda: getattr(hv, "cursor", 0) or 0,
        lambda v: setattr(hv, "cursor", v),
        lambda: getattr(hv, "scroll", 0) or 0,
        lambda v: setattr(hv, "scroll", v),
    )

    def _row_of(orig: int) -> int:
        try:
            return index_map.index(orig)
        except ValueError:
            return 0

    def _select_row(row: int) -> None:
        if hv is None:
            return
        hv.selected = row
        hv.cursor = 0
        hv.scroll = 0
        hv.status_message = ""

    def _handle(event) -> bool:
        if not visible or hv is None:
            return False
        ch = char_of(event)

        verdict = handle_search_input(event, hv, max_len=SEARCH_QUERY_MAX)
        if verdict == "cancel":
            hv.search_mode = False
            hv.search_query = ""
            return True
        if verdict == "run":
            run_search(hv, entries, getattr(hv, "search_query", "") or "",
                       _message_search_text, on_first=lambda i: _select_row(_row_of(i)),
                       label="匹配")
            return True
        if verdict == "consume":
            return True

        # ★ F11 是沙盒视图族开关：在任一沙盒视图内按 F11 即关闭。
        if getattr(event, "kind", "") == "f11":
            close_fullscreen_view(model, hv, "sandbox_history")
            return True
        if is_modal_close_key(event):
            if getattr(hv, "help_open", False):
                hv.help_open = False
                hv.pane = "list"
                hv.cursor = 0
                hv.scroll = 0
                return True
            close_fullscreen_view(model, hv, "sandbox_history")
            return True

        if event.kind == "char":
            if is_help_char(ch):
                hv.help_open = not bool(hv.help_open)
                hv.pane = "detail" if hv.help_open else "list"
                hv.cursor = 0
                hv.scroll = 0
                return True
            if ch == "/":
                hv.search_mode = True
                hv.search_query = getattr(hv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(hv, "search_pattern", "") or ""):
                jump_match(hv, 1 if ch == "n" else -1,
                           on_jump=lambda i: _select_row(_row_of(i)))
                return True
            if ch == "f":
                if pattern and matches:
                    hv.search_filter = not bool(hv.search_filter)
                    hv.status_message = (
                        f"过滤开启：{len(matches)} 项" if hv.search_filter else "过滤关闭"
                    )
                else:
                    hv.status_message = "过滤需先搜索且有匹配"
                return True
            if ch == "R" and entry is not None:
                idx = as_int(entry.get("index"), 0)
                if restore_confirm == idx:
                    hv.applied = {"action": "restore-message", "index": idx}
                    hv.applied_seq += 1
                    hv.status_message = f"正在回滚到消息 {idx}…"
                    hv.restore_confirm = -1
                else:
                    hv.restore_confirm = idx
                    hv.status_message = f"再按 R 确认回滚到消息 {idx}"
                return True

        if pane == "list":
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                hv.pane = "detail"
                hv.help_open = False
                return True
            return False

        if event.kind == "char" and ch == "h":
            if getattr(hv, "help_open", False):
                hv.help_open = False
                return True
            hv.pane = "list"
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
    if matched_set and 0 <= getattr(hv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[hv.search_idx]

    def _render_left(item, idx, is_sel):
        e = item if isinstance(item, dict) else {}
        orig_idx = int(e.get("_index", idx))
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", S_SEL_MARK if is_sel else None),
            StyledRun(f"消息 {e.get('index', '')}", S_TITLE),
            StyledRun(f"  {e.get('count', 0)} 次修改", S_HINT),
            StyledRun(f"  {e.get('files', 0)} 个文件", S_TAG),
            StyledRun(f"  {fmt_time(e.get('time'))}", S_META),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        # ★ 修复（2026-10）：``int(x or -1)`` 会把消息索引 0 吞成 -1（与
        #   ``restore_confirm`` 读取同一类 bug）——用 ``as_int`` 显式取值。
        if restore_confirm >= 0 and as_int(e.get("index"), -1) == restore_confirm:
            bg = S_MATCH_CUR_BG
        elif orig_idx == cur_match_entry:
            bg = S_MATCH_CUR_BG
        elif orig_idx in matched_set:
            bg = S_MATCH_BG
        elif is_sel:
            bg = S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"sh-{idx}"})

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
            "styled": line_runs, "height": 1, "key": f"sh-f-{abs_idx}",
        }))

    if search_mode:
        hint = "  输入搜索词 · Enter 执行 · Esc 取消"
    elif help_open:
        hint = "  帮助面板 · ? / Esc 关闭"
    elif pane == "detail":
        hint = "  jk 滚动 · h 列表 · R 回滚到消息 · ? 帮助 · Esc 关闭"
    else:
        hint = "  ↑↓/jk 选择 · Enter 详情 · R 回滚到消息 · / 搜索 · ? 帮助 · Esc 关闭"
    # ★ 修复（2026-10）：此前分段为「{total} 条消息 · {len(entries)} 组」——
    #   两个数字语义重复且「组」含义不明；改为与流水视图一致的「命中/全部」。
    segs = [f" · {total}/{len(entries)} 组"]
    if filter_on and pattern:
        segs.append(f" · 过滤 {len(matches)}/{len(entries)}")
    header_runs = build_header_runs(
        "\u258d\U0001f553 沙盒历史（消息维度）", S_TITLE, segs, hint, width,
        hint_style=S_HINT, sep_style=S_SEP,
    )

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "sh-header"}),
        h(Row, None, [
            ledger,
            pane_divider(max(vh, len(right_children)), S_SEP),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(hv, "search_idx", -1)
        parts.append(f"/{pattern}  {(idx + 1) if 0 <= idx < n else 0}/{n}")
    sruns = status_runs(
        parts, style=S_STATUS, message=status_message,
        message_style=S_WARN if ("确认" in status_message or "失败" in status_message) else S_OK,
        width=width,
    )
    if sruns:
        children.append(h(TEXT, {"styled": sruns, "height": 1, "key": "sh-status"}))
    if search_mode:
        q = getattr(hv, "search_query", "") or ""
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": S_PROMPT, "height": 1, "key": "sh-search",
        }))
    return h(Column, None, children)
