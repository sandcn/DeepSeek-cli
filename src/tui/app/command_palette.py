"""command_palette — 命令面板（F2 开关的模态全屏视图）。

``model.fullscreen == "palette"`` 时由 App 整屏渲染本组件。目标是让所有斜杠
命令可被**模糊检索、快速执行**，同时保持键盘优先：

布局（左右双栏）：
  - 头部：标题 + 命令/匹配计数 + 操作提示 + ``─`` 填充；
  - 搜索行：``⌕`` + 输入缓冲（始终接收可打印字符，无需先按 ``/``）；
  - 左栏：命令列表（最近使用优先，模糊命中字符下划线高亮，选中行整行底色）；
  - 右栏：选中命令详情（分组 / 别名 / 说明 / 操作方式 / 同组命令）；
  - 底部：最近使用命令与草稿替换提示。

键盘：
  - 输入任意字符过滤（名称 / 别名 / 描述 / 分组，忽略大小写，子序列模糊匹配）；
  - ↑↓ 或 ``Ctrl+P`` / ``Ctrl+N`` 移动 · PgUp/PgDn 翻页 · Home/End 首末；
  - ``Enter`` 立即执行 · ``Tab`` 只填入输入框（便于补充参数）；
  - Backspace 删除 · ``Ctrl+W`` 删一词 · ``Ctrl+U`` 清空查询；
  - Esc / Ctrl+H / F2 关闭。

执行/填入经装配层注入的 ``model.palette_submit`` / ``model.palette_prefill``
交给输入层（与用户真实输入 Enter 的路径一致），视图自身不依赖 Input 实例。
"""

from __future__ import annotations

from typing import Optional

from src.tui.core.style import Style
from src.tui.ink import TEXT, Column, Row, StyledRun, h, use_input, use_state
from src.tui.ink.hooks import use_ref
from src.tui.ink.helpers import (
    truncate_runs,
    truncate_runs_ellipsis,
    wrap_runs_by_width,
)

from ._command_actions import recent_commands, run_command
from ._modal_view import empty_modal_frame, is_modal_close_key, use_modal_scope
from ._view_common import (
    build_header_runs,
    pane_divider,
    status_runs,
    split_panes,
    viewport_rows,
)

__all__ = [
    "CommandPalette",
    "_palette_entries",
    "_rank_entries",
    "_match_entry",
    "_fuzzy_positions",
    "_left_row",
    "_detail_rows",
]

#: 搜索缓冲上限（过滤是逐字符计算，超长查询无实际收益）。
QUERY_MAX = 160
#: 同组命令最多展示条数。
_SAME_GROUP_LIMIT = 6


def _palette_entries() -> list:
    """构建命令目录（分组 / 描述 / 别名；按分组顺序 + 名称排序）。

    数据源与 ``/help``、帮助速查视图同源（``_iter_help_commands``），额外
    追加 ``exit`` 伪命令。命令插件注册表未挂载时该函数自身回退旧命令表。
    """
    from src.core.internal.commands._command_core import (
        GROUP_LABELS,
        GROUP_ORDER,
        _iter_help_commands,
    )

    order = {name: idx for idx, name in enumerate(GROUP_ORDER)}
    entries: list = []
    seen: set = set()
    for group, name, desc, aliases in _iter_help_commands():
        clean = str(name or "").strip().lstrip("/")
        if not clean or clean in seen:
            continue
        seen.add(clean)
        entries.append({
            "name": clean,
            "aliases": tuple(str(a) for a in (aliases or ()) if a),
            "group": str(group or "general"),
            "group_label": GROUP_LABELS.get(str(group or "general"), str(group or "general")),
            "desc": str(desc or ""),
        })
    if "exit" not in seen:
        entries.append({
            "name": "exit",
            "aliases": (),
            "group": "general",
            "group_label": GROUP_LABELS.get("general", "其他"),
            "desc": "退出程序",
        })
    entries.sort(key=lambda e: (
        order.get(e["group"], len(order)),
        e["name"],
    ))
    return entries


def _fuzzy_positions(text: str, token: str) -> Optional[list]:
    """子序列匹配：返回 token 在 text 中依次命中的字符下标；无匹配返回 None。"""
    if not token:
        return []
    low_text = str(text or "").lower()
    low_token = str(token).lower()
    positions: list = []
    pos = 0
    for ch in low_token:
        idx = low_text.find(ch, pos)
        if idx < 0:
            return None
        positions.append(idx)
        pos = idx + 1
    return positions


def _score_positions(text: str, positions: list, token: str) -> float:
    """对子序列命中位置打分（前缀 / 词首 / 连续命中加分，长文本轻微扣分）。"""
    low_text = str(text or "").lower()
    low_token = str(token or "").lower()
    score = float(len(low_token) * 2)
    if low_text.startswith(low_token):
        score += 8.0
    if low_text == low_token:
        score += 16.0
    prev = -2
    for pos in positions:
        if pos == 0 or (pos > 0 and low_text[pos - 1] in " /_-."):
            score += 5.0
        if pos == prev + 1:
            score += 3.0
        prev = pos
    score -= min(len(low_text), 100) * 0.03
    return score


def _match_entry(entry: dict, query: str):
    """匹配单条命令：返回 ``(score, highlights)`` 或 None。

    ``highlights`` 为字段名 → 命中字符下标集合，用于渲染高亮：
    ``name``（含前导 ``/``）/ ``alias`` / ``desc`` / ``group``。
    """
    tokens = [tok for tok in str(query or "").lower().split() if tok]
    if not tokens:
        return 0.0, {}
    fields = (
        ("name", "/" + entry["name"], 3.2),
        ("alias", " ".join("/" + a for a in entry.get("aliases") or ()), 2.4),
        ("desc", entry.get("desc", ""), 1.2),
        ("group", entry.get("group_label", ""), 0.6),
    )
    total = 0.0
    highlights: dict = {}
    for token in tokens:
        best = None
        for field, text, weight in fields:
            if not text:
                continue
            positions = _fuzzy_positions(text, token)
            if positions is None:
                continue
            score = _score_positions(text, positions, token) * weight
            if best is None or score > best[0]:
                best = (score, field, positions)
        if best is None:
            return None
        total += best[0]
        highlights.setdefault(best[1], set()).update(best[2])
    return total, highlights


def _rank_entries(entries: list, query: str, recent: list) -> list:
    """过滤 + 排序命令，返回 ``[(entry, highlights), ...]``。

    空查询：最近使用命令按最近顺序置顶，其余保持分组/名称顺序。
    非空查询：按模糊得分降序；同分时最近使用优先，再按原顺序稳定排序。
    """
    recent_index = {name: idx for idx, name in enumerate(recent or [])}
    if not str(query or "").strip():
        recent_entries = []
        used = set()
        by_name = {entry["name"]: entry for entry in entries}
        for name in recent:
            entry = by_name.get(name)
            if entry is not None and name not in used:
                recent_entries.append((entry, {}))
                used.add(name)
        rest = [(entry, {}) for entry in entries if entry["name"] not in used]
        return recent_entries + rest

    scored = []
    for order, entry in enumerate(entries):
        matched = _match_entry(entry, query)
        if matched is None:
            continue
        score, highlights = matched
        scored.append((
            -score,
            recent_index.get(entry["name"], len(recent_index) + 1),
            order,
            entry,
            highlights,
        ))
    scored.sort(key=lambda item: item[:3])
    return [(item[3], item[4]) for item in scored]


def _fg(style, default: int) -> int:
    """从调色板 Style 取 int 前景色（TrueColor/None 回退默认）。"""
    value = getattr(style, "fg", None)
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _styles() -> dict:
    """当前主题下的面板样式（跟随配色主题实时变化）。"""
    from src.tui.core._theme import get_active_palette, time_glow

    try:
        pal = get_active_palette()
    except Exception:
        pal = None
    accent = _fg(getattr(pal, "accent", None), 45)
    selection_bg = getattr(getattr(pal, "selection_bg", None), "bg", None)
    if not isinstance(selection_bg, int) or isinstance(selection_bg, bool):
        selection_bg = 237
    return {
        "title": Style(fg=time_glow(accent, min(255, accent + 12), 8.0), bold=True),
        "accent": Style(fg=accent, bold=True),
        "name": getattr(pal, "text", None) or Style(fg=252),
        "desc": getattr(pal, "notice", None) or Style(fg=242),
        "group": getattr(pal, "time", None) or Style(fg=110),
        "recent": getattr(pal, "speed", None) or Style(fg=214),
        "hint": getattr(pal, "dim", None) or Style(fg=242),
        "sep": getattr(pal, "sep", None) or Style(fg=238),
        "match": Style(fg=accent, bold=True, underline=True),
        "sel_bg": Style(bg=selection_bg),
        "warn": Style(fg=214, bold=True),
        "ok": getattr(pal, "tool_ok", None) or Style(fg=40),
    }


def _highlight_field(text: str, positions, base_style, match_style) -> list:
    """把命中下标渲染为高亮 run（连续命中合并，保序拆分）。"""
    text = str(text or "")
    hits = set(positions or ())
    if not text or not hits:
        return [StyledRun(text, base_style)] if text else []
    runs: list = []
    start = 0
    idx = 0
    n = len(text)
    while idx < n:
        if idx in hits:
            if start < idx:
                runs.append(StyledRun(text[start:idx], base_style))
            end = idx + 1
            while end < n and end in hits:
                end += 1
            runs.append(StyledRun(text[idx:end], match_style))
            idx = end
            start = end
        else:
            idx += 1
    if start < n:
        runs.append(StyledRun(text[start:], base_style))
    return runs


def _left_row(entry: dict, highlights: dict, selected: bool, recent: bool,
              left_w: int, styles: dict) -> list:
    """左栏单行 runs（命令名 + 别名 + 描述；选中行整行底色并补满宽度）。"""
    marker_style = styles["accent"] if selected else styles["recent"]
    marker = "\u25b8 " if selected else ("\u2605 " if recent else "  ")
    runs: list = [StyledRun(marker, marker_style)]
    name_style = styles["accent"] if selected else styles["name"]
    runs.extend(_highlight_field(
        "/" + entry["name"], highlights.get("name"), name_style, styles["match"],
    ))
    aliases = entry.get("aliases") or ()
    if aliases:
        alias_text = " ".join("/" + a for a in aliases)
        runs.append(StyledRun("  ", None))
        runs.extend(_highlight_field(
            alias_text, highlights.get("alias"), styles["desc"], styles["match"],
        ))
    desc = entry.get("desc", "")
    if desc:
        runs.append(StyledRun("  ", None))
        runs.extend(_highlight_field(
            desc, highlights.get("desc"), styles["desc"], styles["match"],
        ))
    if left_w > 0:
        runs = truncate_runs(runs, left_w)
    if selected:
        selected_style = styles["sel_bg"]
        runs = [
            StyledRun(r.text, (r.style or Style()).merge(selected_style))
            for r in runs
        ]
        used = sum(getattr(r, "width", len(r.text)) for r in runs)
        if left_w > used:
            runs.append(StyledRun(" " * (left_w - used), selected_style))
    return runs


def _same_group(entries: list, entry: dict) -> list:
    """同组其他命令（最多 ``_SAME_GROUP_LIMIT`` 条，保持目录顺序）。"""
    group = entry.get("group")
    return [
        other for other in entries
        if other.get("group") == group and other.get("name") != entry.get("name")
    ][:_SAME_GROUP_LIMIT]


def _detail_rows(entry: Optional[dict], entries: list, right_w: int,
                 styles: dict, recent: bool = False,
                 has_draft: bool = False) -> list:
    """右栏详情行（分组 / 别名 / 说明 / 操作 / 同组命令）。"""
    if entry is None:
        return [[StyledRun("\u65e0\u5339\u914d\u547d\u4ee4", styles["hint"])]]
    rows: list = []
    title = [StyledRun("\u25c6 ", styles["accent"]), StyledRun("/" + entry["name"], styles["title"])]
    if recent:
        title.append(StyledRun("   \u2605 \u6700\u8fd1\u4f7f\u7528", styles["recent"]))
    for line in wrap_runs_by_width(title, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, right_w - 1), styles["sep"])])

    meta: list = [
        StyledRun("\u5206\u7ec4  ", styles["hint"]),
        StyledRun(str(entry.get("group_label", "")), styles["group"]),
    ]
    aliases = entry.get("aliases") or ()
    if aliases:
        meta.append(StyledRun("   \u522b\u540d  ", styles["hint"]))
        meta.append(StyledRun(" ".join("/" + a for a in aliases), styles["name"]))
    for line in wrap_runs_by_width(meta, max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])

    rows.append([StyledRun("\u8bf4\u660e", styles["accent"])])
    desc = entry.get("desc") or "\uff08\u65e0\u63cf\u8ff0\uff09"
    for line in wrap_runs_by_width([StyledRun(desc, styles["name"])], max(1, right_w)):
        rows.append(list(line.runs))
    rows.append([StyledRun(" ", None)])

    rows.append([StyledRun("\u64cd\u4f5c", styles["accent"])])
    action_hint = "Enter \u7acb\u5373\u6267\u884c \u00b7 Tab \u586b\u5165\u8f93\u5165\u6846\u540e\u53ef\u8865\u5145\u53c2\u6570"
    for line in wrap_runs_by_width([StyledRun(action_hint, styles["hint"])], max(1, right_w)):
        rows.append(list(line.runs))
    if has_draft:
        draft_hint = "\u5f53\u524d\u8f93\u5165\u6846\u6709\u8349\u7a3f\uff1a\u6267\u884c\u6216\u586b\u5165\u4f1a\u66ff\u6362\u5b83"
        for line in wrap_runs_by_width([StyledRun(draft_hint, styles["warn"])], max(1, right_w)):
            rows.append(list(line.runs))

    same = _same_group(entries, entry)
    if same:
        rows.append([StyledRun(" ", None)])
        rows.append([StyledRun("\u540c\u7ec4\u547d\u4ee4", styles["accent"])])
        for other in same:
            runs = [
                StyledRun("  /" + other["name"], styles["name"]),
                StyledRun("  " + str(other.get("desc", "")), styles["desc"]),
            ]
            if right_w > 0:
                runs = truncate_runs(runs, right_w)
            rows.append(runs)

    if right_w > 0:
        out: list = []
        for row in rows:
            out.append(truncate_runs(row, right_w) if row else [StyledRun(" ", None)])
        rows = out
    return rows


def _normalize_query_char(ch: str) -> str:
    """过滤输入字符：去掉换行/回车（粘贴多行不应把 Enter 带进查询）。"""
    return str(ch or "").replace("\r", "").replace("\n", "")


def CommandPalette(props) -> object:
    """命令面板组件（模态全屏视图；F2 开关）。"""
    model = props["model"]
    width = props.get("width", 0) or 0
    visible = (getattr(model, "fullscreen", "") or "") == "palette"

    query, set_query = use_state("")
    selected, set_selected = use_state(0)
    scroll, set_scroll = use_state(0)
    # ★ 同帧连续输入（快速打字/粘贴）修复：handler 闭包捕获的是**渲染时**
    #   state，多个按键在同一帧分发时后一键会用旧 query 覆盖前一键（实测
    #   ``type "th"`` 只保留 "h"）。ref 在 handler 内即时推进，state 只负责
    #   驱动渲染。
    query_ref = use_ref("")
    selected_ref = use_ref(0)
    scroll_ref = use_ref(0)

    styles = _styles()
    entries = _palette_entries()
    recent = recent_commands(model)
    rows = _rank_entries(entries, query, recent)
    total = len(rows)
    left_w, right_w = split_panes(width, left_ratio=0.46, min_left=24, min_right=28, fallback=(40, 50))
    vh = max(5, viewport_rows(reserved=5, minimum=5))

    cur = max(0, min(int(selected or 0), total - 1)) if total else 0
    max_scroll = max(0, total - vh)
    off = max(0, min(int(scroll or 0), max_scroll))
    if cur < off:
        off = cur
    elif cur >= off + vh:
        off = cur - vh + 1
    query_ref.current = query
    selected_ref.current = cur
    scroll_ref.current = off
    entry = rows[cur][0] if total else None
    highlights = rows[cur][1] if total else {}
    recent_set = set(recent)

    def _live_total() -> int:
        """按 ref 中的实时查询计算匹配总数（同帧查询变更后仍准确）。"""
        return len(_rank_entries(entries, query_ref.current, recent_commands(model)))

    def _move_to(new_index: int) -> None:
        live_total = _live_total()
        new_index = max(0, min(new_index, live_total - 1)) if live_total else 0
        selected_ref.current = new_index
        set_selected(new_index)
        current_off = int(scroll_ref.current or 0)
        if new_index < current_off:
            scroll_ref.current = new_index
            set_scroll(new_index)
        elif new_index >= current_off + vh:
            new_off = new_index - vh + 1
            scroll_ref.current = new_off
            set_scroll(new_off)

    def _reset_query(new_query: str) -> None:
        if len(new_query) > QUERY_MAX:
            return
        query_ref.current = new_query
        selected_ref.current = 0
        scroll_ref.current = 0
        set_query(new_query)
        set_selected(0)
        set_scroll(0)

    def _finish(submit: bool) -> None:
        # 同帧内可能刚改过查询（state 尚未渲染）——按 ref 的实时查询重新
        # 排序，确保 Enter/Tab 作用于用户眼前的最新结果。
        current_rows = _rank_entries(entries, query_ref.current, recent_commands(model))
        if not current_rows:
            return
        index = max(0, min(int(selected_ref.current or 0), len(current_rows) - 1))
        current_entry = current_rows[index][0]
        model.fullscreen = ""
        run_command(model, current_entry["name"], submit=submit)

    def _handle(event) -> bool:
        if not visible:
            return False
        kind = getattr(event, "kind", "")
        ch = getattr(event, "char", "") or ""
        if is_modal_close_key(event) or kind == "f2":
            model.fullscreen = ""
            return True
        if kind == "enter":
            _finish(True)
            return True
        if kind == "tab":
            _finish(False)
            return True
        if kind == "arrow_up":
            _move_to(int(selected_ref.current or 0) - 1)
            return True
        if kind == "arrow_down":
            _move_to(int(selected_ref.current or 0) + 1)
            return True
        if kind == "page_up":
            _move_to(int(selected_ref.current or 0) - vh)
            return True
        if kind == "page_down":
            _move_to(int(selected_ref.current or 0) + vh)
            return True
        if kind == "home":
            _move_to(0)
            return True
        if kind == "end":
            _move_to(_live_total() - 1)
            return True
        if kind == "backspace":
            parent = query_ref.current
            _reset_query(parent[:-1])
            return True
        if kind == "ctrl_key":
            parent = query_ref.current
            if ch == "\x0e":  # Ctrl+N
                _move_to(int(selected_ref.current or 0) + 1)
            elif ch == "\x10":  # Ctrl+P
                _move_to(int(selected_ref.current or 0) - 1)
            elif ch == "\x15":  # Ctrl+U
                _reset_query("")
            elif ch == "\x17":  # Ctrl+W
                stripped = parent.rstrip()
                cut = stripped.rfind(" ")
                _reset_query("" if cut <= 0 else stripped[:cut + 1])
            return True
        if kind == "char":
            text = _normalize_query_char(ch)
            if text:
                _reset_query(query_ref.current + text)
            return True
        return False

    use_input(_handle, visible)
    use_modal_scope(visible)

    if not visible:
        return empty_modal_frame()

    header_runs = build_header_runs(
        "\u258d\u547d\u4ee4\u9762\u677f", styles["title"],
        [f"  {len(entries)} \u547d\u4ee4 \u00b7 {total} \u5339\u914d"],
        "  Enter \u6267\u884c \u00b7 Tab \u586b\u5165 \u00b7 \u2191\u2193 \u9009\u62e9 \u00b7 Esc \u5173\u95ed",
        width, hint_style=styles["hint"], sep_style=styles["sep"],
    )

    search_runs = [
        StyledRun("  \u2315 ", styles["accent"]),
        StyledRun(query or "\u8f93\u5165\u4ee5\u7b5b\u9009\uff1a\u547d\u4ee4\u540d / \u522b\u540d / \u63cf\u8ff0 / \u5206\u7ec4", styles["name"] if query else styles["hint"]),
        StyledRun("\u258f", styles["accent"]),
        StyledRun("   \u652f\u6301\u5b50\u5e8f\u5217\u6a21\u7cca\u5339\u914d", styles["hint"]),
    ]
    if width > 0:
        search_runs = truncate_runs_ellipsis(search_runs, width)

    left_children: list = []
    window = rows[off:off + vh]
    for i, (item, hl) in enumerate(window):
        abs_idx = off + i
        runs = _left_row(
            item, hl, abs_idx == cur, item["name"] in recent_set,
            left_w, styles,
        )
        left_children.append(h(TEXT, {
            "styled": runs, "height": 1, "key": f"pal-{abs_idx}",
        }))
    if not left_children:
        left_children.append(h(TEXT, {
            "styled": [StyledRun("  \u65e0\u5339\u914d\u547d\u4ee4", styles["hint"])],
            "height": 1, "key": "pal-empty",
        }))

    detail = _detail_rows(
        entry, entries, right_w, styles,
        recent=bool(entry and entry["name"] in recent_set),
        has_draft=bool(getattr(model, "input_text", "")),
    )[:_visible_detail_lines(vh)]
    right_children = [
        h(TEXT, {"styled": line or [StyledRun(" ", None)], "height": 1, "key": f"pal-d-{i}"})
        for i, line in enumerate(detail)
    ]
    if not right_children:
        right_children.append(h(TEXT, {"styled": [StyledRun(" ", None)], "height": 1, "key": "pal-d-0"}))

    children: list = [
        h(TEXT, {"styled": header_runs, "height": 1, "key": "pal-header"}),
        h(TEXT, {"styled": search_runs, "height": 1, "key": "pal-search"}),
        h(Row, None, [
            h(Column, {"width": left_w}, left_children),
            pane_divider(max(vh, len(right_children)), styles["sep"]),
            h(Column, {"width": right_w}, right_children),
        ]),
    ]
    parts: list = []
    if recent:
        parts.append("\u6700\u8fd1 " + " ".join("/" + name for name in recent[:5]))
    if getattr(model, "input_text", ""):
        parts.append("\u5f53\u524d\u8349\u7a3f\u5c06\u88ab\u66ff\u6362")
    status = status_runs(
        parts, style=styles["hint"], width=width,
    )
    if status:
        children.append(h(TEXT, {"styled": status, "height": 1, "key": "pal-status"}))
    return h(Column, None, children)


def _visible_detail_lines(vh: int) -> int:
    """详情面板最大行数（与左栏视口一致；至少 1）。"""
    try:
        return max(1, int(vh))
    except (TypeError, ValueError, OverflowError):
        return 1
