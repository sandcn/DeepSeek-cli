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
    _S_HINT,
    _S_INDEX,
    _S_SEARCH_BG,
    _S_SEARCH_CUR_BG,
    _S_SEL_BG,
    _S_SEL_MARK,
    _S_SEP_ROW,
    _S_TEXT,
    _S_TIME,
)

from src.presentation_data import (
    trace_kind_fg as _trace_kind_fg,
    trace_kind_icon as _trace_kind_icon,
    trace_kind_name as _trace_kind_name,
    trace_status_fg as _trace_status_fg,
    trace_status_icon as _trace_status_icon,
)

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
                          content_rows: list | None = None) -> list:
    """正则搜索 → 匹配索引列表（side: ledger/inspector；非法正则 → 空）。"""
    if not pattern or side not in ("ledger", "inspector"):
        return []
    try:
        rx = re.compile(pattern)
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


def _ledger_row_runs(rec, sel: bool, left_w: int,
                     matched: bool = False, cur_match: bool = False) -> list:
    """台账行 runs（选中高亮 + ▶ 标记；耗时右对齐；宽截断；指纹缓存）。"""
    t_raw = _rec_time_seconds(rec)
    t_key = int(t_raw) if t_raw is not None else None
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
    kind = getattr(rec, "kind", "context")
    icon = _kind_icon(kind)
    runs.append(StyledRun(f"{icon} ", Style(fg=_kind_fg(kind))))
    status = getattr(rec, "status", "") or ""
    if status:
        sicon = _status_icon(status)
        runs.append(StyledRun(f"{sicon} ", Style(fg=_status_fg(status))))
    summary = getattr(rec, "summary", "") or "(空)"
    runs.append(StyledRun(summary, _S_DIM if kind == "reasoning" else _S_TEXT))
    result = getattr(rec, "result", "") or ""
    if result and left_w > 0:
        budget = max(8, left_w // 3)
        prev_runs = truncate_runs([StyledRun(result, _S_DIM)], budget)
        if prev_runs:
            runs.append(StyledRun(" \u00b7 ", _S_HINT))
            runs.extend(prev_runs)
    t = ""
    if t_raw is not None:
        t = format_duration(t_raw)
    if t and left_w > 0:
        used = sum(getattr(r, "width", 1) for r in runs)
        pad = left_w - used - len(t) - 1
        if pad > 0:
            runs.append(StyledRun(" " * pad, None))
        runs.append(StyledRun(t, _S_TIME))
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
    "_VIEWPORT_RESERVED", "_viewport_rows", "_kind_fg", "_status_fg",
    "_kind_icon", "_kind_name", "_status_icon",
    "_rec_time_seconds", "_record_search_text", "_row_search_text",
    "_trace_search_matches", "_ledger_row_runs", "_sep_row_runs",
]
