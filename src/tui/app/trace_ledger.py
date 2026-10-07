"""轨迹台账渲染（从 trace_view 拆分）。

左栏「台账」的记录行/轮次分隔行构建：种类图标、状态图标、摘要、返回预览、
右对齐耗时、vim 搜索匹配高亮、宽截断，以及记录/行的搜索匹配计算。
纯函数 + 模块级缓存（跨帧命中返回同一 runs 引用）。样式取自
``trace_styles``。
"""

from __future__ import annotations

import re
import time as _time

from src.tui._format import format_duration
from src.tui.core.style import Style
from src.tui.ink import StyledRun
from src.tui.ink.helpers import truncate_runs

from .trace_styles import (
    _S_DIM,
    _S_ERROR,
    _S_HINT,
    _S_INDEX,
    _S_MARK,
    _S_SEARCH_BG,
    _S_SEARCH_CUR_BG,
    _S_SEL_BG,
    _S_SEL_MARK,
    _S_SEP_ROW,
    _S_TEXT,
    _S_TIME,
    _S_TIME_ABS,
    _S_TIME_BAR,
    _S_TIME_BAR_BG,
)

from src.presentation_data import (
    trace_kind_fg as _trace_kind_fg,
    trace_kind_icon as _trace_kind_icon,
    trace_kind_name as _trace_kind_name,
    trace_status_fg as _trace_status_fg,
    trace_status_icon as _trace_status_icon,
)

from .trace_types import TraceRecord

#: 台账可见行数预留（全屏模式仅轨迹头 1 行）
_VIEWPORT_RESERVED = 1

_LEDGER_RUNS_CACHE: dict = {}
_LEDGER_RUNS_CACHE_MAX = 256
_SEP_RUNS_CACHE: dict = {}


def _viewport_rows() -> int:
    """台账可见行数（终端高度自适应；无高度上下文回退 16）。"""
    try:
        from src.tui._screen import TerminalWidthCache
        h = TerminalWidthCache.get_default().get_height()
        return max(6, int(h) - _VIEWPORT_RESERVED)
    except Exception:
        return 16


def _kind_icon(kind: str) -> str:
    """记录种类 → 图标（表现层数据注册表；未知回退 ``·``）。"""
    return _trace_kind_icon(kind)


def _kind_name(kind: str) -> str:
    """记录种类 → 显示名（表现层数据注册表；未知回退种类名）。"""
    return _trace_kind_name(kind)


def _status_icon(status: str) -> str:
    """记录状态 → 图标（表现层数据注册表；未知回退 ``·``）。"""
    return _trace_status_icon(status)


def _kind_fg(kind: str) -> int:
    return _trace_kind_fg(kind)


def _status_fg(status: str) -> int:
    return _trace_status_fg(status)


def _rec_time_seconds(rec) -> float | None:
    """记录实时耗时（运行中按起始时间戳实时计算；其余用快照）。

    running 记录按 ``time_started`` 实时计算（整数秒入指纹，每秒刷新一次）；
    时间基准由 ``time_started_monotonic`` 决定（True=单调时钟）。
    """
    if getattr(rec, "status", "") != "running":
        return getattr(rec, "time_seconds", None)
    started = getattr(rec, "time_started", None)
    if started is None:
        return getattr(rec, "time_seconds", None)
    try:
        started_f = float(started)
    except (TypeError, ValueError):
        return getattr(rec, "time_seconds", None)
    if getattr(rec, "time_started_monotonic", True):
        return max(0.0, _time.monotonic() - started_f)
    return max(0.0, _time.time() - started_f)


def _record_search_text(rec) -> str:
    """记录全文（台账搜索匹配文本源）。"""
    parts: list = []
    for attr in ("summary", "result", "subagent_label"):
        v = getattr(rec, attr, None)
        if v:
            parts.append(str(v))
    lines = getattr(rec, "lines", None) or []
    for ln in lines:
        if isinstance(ln, str):
            parts.append(ln)
        else:
            plain = getattr(ln, "plain", None)
            parts.append(plain if plain is not None else str(ln))
    for attr in ("tool_args", "tool_result"):
        v = getattr(rec, attr, None)
        if v is not None:
            parts.append(str(v))
    return "\n".join(p for p in parts if p)


def _row_search_text(row) -> str:
    """内容行文本（检查器搜索匹配文本源）。"""
    if isinstance(row, str):
        return row
    if isinstance(row, (list, tuple)):
        return "".join(getattr(r, "text", "") or "" for r in row)
    return str(row)


def _trace_search_matches(pattern: str, side: str, records: list,
                          content_rows: list | None = None,
                          case_sensitive: bool = False) -> list:
    """正则搜索 → 匹配索引列表（side: ledger/inspector；非法正则 → 空）。

    ★ 2026-10-07（轨迹 Trace 搜索增强）：``case_sensitive`` 控制大小写——
    默认 False（忽略大小写，``re.IGNORECASE``）；True 时区分大小写（``v``
    键在轨迹视图内切换，切换后重跑当前搜索）。
    """
    if not pattern or side not in ("ledger", "inspector"):
        return []
    try:
        rx = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except Exception:
        return []
    matches: list = []
    if side == "ledger":
        for i, rec in enumerate(records):
            if rec is None:
                continue
            try:
                if rx.search(_record_search_text(rec)):
                    matches.append(i)
            except Exception:
                continue
    else:
        for i, row in enumerate(content_rows or []):
            try:
                if rx.search(_row_search_text(row)):
                    matches.append(i)
            except Exception:
                continue
    return matches


def _format_relative(seconds: float) -> str:
    """秒数 → 紧凑相对时间文本（``12s`` / ``3m`` / ``2h`` / ``1d``）。"""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h = m // 60
    if h < 24:
        return f"{h}h"
    return f"{h // 24}d"


#: 台账耗时条形图列数（2026-10-07 第三批；0=不显示）
TIME_BAR_WIDTH = 6


def _time_bar_fill(sec, max_sec, width: int = TIME_BAR_WIDTH) -> tuple:
    """耗时条填充预算：``(fill, width)``（按全表最大耗时归一化）。

    空耗时（None/非法/<=0）→ fill=0（条全为背景 ``░``，仍占位保持对齐）；
    max_sec<=0 → fill=0。有耗时但占比极小时至少填充 1 格（可见）；width<=0
    → ``(0, 0)``（不渲染）。纯函数，便于单测与跨行复用。
    """
    if width <= 0:
        return (0, 0)
    try:
        s = float(sec)
    except (TypeError, ValueError):
        return (0, width)
    try:
        m = float(max_sec)
    except (TypeError, ValueError):
        m = 0.0
    if s <= 0 or m <= 0:
        return (0, width)
    fill = int(round(s / m * width))
    return (max(1, min(fill, width)), width)


def _time_bar_max(records) -> float:
    """记录列表的最大耗时（秒；耗时条归一化真源；无 → 0.0）。"""
    best = 0.0
    for rec in records or []:
        if rec is None:
            continue
        sec = _rec_time_seconds(rec)
        if sec is None:
            continue
        try:
            v = float(sec)
        except (TypeError, ValueError):
            continue
        if v > best:
            best = v
    return best


def _rec_time_text(rec, mode: str) -> str:
    """记录时间戳文本（``abs``=HH:MM:SS / ``rel``=相对距今；无 epoch 时间戳 → ``""``）。

    仅使用**墙上时钟**时间戳（``time_started`` 且 ``time_started_monotonic``
    为 False）——单调时钟值无绝对时间语义，不显示。
    """
    if mode not in ("abs", "rel"):
        return ""
    started = getattr(rec, "time_started", None)
    if started is None or getattr(rec, "time_started_monotonic", True):
        return ""
    try:
        ts = float(started)
    except (TypeError, ValueError):
        return ""
    if mode == "rel":
        return _format_relative(max(0.0, _time.time() - ts))
    try:
        return _time.strftime("%H:%M:%S", _time.localtime(ts))
    except (ValueError, OSError, OverflowError):
        return ""


def _ledger_row_runs(rec, sel: bool, left_w: int,
                     matched: bool = False, cur_match: bool = False,
                     turn: int = 0, mark: str = "",
                     time_mode: str = "off",
                     time_bar: tuple | None = None) -> list:
    """台账行 runs（选中高亮 + ▶ 标记；耗时右对齐；宽截断；指纹缓存）。

    ★ 2026-10-07（轨迹 Trace 台账行增强）：
      - ``turn``（>0）→ ``#N`` 后显示轮次标记 ``tN``（提示记录所属轮次）；
      - ``subagent_label`` 非空 → 摘要前显示 ``↳`` 子代理标记（与合并的
        工具记录区分——Enter 可下钻）；
      - 失败记录（status ∈ fail/error）摘要以 ``_S_ERROR`` 红色高亮。
    三者均进入缓存键（不同轮次/子代理/状态不串缓存）。

    ★ 2026-10-07 第二批（标记 / 时间列）：
      - ``mark``（非空）→ ``#N`` 后显示标记字符 ``'a``（``_S_MARK`` 亮黄
        加粗，vim 书签语义）；
      - ``time_mode``（``abs``/``rel``）→ 行尾在耗时左侧显示记录时间戳
        （``HH:MM:SS`` / 相对距今；无 epoch 时间戳的记录不显示）。
    两者同样进入缓存键。

    ★ 2026-10-07 第三批（耗时条形图）：
      - ``time_bar``（``(fill, width)``）→ 行尾耗时左侧显示迷你占比条
        （``█``*fill + ``░``*(width-fill)）——直观对比各记录耗时长短
        （fill 由渲染器按全表最大耗时归一化预算，见 ``_time_bar_fill``）。
    进入缓存键。
    """
    t_raw = _rec_time_seconds(rec)
    t_key = int(t_raw) if t_raw is not None else None
    t_text = _rec_time_text(rec, time_mode)
    bar_key = (int(time_bar[0]), int(time_bar[1])) if time_bar else None
    key = (
        getattr(rec, "index", 0),
        getattr(rec, "kind", ""),
        getattr(rec, "summary", "") or "",
        getattr(rec, "status", "") or "",
        (getattr(rec, "result", "") or "")[:80],
        t_key,
        bool(sel),
        left_w,
        bool(matched),
        bool(cur_match),
        int(turn or 0),
        bool(getattr(rec, "subagent_label", "")),
        str(mark or ""),
        str(time_mode or "off"),
        t_text,
        bar_key,
    )
    cached = _LEDGER_RUNS_CACHE.get(key)
    if cached is not None:
        return cached
    runs: list = []
    if sel:
        runs.append(StyledRun("\u25b6 ", _S_SEL_MARK))
    else:
        runs.append(StyledRun("  ", None))
    runs.append(StyledRun(f"#{rec.index:>2} ", _S_INDEX))
    try:
        turn_no = int(turn or 0)
    except (TypeError, ValueError):
        turn_no = 0
    if turn_no > 0:
        runs.append(StyledRun(f"t{turn_no} ", _S_HINT))
    if mark:
        runs.append(StyledRun(f"'{mark} ", _S_MARK))
    if getattr(rec, "subagent_label", ""):
        runs.append(StyledRun("\u21b3 ", _S_HINT))
    kind = getattr(rec, "kind", "context")
    icon = _kind_icon(kind)
    runs.append(StyledRun(f"{icon} ", Style(fg=_kind_fg(kind))))
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _status_icon(status)
        runs.append(StyledRun(f"{sicon} ", Style(fg=_status_fg(status))))
    summary = getattr(rec, "summary", "") or "(空)"
    if status in ("fail", "error"):
        summary_style = _S_ERROR
    elif kind == "reasoning":
        summary_style = _S_DIM
    else:
        summary_style = _S_TEXT
    runs.append(StyledRun(summary, summary_style))
    result = getattr(rec, "result", "") or ""
    if result and left_w > 0:
        budget = max(8, left_w // 3)
        prev_runs = truncate_runs([StyledRun(result, _S_DIM)], budget)
        if prev_runs:
            runs.append(StyledRun(" \u00b7 ", _S_HINT))
            runs.extend(prev_runs)
    # ── 行尾右对齐区（耗时条 + 时间列 + 耗时） ──
    right_groups: list = []
    if time_bar and bar_key and bar_key[1] > 0:
        bw = bar_key[1]
        bf = max(0, min(bar_key[0], bw))
        right_groups.append([
            StyledRun("\u2588" * bf, _S_TIME_BAR),
            StyledRun("\u2591" * (bw - bf), _S_TIME_BAR_BG),
        ])
    if t_text:
        right_groups.append([StyledRun(t_text, _S_TIME_ABS)])
    t = ""
    if t_raw is not None:
        t = format_duration(t_raw)
    if t:
        right_groups.append([StyledRun(t, _S_TIME)])
    if right_groups and left_w > 0:
        used = sum(getattr(r, "width", 1) for r in runs)
        right_w = sum(
            sum(getattr(rr, "width", 1) for rr in g) for g in right_groups
        ) + (len(right_groups) - 1)
        pad = left_w - used - right_w - 1
        if pad > 0:
            runs.append(StyledRun(" " * pad, None))
        for i, g in enumerate(right_groups):
            if i:
                runs.append(StyledRun(" ", _S_HINT))
            runs.extend(g)
    if cur_match:
        bg = _S_SEARCH_CUR_BG
    elif matched:
        bg = _S_SEARCH_BG
    elif sel:
        bg = _S_SEL_BG
    else:
        bg = None
    if bg is not None:
        runs = [StyledRun(r.text, (r.style or Style()).merge(bg)) for r in runs]
    runs = truncate_runs(runs, left_w) if left_w > 0 else runs
    _LEDGER_RUNS_CACHE[key] = runs
    if len(_LEDGER_RUNS_CACHE) > _LEDGER_RUNS_CACHE_MAX:
        _LEDGER_RUNS_CACHE.clear()
    return runs


#: 记录行下标 → 轮次号映射缓存（与 ``_LEDGER_RUNS_CACHE`` 同模式：
#: rows 引用稳定（use_memo 命中）→ 跨帧 O(1) 查表）。
_ROW_TURN_CACHE: dict = {}


def _row_turn_map(rows: list) -> dict:
    """记录行下标 → 轮次号（1-based；首个轮次分隔行之前的记录为 0）。

    台账行渲染经此 O(1) 查表显示轮次标记（``#N t2 ⚡ …``）——一次 O(N)
    预计算，rows 引用稳定时跨帧命中零重建。

    ★ 2026-10-07 第二批（内联展开）：``_TraceExpandRow`` 等非记录行不入
    映射（只有 ``TraceRecord`` 记录行参与轮次统计）。
    """
    key = id(rows)
    entry = _ROW_TURN_CACHE.get(key)
    if entry is not None and entry[0] is rows:
        return entry[1]
    mapping: dict = {}
    turn = 0
    for i, r in enumerate(rows):
        if r is None or getattr(r, "_trace_turn_header", False):
            turn += 1
            mapping[i] = 0
        elif isinstance(r, TraceRecord):
            mapping[i] = turn
    if len(_ROW_TURN_CACHE) >= _LEDGER_RUNS_CACHE_MAX:
        _ROW_TURN_CACHE.clear()
    _ROW_TURN_CACHE[key] = (rows, mapping)
    return mapping


def _sep_row_runs(n: int, left_w: int) -> list:
    """轮次分隔行 runs（``── 轮次 N ──``，深灰；纯函数缓存）。"""
    key = (n, left_w)
    cached = _SEP_RUNS_CACHE.get(key)
    if cached is not None:
        return cached
    runs = [StyledRun(f"\u2500\u2500 轮次 {n} \u2500\u2500", _S_SEP_ROW)]
    runs = truncate_runs(runs, left_w) if left_w > 0 else runs
    _SEP_RUNS_CACHE[key] = runs
    if len(_SEP_RUNS_CACHE) > _LEDGER_RUNS_CACHE_MAX:
        _SEP_RUNS_CACHE.clear()
    return runs


__all__ = [
    "_LEDGER_RUNS_CACHE", "_LEDGER_RUNS_CACHE_MAX", "_SEP_RUNS_CACHE",
    "_ROW_TURN_CACHE", "_VIEWPORT_RESERVED", "_viewport_rows", "_kind_fg",
    "_status_fg", "_kind_icon", "_kind_name", "_status_icon",
    "_rec_time_seconds", "_record_search_text", "_row_search_text",
    "_trace_search_matches", "_ledger_row_runs", "_row_turn_map",
    "_sep_row_runs", "_rec_time_text", "_format_relative",
    "TIME_BAR_WIDTH", "_time_bar_fill", "_time_bar_max",
]
