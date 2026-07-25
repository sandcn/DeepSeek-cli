#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""费用计算纯函数 — 计算每轮对话的 Token 消耗和费用数据。

从 src/ui/components/cost_display.py 迁移纯函数部分。
与渲染/输出逻辑分离，保持零副作用。
"""

from __future__ import annotations

from ...core.context_selector import calc_usage_percent_values, total_chars
from ...config import MAX_CONTEXT_CHARS

_COMPRESS_WARN_PCT = 90
_COMPRESS_HINT_PCT = 80
_MILLION = 1_000_000




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
    """计算每轮对话的 Token 消耗和费用数据。

    纯函数，无副作用。返回的数据字典包含格式化后的字符串字段
    （calls_str、duration_str）便于下游直接使用。

    Args:
        delta_in: 本轮输入 token 数。
        delta_out: 本轮输出 token 数。
        delta_calls: 本轮工具调用次数。
        model: 模型名称。
        prices: 价格字典，包含 "input" 和 "output" 键。
        total_stats: 累计统计，包含 "input" 和 "output" 键。
        session_elapsed: 会话已运行秒数（可选），>0 时生成 duration_str。
        messages: 消息列表（可选），用于计算上下文使用百分比。

    Returns:
        dict: 包含以下字段的数据字典：
            - delta_in/delta_out/delta_calls: 原始输入值
            - model: 模型名称
            - round_cost/total_cost: 本轮/累计费用
            - total_input/total_output: 累计 token 数
            - duration_str: 格式化后的持续时间字符串（如 "2m30s"）
            - calls_str: 工具调用次数后缀（如 " ×3"）
            - ctx_pct: 上下文使用百分比
            - compress_hint: 压缩提示字符串
    """
    round_cost = (
        delta_in / _MILLION * prices.get("input", 0)
        + delta_out / _MILLION * prices.get("output", 0)
    )
    total_cost = (
        total_stats['input'] / _MILLION * prices.get("input", 0)
        + total_stats['output'] / _MILLION * prices.get("output", 0)
    )
    calls_str = f" ×{delta_calls}" if delta_calls > 1 else ""

    # 使用 core.formatter（零依赖层），避免循环依赖
    from .formatter import format_duration  # noqa: C0415

    duration_str = (
        f" {format_duration(session_elapsed)}"
        if session_elapsed > 0
        else ""
    )

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
