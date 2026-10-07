"""plugin_view — PluginView 插件视图组件（模态全屏视图，2026-10-04）。

/plugin 命令打开：App 在 ``model.fullscreen == "plugin"`` 时经全屏视图注册表
**整屏只渲染本组件**（消息区/顶部标题栏/状态栏/输入区全部不显示），关闭后
恢复完整聊天界面。视图只展示**当前内核已加载的插件**（运行时 Fiber）。

布局（React Ink 左右布局）：
  - 左栏「插件列表」：分类标题（不可选分隔行）+ 插件名（行尾分类标签 +
    警示标记），↑↓/jk/PgUp/PgDn/Home/End/g/G 上下选择（ListView 标准控件
    ——受控光标 + 虚拟滚动 + 选中整行高亮）；
  - 右栏「详细信息」：选中插件全部字段（分类/状态/来源/依赖/提供服务/
    配置/…，按栏宽换行；错误 / 缺失依赖 / 异常状态字段**警示色高亮**），
    焦点在右栏时 jk/↑↓/PgUp/PgDn/g/G 滚动 + 当前行背景高亮（vim cursorline
    语义，可在右栏查看超长详情）；``?`` 打开帮助面板（键位速查）覆盖右栏。

键盘：
  - 左栏：↑↓/jk 选择 · l/Enter 进入右栏详情 · g/G 首末 · Esc/Ctrl+H 关闭；
  - 右栏：jk/↑↓ 滚动 · g/G 首末 · PgUp/PgDn 翻页 · h 返回左栏 ·
    Esc/Ctrl+H 关闭；
  - 通用（2026-10-07 增强）：``/`` 搜索（回车执行，Esc 取消；``n``/``N``/``p``
    切换匹配）；``f`` 过滤模式（只显示匹配插件）；``?`` 帮助面板；
    ``y`` 复制选中插件信息到剪贴板（OSC52）。

数据源：``plugins.view_model.build_plugin_entries``（当前内核已加载插件——
运行时 Fiber；命令线程构建后注入 ``model.plugin_view.entries``；组件只读）。
依赖约束：仅依赖 app 同层（_state_types）与 ink 框架（Layer 0/1），无 tools
层反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.hooks import use_memo
from src.tui.ink.widgets.listview import ListView

from ._inspector_pane import PaneState, handle_nav, resolve
from ._keymap_pane import keymap_panel_rows
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from .plugin_export import write_export as write_plugin_export
from .plugin_relation import relation_rows

__all__ = [
    "PluginView", "_plugin_search_matches", "_build_display", "_cycle_filter",
    "_filter_allowed", "_state_options", "_kind_options",
    "_cycle_state_filter", "_cycle_kind_filter", "_do_export",
]

# ── 样式（静态色——浏览界面，不呼吸，diff 零输出） ──
_S_TITLE = Style(fg=45, bold=True)        # 视图标题/详情标题（亮青加粗）
_S_HINT = Style(fg=242)                    # 提示/弱化（暗灰）
_S_SEP_ROW = Style(fg=238)                 # 分隔线（深灰）
_S_NAME = Style(fg=252)                    # 插件名（亮白）
_S_TAG = Style(fg=110)                     # 分类标签（浅蓝）
_S_FIELD = Style(fg=75)                    # 详情字段名（浅紫蓝）
_S_VALUE = Style(fg=252)                   # 详情字段值（亮白）
_S_GROUP = Style(fg=110, bold=True)        # 左栏分类标题（浅蓝加粗）
_S_SEL_BG = Style(bg=237)                  # 选中行背景（静态 237）
_S_SEL_MARK = Style(fg=45, bold=True)      # 选中 ▶ 标记（亮青加粗）
_S_INSP_BG = Style(bg=237)                 # 右栏光标行背景
# 增强（2026-10-07）：告警 / 搜索高亮 / 状态提示
_S_ERR = Style(fg=196, bold=True)          # 错误字段/状态（红加粗）
_S_WARN = Style(fg=214, bold=True)         # 缺失依赖等警示（黄加粗）
_S_ALERT = Style(fg=214)                   # 左栏行尾警示标记（黄）
_S_SEARCH_BG = Style(bg=236)               # 搜索匹配行背景
_S_SEARCH_CUR_BG = Style(bg=25)            # 当前匹配合行背景（亮蓝）
_S_STATUS = Style(fg=221)                  # 底部状态行
_S_OK = Style(fg=40, bold=True)            # 成功反馈（绿）
_S_SEARCH_PROMPT = Style(fg=45, bold=True)  # 搜索输入行（亮青加粗）
_S_HELP_KEY = Style(fg=214)                # 帮助面板键位（黄）
_S_HELP_GROUP = Style(fg=110, bold=True)   # 帮助面板分组标题
_S_HELP_DESC = Style(fg=252)               # 帮助面板说明

#: 视图可见行预算预留（头部 1 行 + 底部余量）
_VIEWPORT_RESERVED = 2
#: 右栏详情内容行全量生成上限（超限截断 + 提示行）
_MAX_DETAIL_ROWS = 4000
#: 搜索输入长度上限（渲染行按宽度截断，无上限累积只浪费内存）
_SEARCH_QUERY_MAX = 200


def _viewport_rows() -> int:
    """视图可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache

        h_ = TerminalWidthCache.get_default().get_height()
        return max(8, int(h_) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _build_display(entries: list[dict], allowed: set | None = None) -> tuple[list, list]:
    """条目列表 → (display_items, specs)。

    ``display_items`` 供 ListView（``None`` 为不可选分类标题分隔行）；
    ``specs`` 与其逐项对齐（``("sep", 标题, 数量)`` / ``("entry", 条目, 索引)``）。

    Args:
        entries: 插件条目列表。
        allowed: 保留的条目索引集合（None/空 = 全部；过滤模式传匹配集合）。
            分类标题数量随之只统计保留条目。
    """
    from src.plugins.view_model import KIND_LABELS, KIND_ORDER

    by_kind: dict[str, list[tuple[int, dict]]] = {}
    for idx, entry in enumerate(entries or []):
        if allowed is not None and idx not in allowed:
            continue
        by_kind.setdefault(entry.get("kind", "kernel"), []).append((idx, entry))

    display_items: list = []
    specs: list = []
    ordered = list(KIND_ORDER) + [k for k in by_kind if k not in KIND_ORDER]
    for kind in ordered:
        items = by_kind.get(kind)
        if not items:
            continue
        display_items.append(None)
        specs.append(("sep", KIND_LABELS.get(kind, kind), len(items)))
        for idx, entry in items:
            display_items.append(entry)
            specs.append(("entry", entry, idx))
    return display_items, specs


def _field_style(label: str, value, levels: dict) -> Style:
    """详情字段值样式（按警示级别分色；正常字段用默认值样式）。"""
    level = levels.get(label) if isinstance(levels, dict) else None
    if level == "error":
        return _S_ERR
    if level == "warn":
        return _S_WARN
    return _S_VALUE


def _detail_rows(entry: dict, right_w: int) -> list:
    """选中条目 → 详情内容行（``list[list[StyledRun]]``；按栏宽换行）。"""
    rows: list = []
    if entry is None:
        return [[StyledRun("无已加载插件", _S_HINT)]]

    header = [
        StyledRun(str(entry.get("name", "")), _S_TITLE),
        StyledRun("  " + str(entry.get("kind_label", "")), _S_TAG),
    ]
    for line in wrap_runs_by_width(header, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), _S_SEP_ROW)])

    levels = entry.get("field_levels") or {}
    for item in entry.get("fields") or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        label, value = item[0], item[1]
        runs = [
            StyledRun(f"{label}: ", _S_FIELD),
            StyledRun(
                str(value) if value is not None else "(空)",
                _field_style(label, value, levels),
            ),
        ]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
        if len(rows) > _MAX_DETAIL_ROWS:
            rows = rows[:_MAX_DETAIL_ROWS]
            rows.append([StyledRun("\u2026 内容过长，已截断", _S_HINT)])
            break
    return rows


def _help_rows(right_w: int) -> list:
    """帮助面板内容行（插件视图键位速查）。"""
    from src.presentation_data import plugin_keymap

    return keymap_panel_rows(
        plugin_keymap(), right_w,
        key_style=_S_HELP_KEY, group_style=_S_HELP_GROUP,
        desc_style=_S_HELP_DESC, sep_style=_S_SEP_ROW,
        empty_text="(\u5feb\u6377\u952e\u901f\u67e5\u8868\u672a\u6ce8\u518c)",
    )


def _plugin_entry_text(entry: dict) -> str:
    """插件条目搜索文本（名称/分类/状态/字段值）。"""
    from src.plugins.view_model import plugin_search_text

    return plugin_search_text(entry)


def _plugin_search_matches(entries: list, pattern: str) -> list:
    """搜索匹配的条目索引列表（子串匹配，忽略大小写；空模式 → 空列表）。"""
    if not pattern:
        return []
    low = str(pattern).lower()
    out: list = []
    for i, entry in enumerate(entries or []):
        if low in _plugin_entry_text(entry).lower():
            out.append(i)
    return out


def _cycle_filter(pv, entries) -> None:
    """``f``：切换过滤模式（列表只显示搜索匹配插件）。"""
    new_value = not bool(getattr(pv, "search_filter", False))
    pattern = getattr(pv, "search_pattern", "") or ""
    matches = list(getattr(pv, "search_matches", None) or [])
    if new_value and not (pattern and matches):
        pv.search_filter = False
        pv.status_message = "过滤需先搜索且有匹配（/ 搜索）"
        return
    pv.search_filter = new_value
    pv.status_message = (
        f"过滤开启：仅显示 {len(matches)} 个匹配"
        if new_value else "过滤关闭：显示全部插件"
    )


# ═══════════════════════════════════════════════════════════
# 状态 / 分类过滤 + 依赖关系 + 导出（2026-10-07 第三批）
# ═══════════════════════════════════════════════════════════

#: 循环选项最小值（0 表示「全部」）
def _state_options(entries: list) -> list:
    """状态过滤循环选项（``""``（全部）+ 去重排序的条目状态）。"""
    states = sorted({
        str(e.get("subtitle", "") or "").strip()
        for e in (entries or []) if isinstance(e, dict)
        and str(e.get("subtitle", "") or "").strip()
    })
    return [""] + states


def _kind_options(entries: list) -> list:
    """分类过滤循环选项（``""``（全部）+ 去重排序的条目分类）。"""
    kinds = sorted({
        str(e.get("kind", "kernel") or "kernel")
        for e in (entries or []) if isinstance(e, dict)
    })
    return [""] + kinds


def _cycle_state_filter(pv, entries) -> str:
    """``S``：循环按状态过滤（全部 → 各状态 → 全部）。"""
    opts = _state_options(entries)
    cur = getattr(pv, "filter_state", "") or ""
    try:
        idx = opts.index(cur)
    except ValueError:
        idx = 0
    new = opts[(idx + 1) % len(opts)]
    pv.filter_state = new
    pv.status_message = f"状态过滤：{new or '全部'}"
    return new


def _cycle_kind_filter(pv, entries) -> str:
    """``K``：循环按分类过滤（全部 → 各分类 → 全部）。"""
    opts = _kind_options(entries)
    cur = getattr(pv, "filter_kind", "") or ""
    try:
        idx = opts.index(cur)
    except ValueError:
        idx = 0
    new = opts[(idx + 1) % len(opts)]
    pv.filter_kind = new
    pv.status_message = f"分类过滤：{new or '全部'}"
    return new


def _filter_allowed(entries: list, pattern: str, matches: list,
                    filter_on: bool, filter_state: str,
                    filter_kind: str) -> set | None:
    """组合过滤：搜索匹配 ∩ 状态 ∩ 分类 → 保留条目索引集合。

    ``None`` = 无任何过滤（显示全部；零成本快路径）。
    """
    active = bool(filter_on and pattern and matches)
    state = filter_state or ""
    kind = filter_kind or ""
    if not active and not state and not kind:
        return None
    allowed = set(range(len(entries or [])))
    if active:
        allowed &= {i for i in matches if 0 <= i < len(entries or [])}
    if state:
        allowed = {
            i for i in allowed
            if str(entries[i].get("subtitle", "") or "").strip() == state
        }
    if kind:
        allowed = {
            i for i in allowed
            if str(entries[i].get("kind", "kernel") or "kernel") == kind
        }
    return allowed


def _do_export(pv, entries, fmt: str) -> None:
    """``w``/``W``：导出插件清单为 Markdown / JSON 文件。"""
    items = [e for e in (entries or []) if isinstance(e, dict)]
    if not items:
        pv.status_message = "无可导出的插件"
        return
    try:
        path = write_plugin_export(items, fmt)
    except Exception as exc:  # 写盘失败（权限/磁盘）→ 状态提示，不崩溃
        pv.status_message = f"导出失败：{exc}"
        return
    label = "Markdown" if fmt == "md" else "JSON"
    pv.status_message = f"已导出 {len(items)} 个插件（{label}）→ {path}"


def _copy_entry(pv, entry) -> None:
    """``y``：复制选中插件信息到剪贴板（OSC52）。"""
    from src.plugins.view_model import format_plugin_entry_text

    if entry is None:
        pv.status_message = "无可复制的插件"
        return
    text = format_plugin_entry_text(entry)
    from src.tui._screen import set_clipboard
    if text and set_clipboard(text):
        pv.status_message = (
            f"已复制 {entry.get('name', '')}（{len(text)} 字符）到剪贴板"
        )
    else:
        pv.status_message = "复制失败：无可用终端输出"


def PluginView(props) -> object:
    """已加载插件视图组件（模态全屏视图；App 按 FULLSCREEN_VIEWS 整屏渲染）。

    Props:
        model: AppModel 实例（读 ``model.plugin_view`` / ``model.fullscreen``）。
        width: 终端宽度（左右栏宽分配）。
    """
    model = props["model"]
    width = props.get("width", 0) or 0
    pv = getattr(model, "plugin_view", None)
    visible = bool(pv is not None and pv.visible and not pv.done)
    entries = list(getattr(pv, "entries", None) or []) if pv is not None else []
    help_open = bool(getattr(pv, "help_open", False)) if pv is not None else False
    search_mode = bool(getattr(pv, "search_mode", False)) if pv is not None else False
    pattern = (getattr(pv, "search_pattern", "") or "") if pv is not None else ""
    filter_on = bool(getattr(pv, "search_filter", False)) if pv is not None else False
    matches = list(getattr(pv, "search_matches", None) or []) if pv is not None else []
    status_message = (getattr(pv, "status_message", "") or "") if pv is not None else ""

    # ── 统计（use_memo：条目集变化才重算） ──
    from src.plugins.view_model import collect_plugin_stats, format_plugin_stats

    stats = use_memo(
        lambda: collect_plugin_stats(entries),
        (id(entries), len(entries)),
    )
    stats_text = format_plugin_stats(stats)

    # ── 过滤视图（``f`` 搜索过滤 ∩ ``S`` 状态 ∩ ``K`` 分类） ──
    #   ★ 2026-10-07 第三批：组合过滤（搜索匹配 ∧ 状态 ∧ 分类）——allowed
    #   为保留条目索引集合（None = 无过滤）。
    filter_state = (getattr(pv, "filter_state", "") or "") if pv is not None else ""
    filter_kind = (getattr(pv, "filter_kind", "") or "") if pv is not None else ""
    relation_open = bool(getattr(pv, "relation_open", False)) if pv is not None else False
    allowed = _filter_allowed(
        entries, pattern, matches, filter_on, filter_state, filter_kind,
    )
    filter_active = bool(filter_on and pattern and matches)
    display_items, specs = _build_display(entries, allowed)
    total = len(display_items)
    # 条目索引 → 显示行下标（匹配定位用）
    entry_to_row: dict = {}
    for i, spec in enumerate(specs):
        if spec[0] == "entry":
            entry_to_row[spec[2]] = i

    # ── 选中钳制（分隔行不可选——落到最近可选项） ──
    try:
        sel = max(0, min(int(getattr(pv, "selected", 0) or 0), total - 1)) if total else 0
    except (TypeError, ValueError):
        sel = 0
    if total and display_items[sel] is None:
        nxt = sel + 1
        while nxt < total and display_items[nxt] is None:
            nxt += 1
        if nxt >= total:
            nxt = sel - 1
            while nxt >= 0 and display_items[nxt] is None:
                nxt -= 1
            nxt = nxt if nxt >= 0 else 0
        sel = nxt
    if pv is not None and sel != getattr(pv, "selected", None):
        pv.selected = sel

    entry = specs[sel][1] if (total and specs[sel][0] == "entry") else None

    pane = getattr(pv, "pane", "list") if pv is not None else "list"
    if pane not in ("list", "detail"):
        pane = "list"
    if help_open or relation_open:
        # 帮助 / 关系面板覆盖右栏 → 焦点视为详情（滚动导航走通用逻辑）
        pane = "detail"

    # ── 栏宽分配 ──
    if width > 0:
        left_w = max(18, int(width * 0.34))
        if width - left_w - 1 < 24:
            left_w = max(14, width - 25)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 30, 50

    # 底部行预算（状态行 / 搜索输入行各占一行）
    extra_rows = (1 if search_mode else 0) + (1 if status_message else 0)
    vh = max(4, _viewport_rows() - extra_rows)

    # ── 右栏内容（帮助 / 关系 / 详情）+ 光标/滚动协调 ──
    # ★ 2026-10-07 第三批（依赖关系视图）：``r`` 切换 → 右栏显示选中插件的
    #   依赖/被依赖/提供服务关系（``relation_rows``），Enter 跳转到相关插件。
    by_name = {str(e.get("name", "")): e for e in entries if isinstance(e, dict)}
    name_to_idx = {
        str(e.get("name", "")): i for i, e in enumerate(entries)
        if isinstance(e, dict)
    }
    rel_targets: list = []
    if help_open:
        content_rows = _help_rows(right_w)
    elif relation_open:
        content_rows, rel_targets = relation_rows(entry, by_name, right_w)
    else:
        content_rows = _detail_rows(entry, right_w)
    total_content = len(content_rows)
    content_vh = max(1, vh)
    try:
        cursor_raw = int(getattr(pv, "cursor", 0) or 0)
    except (TypeError, ValueError):
        cursor_raw = 0
    try:
        scroll_raw = int(getattr(pv, "scroll", 0) or 0)
    except (TypeError, ValueError):
        scroll_raw = 0
    # ★ P0-1：检查器（右栏）光标/滚动归一化统一走 ``_inspector_pane.resolve``
    #   （越界钳制 + 光标可见跟随）——取代本地复刻（与 trace_view /
    #   trace_tools_view 三份重复实现中的一份）。
    cursor, scroll = resolve(cursor_raw, scroll_raw, total_content, content_vh)
    if pv is not None:
        pv.cursor = cursor
        pv.scroll = scroll

    # 检查器面板状态规约（通用滚动/光标逻辑经 getter/setter 注入复用）
    _pane_state = PaneState(
        lambda: getattr(pv, "cursor", 0) or 0,
        lambda v: setattr(pv, "cursor", v),
        lambda: getattr(pv, "scroll", 0) or 0,
        lambda v: setattr(pv, "scroll", v),
    )

    # ── 搜索辅助（模块级逻辑，组件内薄封装） ──
    def _run_search() -> None:
        q = getattr(pv, "search_query", "") or ""
        pv.search_mode = False
        pv.search_pattern = q
        found = _plugin_search_matches(entries, q)
        pv.search_matches = found
        if found:
            pv.search_idx = 0
            row = entry_to_row.get(found[0])
            if row is not None:
                pv.selected = row
            pv.status_message = f"{len(found)} 个匹配"
        else:
            pv.search_idx = -1
            pv.status_message = f"无匹配：{q}" if q else ""

    def _jump_match(delta: int) -> None:
        found = list(getattr(pv, "search_matches", None) or [])
        if not found:
            return
        idx = getattr(pv, "search_idx", -1)
        n = len(found)
        if idx < 0:
            new_idx = 0 if delta > 0 else n - 1
        else:
            new_idx = (idx + delta) % n
        pv.search_idx = new_idx
        row = entry_to_row.get(found[new_idx])
        if row is not None:
            pv.selected = row
            pv.pane = "list"
            pv.cursor = 0
            pv.scroll = 0

    # ── 输入处理 ──
    def _handle(event) -> bool:
        if not visible or pv is None:
            return False
        pane_now = getattr(pv, "pane", "list") or "list"
        # ★ 2026-10-07（帮助面板）：帮助打开时焦点视为右栏（帮助内容滚动
        #   走通用导航；列表控件 focus=False 不抢按键）。
        if getattr(pv, "help_open", False):
            pane_now = "detail"
        ch = getattr(event, "char", "") or ""

        # ── 搜索输入模式（vim 风格：字符累积 / 退格 / 回车执行 / Esc 取消） ──
        if getattr(pv, "search_mode", False):
            if event.kind == "escape":
                pv.search_mode = False
                pv.search_query = ""
                return True
            if event.kind == "char":
                if ch and "\n" not in ch and "\r" not in ch:
                    q = getattr(pv, "search_query", "") or ""
                    if len(q) < _SEARCH_QUERY_MAX:
                        pv.search_query = q + ch
                return True
            if event.kind == "backspace":
                q = getattr(pv, "search_query", "") or ""
                if q:
                    pv.search_query = q[:-1]
                return True
            if event.kind == "enter":
                _run_search()
                return True
            return True
        # 模态统一关闭键（Esc / Ctrl+H）→ 帮助/关系面板/搜索态优先关闭
        if is_modal_close_key(event):
            if getattr(pv, "help_open", False):
                pv.help_open = False
                pv.pane = "list"
                pv.cursor = 0
                pv.scroll = 0
                pv.status_message = ""
                return True
            if getattr(pv, "relation_open", False):
                pv.relation_open = False
                pv.pane = "list"
                pv.cursor = 0
                pv.scroll = 0
                pv.status_message = ""
                return True
            pv.try_set_final("cancel")
            return True

        # ── 通用增强键（任意焦点） ──
        if event.kind == "char":
            if ch == "?":
                pv.help_open = not bool(getattr(pv, "help_open", False))
                # 帮助打开 → 焦点右栏（帮助内容滚动）；关闭 → 回列表。
                pv.pane = "detail" if pv.help_open else "list"
                pv.cursor = 0
                pv.scroll = 0
                pv.status_message = ""
                return True
            if ch == "/":
                pv.search_mode = True
                pv.search_query = getattr(pv, "search_pattern", "") or ""
                return True
            if ch in ("n", "N", "p") and (getattr(pv, "search_pattern", "") or ""):
                _jump_match(1 if ch == "n" else -1)
                return True
            if ch == "f":
                _cycle_filter(pv, entries)
                return True
            if ch == "y":
                _copy_entry(pv, entry)
                return True
            # ★ 2026-10-07 第三批：依赖关系视图 / 状态分类过滤 / 清单导出。
            if ch == "r":
                pv.relation_open = not bool(getattr(pv, "relation_open", False))
                pv.pane = "detail" if pv.relation_open else "list"
                pv.cursor = 0
                pv.scroll = 0
                pv.status_message = ""
                return True
            if ch == "S":
                _cycle_state_filter(pv, entries)
                pv.selected = 0
                pv.cursor = 0
                pv.scroll = 0
                return True
            if ch == "K":
                _cycle_kind_filter(pv, entries)
                pv.selected = 0
                pv.cursor = 0
                pv.scroll = 0
                return True
            if ch in ("w", "W"):
                _do_export(pv, entries, "md" if ch == "w" else "json")
                return True

        if pane_now == "list":
            # l / Enter → 进入右栏详情；其余放行（ListView 消费导航键）
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                pv.pane = "detail"
                pv.help_open = False
                return True
            return False

        # ── 右栏焦点 ──
        if event.kind == "char" and ch == "h":
            if getattr(pv, "help_open", False):
                pv.help_open = False
                return True
            if getattr(pv, "relation_open", False):
                pv.relation_open = False
                return True
            pv.pane = "list"
            return True
        # ★ 2026-10-07 第三批（依赖关系视图）：右栏 Enter → 跳转到当前光标
        #   行的关系目标插件（若已加载）；跳转后关闭关系面板、选中该插件。
        if event.kind == "enter" and getattr(pv, "relation_open", False):
            cur = getattr(pv, "cursor", 0) or 0
            tgt = rel_targets[cur] if 0 <= cur < len(rel_targets) else None
            if tgt:
                idx = name_to_idx.get(str(tgt))
                row = entry_to_row.get(idx) if idx is not None else None
                if row is not None:
                    pv.selected = row
                    pv.relation_open = False
                    pv.pane = "list"
                    pv.cursor = 0
                    pv.scroll = 0
                    pv.status_message = f"\u2192 {tgt}"
                    return True
            pv.status_message = "该关系项无法跳转"
            return True
        # ★ P0-1：通用 vim 导航（↑↓/j/k、PgUp/PgDn、Home/End、g/G）收敛到
        #   ``_inspector_pane.handle_nav``（三视图共享同一实现）。
        if handle_nav(event, _pane_state, total_content, content_vh):
            return True
        return False

    use_input(_handle, visible)
    # ★ 模态全屏视图声明：visible 期间未消费按键被 input router 吞掉（字符/
    #   Enter 不落入输入缓冲）；关闭后（visible=False）hook 不激活零影响。
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    # ── 左栏行渲染 ──
    matched_set = set(matches) if (pattern and matches) else set()
    cur_match_entry = -1
    if matched_set and 0 <= getattr(pv, "search_idx", -1) < len(matches):
        cur_match_entry = matches[pv.search_idx]

    def _render_left(item, idx, is_sel):
        spec = specs[idx] if 0 <= idx < len(specs) else ("sep", "", 0)
        if spec[0] == "sep":
            label = f"\u2500 {spec[1]} \u00b7 {spec[2]}"
            runs = [StyledRun(label, _S_GROUP)]
            return h(TEXT, {
                "styled": truncate_runs(runs, left_w) if left_w > 0 else runs,
                "height": 1, "key": f"pv-sep-{idx}",
            })
        ent = spec[1]
        ent_idx = spec[2]
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(str(ent.get("name", "")), _S_NAME),
            StyledRun("  " + str(ent.get("kind_label", "")), _S_TAG),
        ]
        alerts = ent.get("alerts") or []
        if alerts:
            runs.append(StyledRun(
                "  " + " ".join(f"\u26a0{a}" for a in alerts), _S_ALERT,
            ))
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        bg = None
        if ent_idx == cur_match_entry:
            bg = _S_SEARCH_CUR_BG
        elif ent_idx in matched_set:
            bg = _S_SEARCH_BG
        elif is_sel:
            bg = _S_SEL_BG
        if bg is not None:
            runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"pv-{idx}"})

    def _on_navigate(idx: int) -> None:
        if pv is None:
            return
        pv.selected = int(idx)
        pv.cursor = 0
        pv.scroll = 0
        pv.status_message = ""

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": _on_navigate,
        "focus": visible and pane == "list" and not search_mode,
    })

    # ── 右栏详情渲染 ──
    right_children: list = []
    if entry is None and not help_open:
        right_children.append(h(TEXT, {
            "children": "\u65e0\u5df2\u52a0\u8f7d\u63d2\u4ef6", "style": _S_HINT,
            "height": 1, "key": "pv-empty",
        }))
    else:
        window = content_rows[scroll:scroll + content_vh]
        for i, runs in enumerate(window):
            abs_idx = scroll + i
            is_cur = pane == "detail" and abs_idx == cursor
            if is_cur:
                runs = [
                    StyledRun(r.text, (r.style or Style()).merge(_S_INSP_BG))
                    for r in runs
                ]
            right_children.append(h(TEXT, {
                "styled": runs, "height": 1, "key": f"pv-f-{abs_idx}",
            }))

    # ── 头部（标题 + 统计 + 提示；行尾 ─ 分隔线填充至满宽） ──
    if search_mode:
        header_hint = "  \u8f93\u5165\u641c\u7d22\u8bcd \u00b7 Enter \u6267\u884c \u00b7 Esc \u53d6\u6d88"
    elif help_open:
        header_hint = "  \u5e2e\u52a9\u9762\u677f \u00b7 ? / q / Esc \u5173\u95ed"
    elif relation_open:
        header_hint = "  jk \u6eda\u52a8 \u00b7 Enter \u8df3\u8f6c \u00b7 r / Esc \u5173\u95ed"
    elif pane == "detail":
        header_hint = "  jk \u6eda\u52a8 \u00b7 h \u5217\u8868 \u00b7 r \u5173\u7cfb \u00b7 / \u641c\u7d22 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u5173\u95ed"
    else:
        header_hint = "  \u2191\u2193/jk \u9009\u62e9 \u00b7 l/\u21b5 \u8be6\u60c5 \u00b7 r \u5173\u7cfb \u00b7 / \u641c\u7d22 \u00b7 ? \u5e2e\u52a9 \u00b7 Esc \u5173\u95ed"
    count_text = f" \u00b7 {stats_text}"
    if filter_active:
        count_text += f" \u00b7 \u8fc7\u6ee4 {len(set(matches))}/{stats.get('total', 0)}"
    if entry is not None and total_content > content_vh:
        count_text += f" \u00b7 \u8be6\u60c5 {scroll + 1}-{scroll + len(window)}/{total_content}"
    header_runs = [
        StyledRun("\u258d\U0001f9e9 \u5df2\u52a0\u8f7d\u63d2\u4ef6", _S_TITLE),
        StyledRun(count_text, _S_HINT),
        StyledRun(header_hint, _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "pv-header"}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    # ── 底部状态行（搜索计数 + 操作反馈） ──
    status_parts: list = []
    if pattern:
        n = len(matches)
        idx = getattr(pv, "search_idx", -1)
        cur = (idx + 1) if 0 <= idx < n else 0
        seg = f"/{pattern}  {cur}/{n}"
        if filter_active:
            seg += " [\u8fc7\u6ee4]"
        status_parts.append(seg)
    # ★ 2026-10-07 第三批（状态 / 分类过滤）：过滤条件标注
    if filter_state:
        status_parts.append(f"\u72b6\u6001 {filter_state}")
    if filter_kind:
        status_parts.append(f"\u5206\u7c7b {filter_kind}")
    if status_message:
        status_parts.append(status_message)
    if status_parts:
        text = "  \u00b7  ".join(status_parts)
        if width > 0:
            text = text[:width]
        children.append(h(TEXT, {
            "children": text, "style": _S_OK if status_message else _S_STATUS,
            "height": 1, "key": "pv-status",
        }))
    # ── 底部搜索输入行 ──
    if search_mode:
        q = getattr(pv, "search_query", "") or ""
        if width > 0:
            q = q[: max(0, width - 2)]
        children.append(h(TEXT, {
            "children": f"/{q}\u258f", "style": _S_SEARCH_PROMPT,
            "height": 1, "key": "pv-search",
        }))
    return h(Column, None, children)
