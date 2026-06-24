"""费用显示组件 — 显示每轮对话的 Token 消耗和费用"""
from __future__ import annotations

import shutil
from ...core.constants import RESET, DARK_GRAY
from ..ansi import strip_ansi, truncate_ansi_line
from ...config import MAX_CONTEXT_CHARS
from ...core.context_selector import calc_usage_percent_values, total_chars
from .._lock import _try_acquire_output_lock
from .. import _lock as _lock_mod  # 通过模块访问回调变量，支持运行时注册
from ..parallel._text_formatter import TextFormatter

_COMPRESS_WARN_PCT = 90
_COMPRESS_HINT_PCT = 80
_MILLION = 1_000_000


def _get_content_length(content):
    """计算消息content的字符长度，支持字符串和多模态列表格式"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        # 多模态消息：提取text部分的长度
        total = 0
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += len(part.get("text", ""))
            elif isinstance(part, str):
                total += len(part)
        return total
    return 0


def compute_round_cost_data(
    delta_in: int,
    delta_out: int,
    delta_calls: int,
    model: str,
    prices: dict,
    total_stats: dict,
    session_elapsed: float = 0,
    messages: list | None = None,
) -> dict:
    round_cost = delta_in / _MILLION * prices.get("input", 0) + delta_out / _MILLION * prices.get("output", 0)
    total_cost = total_stats['input'] / _MILLION * prices.get("input", 0) + total_stats['output'] / _MILLION * prices.get("output", 0)
    calls_str = f" ×{delta_calls}" if delta_calls > 1 else ""
    duration_str = f" {TextFormatter.format_duration(session_elapsed)}" if session_elapsed > 0 else ""

    ctx_pct = 0.0
    compress_hint = ""
    if messages and MAX_CONTEXT_CHARS > 0:
        total_chars_val = total_chars(messages)
        pct = calc_usage_percent_values(total_chars_val)
        ctx_pct = pct
        if pct >= _COMPRESS_WARN_PCT:
            compress_hint = " ⚠ compress!"
        elif pct >= _COMPRESS_HINT_PCT:
            compress_hint = " compress"

    return {
        "delta_in": delta_in,
        "delta_out": delta_out,
        "delta_calls": delta_calls,
        "model": model,
        "round_cost": round_cost,
        "total_cost": total_cost,
        "total_input": total_stats['input'],
        "total_output": total_stats['output'],
        "duration_str": duration_str,
        "calls_str": calls_str,
        "ctx_pct": ctx_pct,
        "compress_hint": compress_hint,
    }


def show_round_cost(delta_in, delta_out, delta_calls, model, prices, total_stats, session_elapsed=0, messages=None):
    data = compute_round_cost_data(delta_in, delta_out, delta_calls, model, prices, total_stats, session_elapsed, messages)

    ctx_str = ""
    if data["ctx_pct"] > 0:
        ctx_str = f" ctx:{data['ctx_pct']:.0f}%{data['compress_hint']}"

    line = (f"{DARK_GRAY}  {data['model']}{data['calls_str']}  {TextFormatter.format_token_count(delta_in)}↑/"
            f"{TextFormatter.format_token_count(delta_out)}↓"
            f"  ${data['round_cost']:.4f}  "
            f"∑{TextFormatter.format_token_count(data['total_input'])}/"
            f"{TextFormatter.format_token_count(data['total_output'])}t"
            f"  ${data['total_cost']:.4f}{ctx_str}{data['duration_str']}{RESET}")
    # ★ 自适应终端宽度 — ANSI-aware 截断
    try:
        max_w = shutil.get_terminal_size().columns - 1
    except Exception:
        max_w = 80
    if max_w > 10:
        line = truncate_ansi_line(line, max_w)
    with _try_acquire_output_lock(name="cost_display", timeout=1.0):
        # 通过回调路由到 ChatUI 上屏（回调由 chat_ui 侧在初始化时注册）
        try:
            if _lock_mod._write_line_callback is not None:
                _lock_mod._write_line_callback(line)
                return
        except Exception:
            pass
        # 回调不可用 → 降级为 print()
        print(line)
