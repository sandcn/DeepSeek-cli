"""plugin_view — PluginView 插件视图组件（模态全屏视图，2026-10-04）。

/plugin 命令打开：App 在 ``model.fullscreen == "plugin"`` 时经全屏视图注册表
**整屏只渲染本组件**（消息区/顶部标题栏/状态栏/输入区全部不显示），关闭后
恢复完整聊天界面。视图只展示**当前内核已加载的插件**（运行时 Fiber）。

布局（React Ink 左右布局）：
  - 左栏「插件列表」：分类标题（不可选分隔行）+ 插件名（行尾分类标签），
    ↑↓/jk/PgUp/PgDn/Home/End/g/G 上下选择（ListView 标准控件——受控光标 +
    虚拟滚动 + 选中整行高亮）；
  - 右栏「详细信息」：选中插件全部字段（分类/状态/来源/依赖/提供服务/
    配置/…，按栏宽换行），焦点在右栏时 jk/↑↓/PgUp/PgDn/g/G 滚动 +
    当前行背景高亮（vim cursorline 语义，可在右栏查看超长详情）。

键盘：
  - 左栏：↑↓/jk 选择 · l/Enter 进入右栏详情 · g/G 首末 · Esc/Ctrl+H 关闭；
  - 右栏：jk/↑↓ 滚动 · g/G 首末 · PgUp/PgDn 翻页 · h 返回左栏 ·
    Esc/Ctrl+H 关闭。

数据源：``plugins.view_model.build_plugin_entries``（当前内核已加载插件——
运行时 Fiber；命令线程构建后注入 ``model.plugin_view.entries``；组件只读）。
依赖约束：仅依赖 app 同层（_state_types）与 ink 框架（Layer 0/1），无 tools
层反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_fullscreen, use_input
from src.tui.ink.helpers import truncate_runs, wrap_runs_by_width
from src.tui.ink.widgets.listview import ListView

__all__ = ["PluginView"]

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

#: 视图可见行预算预留（头部 1 行 + 底部余量）
_VIEWPORT_RESERVED = 2
#: 右栏详情内容行全量生成上限（超限截断 + 提示行）
_MAX_DETAIL_ROWS = 4000


def _viewport_rows() -> int:
    """视图可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache

        h_ = TerminalWidthCache.get_default().get_height()
        return max(8, int(h_) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _build_display(entries: list[dict]) -> tuple[list, list]:
    """条目列表 → (display_items, specs)。

    ``display_items`` 供 ListView（``None`` 为不可选分类标题分隔行）；
    ``specs`` 与其逐项对齐（``("sep", 标题, 数量)`` / ``("entry", 条目)``）。
    """
    from src.plugins.view_model import KIND_LABELS, KIND_ORDER

    by_kind: dict[str, list[dict]] = {}
    for entry in entries:
        by_kind.setdefault(entry.get("kind", "kernel"), []).append(entry)

    display_items: list = []
    specs: list = []
    ordered = list(KIND_ORDER) + [k for k in by_kind if k not in KIND_ORDER]
    for kind in ordered:
        items = by_kind.get(kind)
        if not items:
            continue
        display_items.append(None)
        specs.append(("sep", KIND_LABELS.get(kind, kind), len(items)))
        for entry in items:
            display_items.append(entry)
            specs.append(("entry", entry))
    return display_items, specs


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

    for label, value in entry.get("fields") or []:
        runs = [
            StyledRun(f"{label}: ", _S_FIELD),
            StyledRun(str(value) if value is not None else "(空)", _S_VALUE),
        ]
        for line in wrap_runs_by_width(runs, max(1, right_w)):
            rows.append(list(line.runs))
        if len(rows) > _MAX_DETAIL_ROWS:
            rows = rows[:_MAX_DETAIL_ROWS]
            rows.append([StyledRun("\u2026 内容过长，已截断", _S_HINT)])
            break
    return rows


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

    display_items, specs = _build_display(entries)
    total = len(display_items)

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

    # ── 栏宽分配 ──
    if width > 0:
        left_w = max(18, int(width * 0.34))
        if width - left_w - 1 < 24:
            left_w = max(14, width - 25)
        right_w = max(1, width - left_w - 1)
    else:
        left_w, right_w = 30, 50

    vh = _viewport_rows()

    # ── 右栏内容 + 光标/滚动协调 ──
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
    cursor = max(0, min(cursor_raw, total_content - 1)) if total_content else 0
    if total_content > content_vh:
        scroll = max(0, min(scroll_raw, total_content - content_vh))
        if cursor < scroll:
            scroll = cursor
        elif cursor >= scroll + content_vh:
            scroll = cursor - content_vh + 1
    else:
        scroll = 0
    if pv is not None:
        pv.cursor = cursor
        pv.scroll = scroll

    def _scroll_for(cursor: int, scroll: int) -> int:
        if total_content <= content_vh:
            return 0
        scroll = max(0, min(int(scroll), total_content - content_vh))
        cursor = max(0, min(int(cursor), total_content - 1))
        if cursor < scroll:
            return cursor
        if cursor >= scroll + content_vh:
            return cursor - content_vh + 1
        return scroll

    def _move_cursor(new: int) -> None:
        if pv is None:
            return
        new = max(0, min(int(new), total_content - 1)) if total_content else 0
        pv.cursor = new
        pv.scroll = _scroll_for(new, getattr(pv, "scroll", 0) or 0)

    # ── 输入处理 ──
    def _handle(event) -> bool:
        if not visible or pv is None:
            return False
        pane_now = getattr(pv, "pane", "list") or "list"
        if event.kind == "escape":
            pv.try_set_final("cancel")
            return True
        if event.kind == "ctrl_key" and getattr(event, "char", "") == "\x08":
            pv.try_set_final("cancel")
            return True

        ch = getattr(event, "char", "") or ""
        if pane_now == "list":
            # l / Enter → 进入右栏详情；其余放行（ListView 消费导航键）
            if event.kind == "enter" or (event.kind == "char" and ch == "l"):
                pv.pane = "detail"
                return True
            return False

        # ── 右栏焦点 ──
        if event.kind == "char" and ch == "h":
            pv.pane = "list"
            return True
        cur = getattr(pv, "cursor", 0) or 0
        if event.kind == "arrow_down" or (event.kind == "char" and ch in ("j", "J")):
            _move_cursor(cur + 1)
            return True
        if event.kind == "arrow_up" or (event.kind == "char" and ch in ("k", "K")):
            _move_cursor(cur - 1)
            return True
        if event.kind == "page_down":
            _move_cursor(cur + content_vh)
            return True
        if event.kind == "page_up":
            _move_cursor(cur - content_vh)
            return True
        if event.kind == "home":
            _move_cursor(0)
            return True
        if event.kind == "end":
            _move_cursor(total_content)
            return True
        if event.kind == "char" and ch == "g":
            _move_cursor(0)
            return True
        if event.kind == "char" and ch == "G":
            _move_cursor(total_content)
            return True
        return False

    use_input(_handle, visible)
    # ★ 模态全屏视图声明：visible 期间未消费按键被 input router 吞掉（字符/
    #   Enter 不落入输入缓冲）；关闭后（visible=False）hook 不激活零影响。
    use_fullscreen(visible)

    if not visible:
        return h(TEXT, {"children": ""})

    # ── 左栏行渲染 ──
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
        runs = [
            StyledRun("\u25b6 " if is_sel else "  ", _S_SEL_MARK if is_sel else None),
            StyledRun(str(ent.get("name", "")), _S_NAME),
            StyledRun("  " + str(ent.get("kind_label", "")), _S_TAG),
        ]
        if left_w > 0:
            runs = truncate_runs(runs, left_w)
        if is_sel:
            runs = [StyledRun(r.text, (r.style or Style()).merge(_S_SEL_BG)) for r in runs]
        return h(TEXT, {"styled": runs, "height": 1, "key": f"pv-{idx}"})

    def _on_navigate(idx: int) -> None:
        if pv is None:
            return
        pv.selected = int(idx)
        pv.cursor = 0
        pv.scroll = 0

    ledger = h(ListView, {
        "items": display_items,
        "height": vh,
        "width": left_w,
        "cursor": sel if total else 0,
        "renderItem": _render_left,
        "onNavigate": _on_navigate,
        "focus": visible and pane == "list",
    })

    # ── 右栏详情渲染 ──
    right_children: list = []
    if entry is None:
        right_children.append(h(TEXT, {
            "children": "无已加载插件", "style": _S_HINT, "height": 1,
            "key": "pv-empty",
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
    if pane == "detail":
        header_hint = "\u2191\u2193/jk \u6eda\u52a8 \u00b7 h \u5217\u8868 \u00b7 g/G \u9996\u672b \u00b7 Esc \u5173\u95ed"
    else:
        header_hint = "\u2191\u2193/jk \u9009\u62e9 \u00b7 l/\u21b5 \u8be6\u60c5 \u00b7 g/G \u9996\u672b \u00b7 Esc \u5173\u95ed"
    count_text = f" \u00b7 \u5171 {len(entries)} \u4e2a"
    if entry is not None and total_content > content_vh:
        count_text += f" \u00b7 \u8be6\u60c5 {scroll + 1}-{scroll + len(window)}/{total_content}"
    header_runs = [
        StyledRun("\u258d\U0001f9e9 \u5df2\u52a0\u8f7d\u63d2\u4ef6", _S_TITLE),
        StyledRun(count_text, _S_HINT),
        StyledRun(f"  {header_hint}", _S_HINT),
    ]
    if width > 0:
        header_runs = truncate_runs(header_runs, width)
        used = sum(getattr(r, "width", 1) for r in header_runs)
        pad = width - used
        if pad > 0:
            header_runs.append(StyledRun("\u2500" * pad, _S_SEP_ROW))

    return h(Column, None, [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "pv-header"}),
        h(Row, None, [
            ledger,
            h(TEXT, {"children": "\u2502", "style": _S_SEP_ROW, "height": 1}),
            h(Column, {"width": right_w}, right_children),
        ]),
    ])
