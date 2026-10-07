"""轨迹统计概览（trace_stats）——头部统计条 + ``i`` 统计面板数据源。

2026-10-07（用户需求：轨迹 Trace 显示信息 / 更多功能）新增：
  - ``collect_trace_stats``：按记录列表汇总（条数/轮次/工具/失败/运行中/
    耗时/token/各工具排行/种类分布）——纯函数，供头部统计条与 ``i``
    统计面板共用（单一真源，避免两处统计口径漂移）；
  - ``format_summary``：头部单行统计文本（宽度由调用方截断）；
  - ``stats_panel_rows``：``i`` 面板全量内容行（滚动窗口数据源，行数据
    模型与检查器一致 = ``list[StyledRun]``）。

颜色/样式取自 ``trace_styles``（可经表现层数据表 Patch/Overlay 覆盖）；
耗时口径复用 ``trace_ledger._rec_time_seconds``（运行中记录实时计算——
与台账行耗时同一实现）。
"""

from __future__ import annotations

from src.tui._format import format_duration, format_tokens
from src.tui.ink import StyledRun

from .trace_ledger import _rec_time_seconds
from .trace_styles import (
    _S_DIM,
    _S_ERROR,
    _S_HINT,
    _S_SECTION,
    _S_SEP_ROW,
    _S_STATS_BAR,
    _S_STATS_LABEL,
    _S_STATS_VALUE,
    _S_WARN,
)

__all__ = [
    "collect_trace_stats", "format_summary", "stats_panel_rows",
    "_tool_display_name", "_EFFECTIVE_STATUS",
]

#: 「有效状态」归一（fail/error 视为失败；done 视为完成）。
_EFFECTIVE_STATUS = {"fail": "failed", "error": "failed", "done": "done"}

#: 统计面板各分组小节标题前缀（对齐检查器参数/返回值小节前缀）。
_SECTION_PREFIX = "\u25b8 "  # ▸

#: 工具耗时排行显示条数上限（面板空间有限；超出不显示，概览计数仍完整）。
_TOOL_RANK_LIMIT = 12
#: 排行条形图最大列数（按最大耗时归一化）。
_RANK_BAR_MAX = 16


def _tool_display_name(rec) -> str:
    """记录的工具显示名（``tool_name`` 优先，回退摘要首词，未知 → 工具）。"""
    name = (getattr(rec, "tool_name", "") or "").strip()
    if name:
        return name
    summary = (getattr(rec, "summary", "") or "").strip()
    if summary:
        first = summary.split()[0]
        if first:
            return first
    return "工具"


def collect_trace_stats(records) -> dict:
    """记录列表 → 统计汇总（纯函数，无副作用）。

    Returns:
        dict：
          - ``total``：记录总数（含工具列表记录）；
          - ``turns``：轮次数（``kind == "user"`` 记录数）；
          - ``tools`` / ``tools_done`` / ``tools_failed`` / ``tools_running``：
            工具调用计数（按状态归类）；
          - ``errors``：失败记录数（status ∈ fail/error，含工具与子代理）；
          - ``running``：运行中记录数；
          - ``elapsed``：非运行中记录耗时之和（秒；运行中实时值不计入
            汇总以避免抖动，另经 ``running_elapsed`` 单列）；
          - ``running_elapsed``：运行中记录实时耗时之和（秒）；
          - ``tokens_in`` / ``tokens_out`` / ``tokens_cache``：token 汇总；
          - ``success_rate``：工具成功率（无已完成工具时为 None）；
          - ``kinds``：{kind: count}；
          - ``tool_ranking``：[(工具名, 次数, 总耗时, 失败数)] 按总耗时降序。
    """
    stats = {
        "total": 0,
        "turns": 0,
        "tools": 0,
        "tools_done": 0,
        "tools_failed": 0,
        "tools_running": 0,
        "errors": 0,
        "running": 0,
        "elapsed": 0.0,
        "running_elapsed": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "tokens_cache": 0,
        "success_rate": None,
        "kinds": {},
        "tool_ranking": [],
    }
    if not records:
        return stats
    kind_counts: dict = {}
    tool_agg: dict = {}
    for rec in records:
        if rec is None:
            continue
        kind = getattr(rec, "kind", "") or "context"
        stats["total"] += 1
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
        if kind == "user":
            stats["turns"] += 1
        status = (getattr(rec, "status", "") or "").lower()
        effective = _EFFECTIVE_STATUS.get(status, status)
        if effective == "failed":
            stats["errors"] += 1
        if status == "running":
            stats["running"] += 1
        sec = _rec_time_seconds(rec)
        sec = float(sec) if sec is not None else 0.0
        if status == "running":
            stats["running_elapsed"] += sec
        else:
            stats["elapsed"] += sec
        tokens = getattr(rec, "tokens", None) or {}
        if isinstance(tokens, dict):
            stats["tokens_in"] += _safe_int(tokens.get("input"))
            stats["tokens_out"] += _safe_int(tokens.get("output"))
            stats["tokens_cache"] += _safe_int(tokens.get("cache"))
        if kind == "tool":
            stats["tools"] += 1
            name = _tool_display_name(rec)
            entry = tool_agg.get(name)
            if entry is None:
                entry = [0, 0.0, 0, 0]
                tool_agg[name] = entry
            entry[0] += 1
            entry[1] += sec
            if effective == "failed":
                entry[2] += 1
                stats["tools_failed"] += 1
            elif status == "running":
                entry[3] += 1
                stats["tools_running"] += 1
            else:
                stats["tools_done"] += 1
    stats["kinds"] = kind_counts
    finished = stats["tools_done"] + stats["tools_failed"]
    if finished > 0:
        stats["success_rate"] = stats["tools_done"] / finished
    stats["tool_ranking"] = sorted(
        ((name, cnt, sec, fails) for name, (cnt, sec, fails, _run) in tool_agg.items()),
        key=lambda item: (-item[2], -item[1], item[0]),
    )
    return stats


def _safe_int(v) -> int:
    """int 归一化（异常注入值回退 0；与 trace_view._safe_int 同语义）。"""
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return 0


def format_summary(stats: dict, sel_pos: int = 0, total: int = 0) -> str:
    """头部统计条单行文本（``·`` 分隔；调用方按栏宽截断）。

    例：``42 条 · 5 轮 · 工具 6/7 · 失败 1 · 12.3s · ↑1.2k ↓3.4k · 12/42``

    Args:
        stats: ``collect_trace_stats`` 结果。
        sel_pos: 当前选中位置（1-based；0 = 无）。
        total: 记录总数（位置显示 ``n/total``）。
    """
    parts = [f"{_safe_int(stats.get('total'))} 条"]
    turns = _safe_int(stats.get("turns"))
    if turns:
        parts.append(f"{turns} 轮")
    tools = _safe_int(stats.get("tools"))
    if tools:
        done = _safe_int(stats.get("tools_done"))
        failed = _safe_int(stats.get("tools_failed"))
        running = _safe_int(stats.get("tools_running"))
        seg = f"工具 {done}/{tools}"
        if running:
            seg += f" 运行 {running}"
        parts.append(seg)
        if failed or _safe_int(stats.get("errors")):
            parts.append(f"失败 {failed or _safe_int(stats.get('errors'))}")
    elapsed = float(stats.get("elapsed") or 0.0)
    if elapsed > 0:
        parts.append(format_duration(elapsed))
    tokens_in = _safe_int(stats.get("tokens_in"))
    tokens_out = _safe_int(stats.get("tokens_out"))
    if tokens_in or tokens_out:
        parts.append(f"\u2191{format_tokens(tokens_in)} \u2193{format_tokens(tokens_out)}")
    if total:
        pos = _safe_int(sel_pos)
        parts.append(f"{pos}/{total}")
    return " \u00b7 ".join(parts)


def _ratio_row(label: str, count: int, total: int, width: int) -> list:
    """比例行：``label  占比条 count``（条宽按比例；total<=0 → 空条）。

    ``label`` 恒为记录种类名（ASCII 常量化——``type=<kind>`` 注册名），
    ``len`` 即显示宽度（中文宽度差异不适用）。
    """
    bar_w = max(0, width - len(label) - 10)
    filled = 0
    if total > 0 and bar_w > 0:
        filled = int(round(count / total * bar_w))
        filled = max(1, min(bar_w, filled)) if count else 0
    runs = [
        StyledRun(f"{label} ", _S_STATS_LABEL),
        StyledRun("\u2588" * filled, _S_STATS_BAR),
        StyledRun("\u2591" * max(0, bar_w - filled), _S_SEP_ROW),
        StyledRun(f" {count}", _S_STATS_VALUE),
    ]
    return runs


def stats_panel_rows(records, width: int) -> list:
    """``i`` 统计面板全量内容行（滚动窗口数据源）。

    小节：概览（计数/耗时/token/成功率）→ 各工具耗时排行（条形图）→
    记录种类分布。返回 ``list[list[StyledRun]]``（与检查器内容行同模型）。

    Args:
        records: 当前轨迹记录列表（视图过滤后的列表——统计随之变化）。
        width: 右栏宽（换行/条宽预算）。
    """
    width = max(1, int(width))
    stats = collect_trace_stats(records)
    rows: list = []
    rows.append([StyledRun(f"{_SECTION_PREFIX}概览", _S_SECTION)])
    rows.append([
        StyledRun("记录 ", _S_STATS_LABEL),
        StyledRun(str(stats["total"]), _S_STATS_VALUE),
        StyledRun(" 条 \u00b7 轮次 ", _S_STATS_LABEL),
        StyledRun(str(stats["turns"]), _S_STATS_VALUE),
        StyledRun(" \u00b7 工具 ", _S_STATS_LABEL),
        StyledRun(str(stats["tools"]), _S_STATS_VALUE),
    ])
    fail_runs = [
        StyledRun("失败 ", _S_STATS_LABEL),
        StyledRun(str(stats["errors"]), _S_ERROR if stats["errors"] else _S_STATS_VALUE),
        StyledRun(" \u00b7 运行中 ", _S_STATS_LABEL),
        StyledRun(str(stats["running"]), _S_WARN if stats["running"] else _S_STATS_VALUE),
    ]
    rows.append(fail_runs)
    time_runs = [
        StyledRun("耗时 ", _S_STATS_LABEL),
        StyledRun(format_duration(stats["elapsed"]), _S_STATS_VALUE),
    ]
    if stats["running_elapsed"] > 0:
        time_runs.append(StyledRun(" \u00b7 运行中 ", _S_STATS_LABEL))
        time_runs.append(StyledRun(
            format_duration(stats["running_elapsed"]), _S_WARN,
        ))
    rows.append(time_runs)
    token_runs = [
        StyledRun("tokens ", _S_STATS_LABEL),
        StyledRun(f"\u2191{format_tokens(stats['tokens_in'])}", _S_STATS_VALUE),
        StyledRun(" ", _S_DIM),
        StyledRun(f"\u2193{format_tokens(stats['tokens_out'])}", _S_STATS_VALUE),
    ]
    if stats["tokens_cache"]:
        token_runs.append(StyledRun(" \u00b7 缓存 ", _S_STATS_LABEL))
        token_runs.append(StyledRun(format_tokens(stats["tokens_cache"]), _S_STATS_VALUE))
    rows.append(token_runs)
    rate = stats["success_rate"]
    if rate is not None:
        rows.append([
            StyledRun("成功率 ", _S_STATS_LABEL),
            StyledRun(f"{rate * 100:.1f}%", _S_ERROR if rate < 1 else _S_STATS_VALUE),
            StyledRun(
                f"（{stats['tools_done']}/{stats['tools_done'] + stats['tools_failed']}）",
                _S_DIM,
            ),
        ])
    # ── 各工具耗时排行 ──
    rows.append([StyledRun("\u2500" * max(1, width - 1), _S_SEP_ROW)])
    rows.append([StyledRun(f"{_SECTION_PREFIX}工具耗时排行", _S_SECTION)])
    ranking = stats["tool_ranking"]
    if not ranking:
        rows.append([StyledRun("(无工具调用)", _S_HINT)])
    else:
        max_sec = max((item[2] for item in ranking), default=0.0)
        name_w = min(
            max((len(item[0]) for item in ranking[: _TOOL_RANK_LIMIT]), default=1),
            max(4, width // 3),
        )
        for i, (name, cnt, sec, fails) in enumerate(ranking[:_TOOL_RANK_LIMIT]):
            bar_w = 0
            if max_sec > 0:
                bar_w = int(round(sec / max_sec * _RANK_BAR_MAX))
            label = name[:name_w].ljust(name_w)
            runs = [
                StyledRun(f"{i + 1:>2} ", _S_HINT),
                StyledRun(label, _S_STATS_LABEL if not fails else _S_ERROR),
                StyledRun(" ", _S_DIM),
                StyledRun("\u2588" * bar_w, _S_STATS_BAR),
                StyledRun(" ", _S_DIM),
                StyledRun(f"{cnt}\u00d7", _S_DIM),
                StyledRun(" ", _S_DIM),
                StyledRun(format_duration(sec), _S_STATS_VALUE),
            ]
            if fails:
                runs.append(StyledRun(f" \u2716{fails}", _S_ERROR))
            rows.append(runs)
        if len(ranking) > _TOOL_RANK_LIMIT:
            rows.append([StyledRun(
                f"\u2026 其余 {len(ranking) - _TOOL_RANK_LIMIT} 个工具未显示", _S_HINT,
            )])
    # ── 记录种类分布 ──
    rows.append([StyledRun("\u2500" * max(1, width - 1), _S_SEP_ROW)])
    rows.append([StyledRun(f"{_SECTION_PREFIX}记录种类分布", _S_SECTION)])
    kinds = stats["kinds"] or {}
    if not kinds:
        rows.append([StyledRun("(无记录)", _S_HINT)])
    else:
        total = stats["total"] or 1
        for kind, count in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])):
            rows.append(_ratio_row(kind, count, total, width))
    # 行宽截断 + 空 run 清理（行级 diff 宽度不变量；空行以空格占位保持高度）
    from src.tui.ink.helpers import truncate_runs
    out: list = []
    for row in rows:
        cleaned = [r for r in row if r.text]
        if not cleaned:
            cleaned = [StyledRun(" ", None)]
        out.append(truncate_runs(cleaned, width))
    return out
