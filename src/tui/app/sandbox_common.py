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
    "VIEW_STATE_ATTRS", "HELP_CHARS", "is_help_char", "as_int", "content_fingerprint",
    "record_signature", "message_signature", "file_entry_signature",
    "sections_signature",
    "reset_sandbox_view_state", "resync_search", "sync_search_matches",
    "change_search_text", "message_search_text", "record_search_text",
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

#: 沙盒视图族：视图 id → AppModel 上的状态属性名（视图族单一真源）。
VIEW_STATE_ATTRS = {
    "changes": "changes_view",
    "sandbox": "sandbox_view",
    "sandbox_history": "sandbox_history_view",
    "sandbox_records": "sandbox_records_view",
}

#: 帮助面板键字符集（半角 ``?`` + 全角 ``？``）——中文输入法下按 ``?`` 常得到
#: 全角问号（U+FF1F），模态沙盒视图内应同样打开帮助面板（搜索输入模式下仍作为
#: 搜索文本，不受影响）。
HELP_CHARS = ("?", "\uff1f")


def is_help_char(ch) -> bool:
    """帮助面板键判定（半角 / 全角问号）。"""
    return str(ch or "") in HELP_CHARS


def as_int(value, default: int = 0) -> int:
    """宽松整数转换（``None`` / 非法 → ``default``；**保留 0**）。

    ★ 修复（2026-10）：此前多处用 ``int(x or default)`` 表达式，``0`` 被
    ``or`` 吞掉——消息索引 0 是合法值（``restore_confirm`` 用 -1 表示「无待
    确认」，``0 or -1`` → -1），导致「消息 0 无法二次确认回滚」及确认高亮
    丢失。本函数显式区分「缺省」（None）与「值为 0」。
    """
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def content_fingerprint(value):
    """内容指纹（str 取 hash，其它取 id）——缓存依赖的廉价值键。"""
    if isinstance(value, str):
        return hash(value)
    return id(value)


def record_signature(row) -> tuple:
    """单条记录条目 → 缓存签名（``use_memo`` 值驱动依赖）。"""
    if not isinstance(row, dict):
        return (None,)
    return (
        str(row.get("path", "")),
        as_int(row.get("message_index"), -1),
        as_int(row.get("seq"), -1),
        str(row.get("tool", "")),
        str(row.get("change_label", "")),
        round(_as_float(row.get("time")), 6),
        round(_as_float(row.get("mtime")), 6),
        content_fingerprint(row.get("before")),
        content_fingerprint(row.get("after")),
    )


def message_signature(entry) -> tuple:
    """消息条目 → 缓存签名（含全部子记录签名，内容变化即失效）。"""
    if not isinstance(entry, dict):
        return (None,)
    return (
        as_int(entry.get("index"), -1),
        as_int(entry.get("count")),
        as_int(entry.get("files")),
        round(_as_float(entry.get("time")), 6),
        tuple(str(t) for t in (entry.get("tools") or [])),
        tuple(record_signature(c) for c in (entry.get("changes") or [])),
    )


def file_entry_signature(entry) -> tuple:
    """文件条目 → 缓存签名（右侧详情 / 预览 / 历史时间线的值驱动依赖）。

    覆盖全部展示字段（变更标签 / 修改次数 / 消息索引范围 / 工具列表 /
    首末内容指纹 / 历史条数）——修复前 ``_detail_deps`` 未含 ``tools``，
    ``_history_deps`` 只取历史条数（同长度内容变化不失效）。
    """
    if not isinstance(entry, dict):
        return (None,)
    history = entry.get("history") or []
    return (
        str(entry.get("path", "")),
        str(entry.get("change_label", "")),
        as_int(entry.get("records"), 0),
        as_int(entry.get("first_index"), -1),
        as_int(entry.get("last_index"), -1),
        round(_as_float(entry.get("mtime")), 6),
        bool(entry.get("reverted")),
        bool(entry.get("is_dir")),
        tuple(str(t) for t in (entry.get("tools") or [])),
        len(history),
        content_fingerprint(entry.get("before")),
        content_fingerprint(entry.get("after")),
    )


def sections_signature(sections) -> tuple:
    """统计区块 → 缓存签名（值驱动；替代不可靠的 ``id()`` 依赖）。

    ``id(obj)`` 作为缓存键不安全：旧对象被 GC 后新对象可能复用同一地址，
    依赖「未变」而命中陈旧缓存。本函数按区块标题 + 行的显示内容取值。
    """
    out: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        rows: list = []
        for item in sec.get("rows") or []:
            fields = _item_fields(item)
            rows.append(tuple(str(f) for f in fields) if fields else ())
        out.append((str(sec.get("title", "")), tuple(rows)))
    return tuple(out)


#: 视图重开 / 切换前的通用状态复位表（基类字段；``visible`` 单独处理）。
_BASE_STATE_RESETS = {
    "done": False,
    "action": "",
    "selected": 0,
    "cursor": 0,
    "scroll": 0,
    "pane": "list",
    "help_open": False,
    "help_scroll": 0,
    "search_mode": False,
    "search_query": "",
    "search_pattern": "",
    "search_idx": -1,
    "search_filter": False,
    "status_message": "",
}
#: 各视图的专有确认 / 输入态复位（键为状态属性名）。
_VIEW_STATE_EXTRA_RESETS = {
    "changes_view": {
        "revert_confirm": "", "revert_all_confirm": False,
        "restore_mode": False, "restore_value": "", "export_message": "",
    },
    "sandbox_view": {"clear_confirm": False},
    "sandbox_history_view": {"restore_confirm": -1},
    "sandbox_records_view": {"revert_confirm": ""},
}


def reset_sandbox_view_state(state, attr: str = "", *, visible: bool = False) -> None:
    """复位沙盒视图状态残留（重开 / 切换子视图前调用）。

    ★ 修复（2026-10）：概览视图按 ``h``/``l`` 打开子视图时此前只翻转
    ``model.fullscreen``——上一次关闭残留在状态上的 ``done=True`` 会让视图
    渲染判定（``visible and not done``）为假，**二次进入显示空白界面**；
    同批确认态（``restore_confirm`` / ``revert_confirm`` / ``clear_confirm``）
    与搜索态也一并复位，避免「上次的二次确认」误伤下一次操作。

    Args:
        state: 视图状态对象（``ListViewState`` 子类；None 时忽略）。
        attr: 状态属性名（``VIEW_STATE_ATTRS`` 的值；用于取专有复位表）。
        visible: 复位后的可见性（打开切换场景传 True）。
    """
    if state is None:
        return
    for name, value in _BASE_STATE_RESETS.items():
        try:
            setattr(state, name, value)
        except Exception:
            continue
    try:
        state.search_matches = []   # 可变默认值：每次赋新 list，避免共享
    except Exception:
        pass
    for name, value in (_VIEW_STATE_EXTRA_RESETS.get(attr) or {}).items():
        try:
            setattr(state, name, value)
        except Exception:
            continue
    try:
        state.visible = bool(visible)
    except Exception:
        pass


def resync_search(state, items, text_of, *, label: str = "匹配") -> int:
    """基准列表变化后重算搜索匹配索引（排序 / 视图模式 / 类型过滤切换）。

    ★ 修复（2026-10）：搜索匹配存的是**基准列表下标**，而排序模式 / 视图
    模式（文件 ↔ 消息）/ 类型过滤都会重建基准列表——旧实现只改模式不重算
    匹配，``f`` 过滤会显示「旧下标在新列表里对应的**另一个**条目」（实测：
    搜 ``b`` 切排序后过滤显示 ``c``）。本函数按新基准列表重算，并在过滤态
    失去全部匹配时自动关闭过滤（避免只剩空列表且无从退出）。

    Returns:
        新匹配条目数（无搜索模式时返回 0 且不触碰状态）。
    """
    pattern = str(getattr(state, "search_pattern", "") or "")
    if not pattern:
        return 0
    from ._view_common import find_matches

    found = list(find_matches(items, pattern, text_of))
    state.search_matches = found
    state.search_idx = 0 if found else -1
    if not found and bool(getattr(state, "search_filter", False)):
        state.search_filter = False
    return len(found)


def sync_search_matches(state, items, text_of) -> list:
    """渲染期同步搜索匹配（基准列表因**数据刷新**变化时自愈）。

    与 :func:`resync_search`（按键切换基准列表时调用）互补：沙盒视图打开
    期间数据会实时刷新（流式输出中工具继续写文件）——基准列表重建后旧匹配
    下标失效，``f`` 过滤与匹配高亮会指向错误条目。本函数在渲染期比对并重算
    （仅在结果变化时写回状态，无搜索模式时零开销）。

    Returns:
        当前有效的匹配索引列表（无搜索模式时原样返回状态值）。
    """
    pattern = str(getattr(state, "search_pattern", "") or "")
    if not pattern:
        return list(getattr(state, "search_matches", None) or [])
    from ._view_common import find_matches

    found = list(find_matches(items, pattern, text_of))
    if found != list(getattr(state, "search_matches", None) or []):
        state.search_matches = found
        if found:
            idx = as_int(getattr(state, "search_idx", -1), -1)
            state.search_idx = idx if 0 <= idx < len(found) else 0
        else:
            state.search_idx = -1
            if bool(getattr(state, "search_filter", False)):
                state.search_filter = False
    return found


def change_search_text(entry: dict) -> str:
    """文件条目搜索文本（路径 + 变更标签 + 工具）。"""
    if not isinstance(entry, dict):
        return ""
    tools = entry.get("tools") or []
    return " ".join([
        str(entry.get("path", "")),
        str(entry.get("change_label", "")),
        " ".join(str(t) for t in tools),
    ])


def message_search_text(entry: dict) -> str:
    """消息条目搜索文本（消息索引 + 文件路径 + 工具）。"""
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        f"消息 {entry.get('index', '')}",
        " ".join(str(p) for p in (entry.get("file_paths") or [])),
        " ".join(str(t) for t in (entry.get("tools") or [])),
    ])


def record_search_text(entry: dict) -> str:
    """记录条目搜索文本（路径 + 标签 + 工具 + 消息索引）。"""
    if not isinstance(entry, dict):
        return ""
    return " ".join([
        str(entry.get("path", "")),
        str(entry.get("change_label", "")),
        str(entry.get("tool", "")),
        f"消息 {entry.get('message_index', '')}",
    ])


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
    """前后内容行数变化摘要（``-3/+5``；新建 ``+N 行`` / 删除 ``-N 行``）。

    目录 / 无内容记录（前后均为 ``None``）显示 ``—``——修复前走最后分支
    输出 ``-0/+0``（误导为「内容未变」）。
    """
    if before is None and after is None:
        return "\u2014"
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
    """统计区块的标签列宽（显示列，含值前间隔）。

    ★ 修复（2026-10）：此前只统计 ``list/tuple`` 形式的条目——``dict`` 形式
    条目（``stats_rows`` / ``_item_fields`` 明确支持 ``label/value/kind/bar``）
    的标签宽度被忽略，长标签（CJK）区块列宽回退到 14 → 值起点错位。现统一
    经 :func:`_item_fields` 归一化取值。
    """
    from src.tui._width import wcswidth_simple

    widest = 0
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        for item in sec.get("rows") or []:
            fields = _item_fields(item)
            if fields is not None:
                widest = max(widest, wcswidth_simple(str(fields[0])))
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
    from src.tui._width import wcswidth_simple

    from ._view_common import pad_to_width

    label_col = stats_label_column(sections)
    rows: list = []
    for sec in sections or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", ""))
        prefix = f"\u25b8 {title} "
        # ★ 修复（2026-10）：分隔线填充此前按**字符数**（``len(prefix)``）计算，
        #   含 CJK 标题时实际显示宽度更大 → 分隔线超出 / 不足（行宽漂移）。
        #   改为按显示宽度扣减（与 ``stats_label_column`` / ``pad_to_width`` 同源）。
        prefix_w = wcswidth_simple(prefix)
        pad = max(0, width - prefix_w - 1) if width > 0 else 0
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
