"""sandbox_common — 文件沙盒系列视图共享的样式与纯渲染辅助（2026-10）。

文件沙盒共有四个 TUI 视图，它们的左栏条目 / 右栏差异 / 统计区块渲染完全
同源：

  - ``changes``（``changes_view.py``）文件变更审查器；
  - ``sandbox``（``sandbox_stats_view.py``）沙盒概览；
  - ``sandbox_history``（``sandbox_history_view.py``）消息维度历史；
  - ``sandbox_records``（``sandbox_records_view.py``）变更记录流水。

本模块把这些**与具体视图无关**的样式常量与纯函数集中一处（单一真源），
避免四处复制漂移；各视图只负责自身的数据组织与键盘语义。

依赖约束：仅依赖 tui 核心样式 / ink 框架 / ``_ansi_runs``（Layer 0/1），
不依赖 core 层（数据由命令线程构建后经视图状态注入）。
"""

from __future__ import annotations

import time as _time

from src.tui.core.style import Style
from src.tui.ink import StyledRun
from src.tui.ink.helpers import wrap_runs_by_width

from ._ansi_runs import ansi_text_to_rows

__all__ = [
    "S_TITLE", "S_HINT", "S_SEP", "S_PATH", "S_TAG", "S_ADD", "S_DEL",
    "S_META", "S_SEL_BG", "S_SEL_MARK", "S_INSP_BG", "S_MATCH_BG",
    "S_MATCH_CUR_BG", "S_STATUS", "S_OK", "S_WARN", "S_PROMPT", "S_GROUP",
    "S_LABEL", "S_VALUE", "S_ERR",
    "MAX_DIFF_ROWS",
    "SORT_MODES", "TYPE_FILTERS", "sort_mode_label", "entry_sort_key",
    "fmt_time", "change_tag_style", "line_delta", "diff_rows",
    "history_rows", "message_detail_rows", "stats_rows", "stats_label_column",
]

#: 变更审查器排序模式（``s`` 循环）。
SORT_MODES = ("path", "records", "recent", "index")
_SORT_LABELS = {
    "path": "路径",
    "records": "修改次数",
    "recent": "最近修改",
    "index": "消息索引",
}
#: 变更类型过滤循环值（``T`` 循环）。
TYPE_FILTERS = ("", "新建", "修改", "删除", "目录")


def sort_mode_label(mode: str) -> str:
    """排序模式显示名。"""
    return _SORT_LABELS.get(mode, _SORT_LABELS["path"])


def entry_sort_key(entries: list, mode: str) -> list:
    """按排序模式返回排序后的文件变更条目列表（不修改入参）。"""
    items = list(entries or [])
    if mode == "records":
        return sorted(items, key=lambda e: (-int(e.get("records", 0) or 0), str(e.get("path", ""))))
    if mode == "recent":
        return sorted(items, key=lambda e: (-float(e.get("mtime", 0.0) or 0.0), str(e.get("path", ""))))
    if mode == "index":
        return sorted(items, key=lambda e: (int(e.get("last_index", 0) or 0), str(e.get("path", ""))))
    return sorted(items, key=lambda e: str(e.get("path", "")))

# ── 样式（静态色——浏览界面，不呼吸，diff 零输出） ──
S_TITLE = Style(fg=45, bold=True)
S_HINT = Style(fg=242)
S_SEP = Style(fg=238)
S_PATH = Style(fg=252)
S_TAG = Style(fg=110)
S_ADD = Style(fg=40)
S_DEL = Style(fg=196)
S_META = Style(fg=75)
S_SEL_BG = Style(bg=237)
S_SEL_MARK = Style(fg=45, bold=True)
S_INSP_BG = Style(bg=237)
S_MATCH_BG = Style(bg=236)
S_MATCH_CUR_BG = Style(bg=25)
S_STATUS = Style(fg=221)
S_OK = Style(fg=40, bold=True)
S_WARN = Style(fg=214, bold=True)
S_ERR = Style(fg=196, bold=True)
S_PROMPT = Style(fg=45, bold=True)
S_GROUP = Style(fg=110, bold=True)
S_LABEL = Style(fg=75)
S_VALUE = Style(fg=252)

#: 差异渲染最大行数（超长 diff 截断，避免巨型文件拖慢渲染）。
MAX_DIFF_ROWS = 4000
#: 单文件历史模式下每条记录 diff 的最大行数（多记录叠加时的预算控制）。
_MAX_HISTORY_DIFF_ROWS = 120


def fmt_time(ts) -> str:
    """时间戳 → ``HH:MM:SS``（异常回退空串）。"""
    try:
        return _time.strftime("%H:%M:%S", _time.localtime(float(ts)))
    except Exception:
        return ""


def change_tag_style(label: str) -> Style:
    """变更标签 → 着色（新建绿 / 删除红 / 无变化灰 / 修改默认）。"""
    text = str(label or "")
    if text.startswith("新建"):
        return Style(fg=40)
    if text.startswith("删除"):
        return Style(fg=196)
    if text.startswith("无变化"):
        return S_HINT
    return S_TAG


def _line_count(text) -> int:
    if not isinstance(text, str) or not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def line_delta(before, after) -> str:
    """前后内容行数变化摘要（``-3/+5``；新建 ``+N 行`` / 删除 ``-N 行``）。"""
    b, a = _line_count(before), _line_count(after)
    if before is None and after is not None:
        return f"+{a} 行"
    if before is not None and after is None:
        return f"-{b} 行"
    return f"-{b}/+{a}"


def diff_rows(path: str, before, after, width: int,
              max_rows: int = MAX_DIFF_ROWS) -> list:
    """差异内容行（``list[list[StyledRun]]``；超长截断）。

    内容相同 / 无差异渲染器可用时返回提示行。
    """
    rows: list = []
    if before == after:
        return [[StyledRun("(内容无变化)", S_HINT)]]
    from src.tui._diff_renderer import render_diff_to_ansi

    try:
        ansi = render_diff_to_ansi(path, before or "", after or "")
    except Exception:
        ansi = ""
    if not ansi:
        return [[StyledRun("(无差异可显示)", S_HINT)]]
    limit = max(1, int(max_rows))
    for runs in ansi_text_to_rows(ansi):
        if not runs:
            rows.append([StyledRun(" ", None)])
            continue
        for line in wrap_runs_by_width(runs, max(1, width)):
            rows.append(list(line.runs))
        if len(rows) > limit:
            rows = rows[:limit]
            rows.append([StyledRun("\u2026 差异过长，已截断", S_HINT)])
            break
    return rows


def _history_diff_rows(row: dict, width: int) -> list:
    """单条记录 → 差异行（带标题分隔行）。"""
    head = [
        StyledRun(f"  \u2500\u2500 #{row.get('seq', 0)} ", S_META),
        StyledRun(f"[{row.get('change_label', '')}] ", change_tag_style(row.get("change_label", ""))),
        StyledRun(str(row.get("tool", "") or "?"), S_LABEL),
        StyledRun(f" · 消息 {row.get('message_index', 0)}", S_HINT),
        StyledRun(f" · {fmt_time(row.get('time'))}", S_HINT),
    ]
    rows: list = []
    for line in wrap_runs_by_width(head, max(1, width)):
        rows.append(list(line.runs))
    rows.extend(diff_rows(
        str(row.get("path", "")), row.get("before"), row.get("after"),
        width, max_rows=_MAX_HISTORY_DIFF_ROWS,
    ))
    rows.append([StyledRun(" ", None)])
    return rows


def history_rows(entry: dict, width: int) -> list:
    """单文件历史时间线内容行（每条记录摘要 + 差异）。

    ``entry`` 为文件变更条目（``_sandbox_cmd.build_change_entries`` 产出，
    含 ``history`` 列表时优先使用）。
    """
    if not isinstance(entry, dict):
        return [[StyledRun("无选中文件", S_HINT)]]
    rows: list = []
    path = str(entry.get("path", ""))
    label = str(entry.get("change_label", ""))
    header = [
        StyledRun(path, S_TITLE),
        StyledRun(f"  {label}", change_tag_style(label)),
        StyledRun(f"  {entry.get('records', 0)} 次修改", S_META),
    ]
    for line in wrap_runs_by_width(header, max(1, width)):
        rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, width - 1), S_SEP)])
    history = entry.get("history") or []
    if not history:
        rows.append([StyledRun("(无历史记录)", S_HINT)])
        return rows
    for row in history:
        rows.extend(_history_diff_rows(row, width))
    return rows


def message_detail_rows(entry: dict, width: int) -> list:
    """消息条目 → 该消息下全部文件变更（逐文件摘要 + diff）。"""
    if not isinstance(entry, dict):
        return [[StyledRun("无选中消息", S_HINT)]]
    rows: list = []
    header = [
        StyledRun(f"消息 {entry.get('index', '')}", S_TITLE),
        StyledRun(f"  {entry.get('count', 0)} 次修改", S_META),
        StyledRun(f"  {entry.get('files', 0)} 个文件", S_META),
        StyledRun(f"  {fmt_time(entry.get('time'))}", S_META),
    ]
    for line in wrap_runs_by_width(header, max(1, width)):
        rows.append(list(line.runs))
    tools = entry.get("tools") or []
    if tools:
        runs = [StyledRun("工具: ", S_LABEL), StyledRun(" / ".join(str(t) for t in tools), S_TAG)]
        for line in wrap_runs_by_width(runs, max(1, width)):
            rows.append(list(line.runs))
    rows.append([StyledRun("\u2500" * max(1, width - 1), S_SEP)])
    for change in entry.get("changes") or []:
        path = str(change.get("path", ""))
        label = str(change.get("change_label", ""))
        head = [
            StyledRun(f"[{label}] ", change_tag_style(label)),
            StyledRun(path, S_PATH),
            StyledRun(f"  \u00b7 {change.get('tool', '')}", S_META),
        ]
        for line in wrap_runs_by_width(head, max(1, width)):
            rows.append(list(line.runs))
        rows.extend(diff_rows(path, change.get("before"), change.get("after"), width))
        rows.append([StyledRun(" ", None)])
        if len(rows) > MAX_DIFF_ROWS:
            rows = rows[:MAX_DIFF_ROWS]
            rows.append([StyledRun("\u2026 内容过长，已截断", S_HINT)])
            break
    return rows


def stats_label_column(sections: list) -> int:
    """统计区块的标签列宽（显示列，含值前间隔）。"""
    from src.tui._width import wcswidth_simple

    widest = 0
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("rows") or []:
            if isinstance(item, (list, tuple)) and item:
                widest = max(widest, wcswidth_simple(str(item[0])))
    return max(14, widest + 1)


def _item_fields(item):
    """统计条目 → ``(label, value, kind, ratio)``；非法条目返回 None。"""
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        return (
            item[0], item[1],
            item[2] if len(item) > 2 else "",
            item[3] if len(item) > 3 else None,
        )
    if isinstance(item, dict):
        return (
            item.get("label", ""), item.get("value", ""),
            item.get("kind", ""), item.get("bar"),
        )
    return None


def _value_style(kind: str) -> Style:
    if kind == "error":
        return S_ERR
    if kind == "warn":
        return S_WARN
    if kind == "ok":
        return S_OK
    return S_VALUE


def stats_rows(sections: list, width: int) -> list:
    """统计区块 → 内容行（``list[list[StyledRun]]``，对齐 usage 视图结构）。"""
    from ._view_common import pad_to_width

    label_col = stats_label_column(sections)
    rows: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", ""))
        prefix = f"\u25b8 {title} "
        pad = max(0, width - len(prefix) - 1) if width > 0 else 0
        rows.append([
            StyledRun(prefix, S_GROUP),
            StyledRun("\u2500" * pad, S_SEP),
        ])
        for item in sec.get("rows") or []:
            fields = _item_fields(item)
            if fields is None:
                continue
            label, value, kind, _ratio = fields
            runs = [
                StyledRun("  " + pad_to_width(label, label_col), S_LABEL),
                StyledRun(str(value), _value_style(str(kind))),
            ]
            if width > 0:
                rows.extend(list(line.runs) for line in wrap_runs_by_width(runs, width))
            else:
                rows.append(runs)
        rows.append([StyledRun(" ", None)])
    if not rows:
        rows.append([StyledRun("(暂无数据)", S_HINT)])
    return rows
