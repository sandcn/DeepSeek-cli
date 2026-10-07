"""记录对比面板（trace_compare）——轨迹 Trace ``C`` 键两条记录并排对照。

2026-10-07 第三批（用户需求：轨迹 Trace 记录关联与对比）新增：
  - ``compare_panel_rows``：两条 ``TraceRecord`` → 并排对照面板全量内容行
    （``list[list[StyledRun]]``，与检查器/统计面板同一滚动数据模型）；
  - 字段对照（种类/状态/耗时/tokens/工具/调用 ID/参数行/返回行/子代理）
    中**不同值**以 ``compare_diff`` 高亮、相同值以 ``compare_same`` 弱化；
  - 参数 / 返回值并排预览（各取前若干行，按栏宽截断）。

纯函数（无 UI 状态依赖），便于单测；样式取自 ``trace_styles``（可经表现层
数据表 Patch/Overlay 覆盖）。
"""

from __future__ import annotations

from src.tui._format import format_duration, format_tokens
from src.tui.ink import StyledRun
from src.tui.ink.helpers import truncate_runs

from .trace_ledger import _rec_time_seconds
from .trace_styles import (
    _S_COMPARE_DIFF,
    _S_COMPARE_LABEL,
    _S_COMPARE_SAME,
    _S_DIM,
    _S_HINT,
    _S_SECTION,
    _S_SEP_ROW,
    _S_TEXT,
)

__all__ = ["compare_panel_rows", "_compare_fields", "_side_by_side"]

#: 小节标题前缀（对齐检查器参数/返回值小节）
_SECTION_PREFIX = "\u25b8 "  # ▸
#: 中缝分隔符
_MID = "\u2502"  # │
#: 参数/返回值并排预览行数
_MAX_SIDE_LINES = 6


def _int_or(value, default: int = 0) -> int:
    """int 归一化（异常注入值回退默认）。"""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _line_count(text: str) -> int:
    """文本行数（空串 0）。"""
    if not text:
        return 0
    return text.count("\n") + 1


def _time_text(rec) -> str:
    """记录耗时文本（实时计算；无 → ``""``）。"""
    sec = _rec_time_seconds(rec)
    if sec is None:
        return ""
    try:
        return format_duration(float(sec))
    except (TypeError, ValueError):
        return ""


def _compare_fields(rec) -> list:
    """记录 → ``[(字段名, 值文本), ...]``（对比面板字段真源，纯函数）。

    字段顺序稳定（种类/状态/耗时/tokens/工具/调用 ID/参数行/返回行/子代理），
    对比渲染据此对齐两列（一方缺失该字段显示 ``-``）。
    """
    if rec is None:
        return []
    fields: list = [
        ("种类", getattr(rec, "kind", "") or "context"),
        ("状态", (getattr(rec, "status", "") or "") or "-"),
        ("耗时", _time_text(rec) or "-"),
    ]
    tokens = getattr(rec, "tokens", None) or {}
    if isinstance(tokens, dict) and tokens:
        tin = _int_or(tokens.get("input"))
        tout = _int_or(tokens.get("output"))
        fields.append(("tokens", f"\u2191{format_tokens(tin)} \u2193{format_tokens(tout)}"))
    name = (getattr(rec, "tool_name", "") or "").strip()
    if name:
        fields.append(("工具", name))
    cid = (getattr(rec, "tool_call_id", "") or "").strip()
    if cid:
        fields.append(("调用ID", cid[:24]))
    args = getattr(rec, "tool_args", None)
    if args is not None and str(args) != "":
        fields.append(("参数行", str(_line_count(str(args)))))
    result = getattr(rec, "tool_result", "") or ""
    if result:
        fields.append(("返回行", str(_line_count(result))))
    sub = (getattr(rec, "subagent_label", "") or "").strip()
    if sub:
        fields.append(("子代理", sub))
    return fields


def _side_by_side(left: list, right: list, half: int, rhalf: int,
                  width: int = 0) -> list:
    """左右两列 runs → 单行 runs（左列填充至 ``half`` + 中缝 + 右列）。

    左列按显示宽度右填充至 ``half``（不足补空格），中缝 ``│`` 深灰，右列
    原样；``width>0`` 时整体再截断到 ``width``（行宽不变量）。
    """
    left = list(left or [])
    right = list(right or [])
    lw = sum(getattr(r, "width", 1) for r in left)
    pad = max(1, int(half) - lw)
    runs = left + [
        StyledRun(" " * pad, None),
        StyledRun(_MID, _S_SEP_ROW),
        StyledRun(" ", None),
    ] + right
    if width > 0:
        runs = truncate_runs(runs, width)
    return runs


def _side_lines(text: str, w: int, limit: int) -> list:
    """文本 → 并排预览行（每行 ``list[StyledRun]``，取前 ``limit`` 行）。"""
    if not text:
        return []
    lines = str(text).splitlines() or [str(text)]
    out: list = []
    for ln in lines[:limit]:
        out.append(truncate_runs([StyledRun(ln, _S_TEXT)], max(1, w)))
    if len(lines) > limit:
        out.append([
            StyledRun(f"\u2026 \u5171 {len(lines)} \u884c", _S_HINT),
        ])
    return out


def _label_row(text: str, style) -> list:
    """面板小节/标题行（单 run）。"""
    return [StyledRun(text, style)]


def compare_panel_rows(rec_a, rec_b, width: int,
                       max_side_lines: int = _MAX_SIDE_LINES) -> list:
    """两条记录并排对比面板全量内容行（滚动窗口数据源）。

    结构：标题（两条记录号/种类）→ 字段对照（不同值高亮）→ 参数并排 →
    返回值并排。任一侧为 None 时显示「(无记录)」占位（不崩溃）。

    Args:
        rec_a: 左侧记录（None = 空）。
        rec_b: 右侧记录（None = 空）。
        width: 面板宽（列宽/截断预算）。
        max_side_lines: 参数/返回值并排预览最大行数。

    Returns:
        ``list[list[StyledRun]]``（行宽 ≤ width）。
    """
    width = max(1, int(width))
    half = max(8, (width - 3) // 2)
    rhalf = max(8, width - half - 3)
    rows: list = []
    head_a = f"#{getattr(rec_a, 'index', 0)} {getattr(rec_a, 'kind', '') or 'context'}" if rec_a is not None else "(无记录)"
    head_b = f"#{getattr(rec_b, 'index', 0)} {getattr(rec_b, 'kind', '') or 'context'}" if rec_b is not None else "(无记录)"
    rows.append(_label_row(f"{_SECTION_PREFIX}记录对比", _S_SECTION))
    rows.append([
        StyledRun("A ", _S_COMPARE_LABEL),
        StyledRun(head_a, _S_TEXT),
        StyledRun("   ", None),
        StyledRun("B ", _S_COMPARE_LABEL),
        StyledRun(head_b, _S_TEXT),
    ])
    rows.append([StyledRun("\u2500" * max(1, width - 1), _S_SEP_ROW)])
    fields_a = {k: v for k, v in _compare_fields(rec_a)}
    fields_b = {k: v for k, v in _compare_fields(rec_b)}
    keys: list = []
    for k, _ in _compare_fields(rec_a):
        if k not in keys:
            keys.append(k)
    for k, _ in _compare_fields(rec_b):
        if k not in keys:
            keys.append(k)
    if not keys:
        rows.append([StyledRun("(无字段可对比)", _S_HINT)])
    for k in keys:
        va = fields_a.get(k, "-")
        vb = fields_b.get(k, "-")
        style = _S_COMPARE_DIFF if va != vb else _S_COMPARE_SAME
        left = truncate_runs([
            StyledRun(f"{k}: ", _S_DIM), StyledRun(str(va), style),
        ], half)
        right = truncate_runs([
            StyledRun(f"{k}: ", _S_DIM), StyledRun(str(vb), style),
        ], rhalf)
        rows.append(_side_by_side(left, right, half, rhalf, width))
    # ── 参数并排 ──
    args_a = str(getattr(rec_a, "tool_args", "") or "") if rec_a is not None else ""
    args_b = str(getattr(rec_b, "tool_args", "") or "") if rec_b is not None else ""
    rows.append([StyledRun("\u2500" * max(1, width - 1), _S_SEP_ROW)])
    rows.append(_label_row(f"{_SECTION_PREFIX}参数", _S_SECTION))
    la = _side_lines(args_a, half, max_side_lines) or [[StyledRun("(无参数)", _S_HINT)]]
    lb = _side_lines(args_b, rhalf, max_side_lines) or [[StyledRun("(无参数)", _S_HINT)]]
    for i in range(max(len(la), len(lb))):
        left = la[i] if i < len(la) else [StyledRun("", None)]
        right = lb[i] if i < len(lb) else [StyledRun("", None)]
        rows.append(_side_by_side(left, right, half, rhalf, width))
    # ── 返回值并排 ──
    res_a = str(getattr(rec_a, "tool_result", "") or "") if rec_a is not None else ""
    res_b = str(getattr(rec_b, "tool_result", "") or "") if rec_b is not None else ""
    rows.append([StyledRun("\u2500" * max(1, width - 1), _S_SEP_ROW)])
    rows.append(_label_row(f"{_SECTION_PREFIX}返回值", _S_SECTION))
    ra = _side_lines(res_a, half, max_side_lines) or [[StyledRun("(无返回)", _S_HINT)]]
    rb = _side_lines(res_b, rhalf, max_side_lines) or [[StyledRun("(无返回)", _S_HINT)]]
    for i in range(max(len(ra), len(rb))):
        left = ra[i] if i < len(ra) else [StyledRun("", None)]
        right = rb[i] if i < len(rb) else [StyledRun("", None)]
        rows.append(_side_by_side(left, right, half, rhalf, width))
    # 空 run 清理 + 行宽不变量
    out: list = []
    for row in rows:
        cleaned = [r for r in row if r.text]
        if not cleaned:
            cleaned = [StyledRun(" ", None)]
        out.append(truncate_runs(cleaned, width))
    return out
