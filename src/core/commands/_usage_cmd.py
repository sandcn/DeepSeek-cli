"""usage 命令 — ``/usage`` 用量仪表盘视图（全屏视图 / 文本回退）。

有活跃 ChatUI 时打开全屏「用量仪表盘」（``model.fullscreen == "usage"``）——
可视化 token 用量 / 上下文窗口 / 费用统计；无 ChatUI 回退文本列出。

数据源：``core.stats`` token 快照、``core.context_manager`` 上下文使用率、
``TOKEN_PRICES``（或 ``ctx.config_port``）费用计算。
"""

from __future__ import annotations

import logging
import time

from ..adapters.output import get_default_output_port
from ..constants import CYAN, DIM, RESET
from ..internal.commands._command_core import CommandContext

_logger = logging.getLogger(__name__)
_out = get_default_output_port()


def _fmt_tokens(n) -> str:
    """token 数 → 可读文本（1.2k / 3.4M）。"""
    try:
        n = int(n)
    except Exception:
        n = 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _fmt_duration(seconds) -> str:
    try:
        seconds = int(seconds)
    except Exception:
        seconds = 0
    if seconds < 0:
        seconds = 0
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _current_model(ctx) -> str:
    try:
        value = ctx.state.get("model")
        if isinstance(value, str) and value:
            return value
    except Exception:
        pass
    try:
        from ...config import MODEL

        return str(MODEL or "")
    except Exception:
        return ""


def _token_prices(ctx, model: str) -> dict:
    """当前模型价格表（config_port 优先，回退 TOKEN_PRICES，再回退默认）。"""
    from ..internal.commands._command_core import _fill_default_cache_price

    prices = None
    try:
        port = getattr(ctx, "config_port", None)
        if port is not None:
            prices = port.get_token_prices().get(model)
    except Exception:
        prices = None
    if not prices:
        try:
            from ...config import TOKEN_PRICES

            prices = TOKEN_PRICES.get(model)
            if not prices and TOKEN_PRICES:
                prices = next(iter(TOKEN_PRICES.values()))
        except Exception:
            prices = None
    if not prices:
        prices = {"input": 0.01, "output": 0.03}
    return _fill_default_cache_price(model, prices) or prices


def build_usage_sections(ctx) -> list:
    """构建用量仪表盘统计区块。"""
    from ..stats import (
        get_per_second_speed,
        get_session_start_time,
        get_token_stats,
    )
    from ..internal.commands._command_core import compute_cost

    stats = get_token_stats() or {}
    model = _current_model(ctx)

    total_input = int(stats.get("input", 0) or 0)
    output = int(stats.get("output", 0) or 0)
    calls = int(stats.get("calls", 0) or 0)
    cache_hit = int(stats.get("input_cache_hit", 0) or 0)
    cache_miss = int(stats.get("input_cache_miss", total_input) or 0)
    try:
        speed = float(get_per_second_speed() or 0.0)
    except Exception:
        speed = 0.0

    # ── 上下文窗口 ──
    capacity = 0
    try:
        from ...config.proxy import config

        capacity = int(config.get("max_context_tokens", 0) or 0)
    except Exception:
        capacity = 0
    pct = None
    try:
        from ..context_manager import get_context_usage_percent

        pct = get_context_usage_percent()
    except Exception:
        pct = None

    # ── 费用 ──
    prices = _token_prices(ctx, model)
    detail = compute_cost(stats, prices)

    sections: list = [
        {
            "title": "Token 用量",
            "rows": [
                ("输入（未命中）", _fmt_tokens(cache_miss) + "t", "info", None),
                ("输入（缓存命中）", _fmt_tokens(cache_hit) + "t", "ok", None),
                ("输出", _fmt_tokens(output) + "t", "info", None),
                ("合计", _fmt_tokens(total_input + output) + "t", "info", None),
                ("API 调用", str(calls), "info", None),
                ("输出速度", f"{speed:.1f} tok/s", "info", None),
            ],
        },
        {
            "title": "上下文窗口",
            "rows": _context_rows(capacity, pct),
        },
        {
            "title": "费用估算",
            "rows": [
                ("输入费用", f"${detail.get('input_cost', 0.0):.4f}", "info", None),
                ("输出费用", f"${detail.get('output_cost', 0.0):.4f}", "info", None),
                ("合计", f"${detail.get('total', 0.0):.4f}", "warn", None),
                ("缓存节省", f"${detail.get('saved', 0.0):.4f}", "ok", None),
                ("模型", model or "(未知)", "info", None),
            ],
        },
        {
            "title": "会话",
            "rows": [
                ("时长", _fmt_duration(time.time() - get_session_start_time()), "info", None),
            ],
        },
    ]
    return sections


def _context_rows(capacity: int, pct) -> list:
    rows: list = []
    if capacity > 0:
        if capacity >= 1_000_000:
            cap_text = f"{capacity / 1_000_000:g}M tokens"
        elif capacity >= 1000:
            cap_text = f"{capacity // 1000}k tokens"
        else:
            cap_text = f"{capacity} tokens"
        rows.append(("容量", cap_text, "info", None))
    else:
        rows.append(("容量", "(未设置)", "warn", None))
    if pct is None:
        rows.append(("使用率", "(不可用)", "info", None))
    else:
        try:
            ratio = max(0.0, min(1.0, float(pct) / 100.0))
        except Exception:
            ratio = 0.0
        kind = "warn" if ratio >= 0.7 else "ok"
        rows.append(("使用率", "", kind, ratio))
        if capacity > 0:
            used = int(capacity * ratio)
            rows.append(("已用", _fmt_tokens(used) + "t", "info", None))
            rows.append(("剩余", _fmt_tokens(max(0, capacity - used)) + "t", "info", None))
    return rows


def _open_usage_ui(ctx) -> bool:
    """打开全屏用量仪表盘视图（有 ChatUI 时）。"""
    from ..adapters.ui_runtime import get_usage_view_state_cls
    from ._view_opener import open_fullscreen_view

    refresh_seq = {"v": 0}

    def setup(model, state):
        state.sections = build_usage_sections(ctx)

    def tick(state) -> bool:
        if state.refresh_seq > refresh_seq["v"]:
            refresh_seq["v"] = state.refresh_seq
            state.sections = build_usage_sections(ctx)
        return False

    return open_fullscreen_view(
        ctx, view_id="usage", state_attr="usage_view",
        state_cls=get_usage_view_state_cls(), setup=setup, on_tick=tick,
        close_hint="用量仪表盘已关闭", timeout_hint="用量仪表盘超时关闭",
    )


def _usage_text(ctx) -> bool:
    """文本列出用量统计（无 ChatUI / 单次模式回退）。"""
    sections = build_usage_sections(ctx)
    _out.write(f"\n{DIM}  \u2500 用量统计{RESET}", level="raw", source="cmd")
    for sec in sections:
        _out.write(f"  {CYAN}\u25b8 {sec.get('title', '')}{RESET}", level="raw", source="cmd")
        for row in sec.get("rows") or []:
            label, value = row[0], row[1]
            extra = ""
            if len(row) > 3 and row[3] is not None:
                extra = f"  {float(row[3]) * 100:.1f}%"
            _out.write(f"    {label}: {value}{extra}", level="raw", source="cmd")
    return True


def _cmd_usage(ctx) -> bool:
    """打开用量仪表盘（有 ChatUI）或文本列出（回退）。"""
    if _open_usage_ui(ctx):
        return True
    return _usage_text(ctx)


from .base import CommandMeta, CommandPlugin, declare_command_plugin


class UsageCommand(CommandPlugin):
    """用量仪表盘（/usage）。"""

    def __init__(self):
        self.meta = CommandMeta(
            name="usage", description="用量仪表盘（token / 上下文 / 费用）", group="model",
        )

    def execute(self, ctx: CommandContext) -> bool:
        return _cmd_usage(ctx)


declare_command_plugin(UsageCommand())
