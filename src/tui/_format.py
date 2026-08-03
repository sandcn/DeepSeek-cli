"""公共格式化模块（Layer 0，零内部依赖）。

方向C 步骤4：收敛 ``_format_duration`` 双实现（status_bar / _subagent_panel）
与 ``_format_tokens`` / ``_format_speed``（_subagent_panel）为单一真源。

设计模式: 享元（Flyweight）— 单一真源复用，调用方仅 re-export 保持 patch 路径。

格式规范（有意统一）：
  - ``format_duration``：<60s 用 ``x.xs``；≥60s 用 ``m:ss``；≥1h 用 ``h:mm:ss``。
    以 status_bar 规范为准；_subagent_panel 旧 ``m}s{:.0f}s``（如 ``15m30s``）
    统一为 ``15:30``，记录为有意的观感变更。
  - ``format_tokens``：≥1M ``x.xM``；≥1k ``x.xk``；否则原样数字。
  - ``format_speed``：≤0 返回 ``-``；≥1M ``x.xMt/s``；≥1k ``x.xkt/s``；
    ≥100 ``xt/s``；≥1 ``x.xt/s``；否则 ``x.xxt/s``（统一 tok/s 显示）。

依赖约束：仅标准库，不依赖任何父包模块（可独立导入）。
"""

from __future__ import annotations

import math


def format_duration(seconds: float) -> str:
    """格式化时长（status_bar 规范格式）。

    Args:
        seconds: 时长（秒），可为浮点。

    Returns:
        <60s → ``x.xs``；≥60s → ``m:ss``；≥1h → ``h:mm:ss``。
        负数按 <60s 分支处理（``x.xs``，与旧 status_bar 实现一致）。

        方向2（inf/NaN 防护）：非有限值（inf/NaN）返回 ``-``（与
        ``format_speed`` ≤0 的 ``-`` 语义一致）——修复前
        ``int(inf//60)`` OverflowError / ``int(nan)`` ValueError。
    """
    if not math.isfinite(seconds):
        return "-"
    if seconds < 60:
        return f"{seconds:.1f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    return f"{mins // 60}:{mins % 60:02d}:{secs:02d}"


def format_tokens(n: int) -> str:
    """格式化 token 计数（≥1M ``x.xM``；≥1k ``x.xk``；否则原样数字）。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def format_speed(s: float) -> str:
    """格式化速度（tok/s，统一 ``t/s`` 显示）。

    ≤0 → ``-``（无速度）；≥1M → ``x.xMt/s``；≥1k → **整数 k 值 + kt/s
    （``.0f`` 舍入）**；≥100 → ``xt/s``；≥1 → ``x.xt/s``；否则 ``x.xxt/s``。

    P3-10：docstring 与实现对齐——≥1k 分支为 ``{:.0f}kt/s``（整数舍入，
    如 1500 → ``2kt/s``）。

    ★ BUG-47（review 方向）：非有限值（inf/NaN）返回 ``-``（与
    ``format_duration`` 的 isfinite 防护一致）——修复前 NaN 走完所有比较后
    ``f"{s:.2f}t/s"`` → ``"nant/s"``（渲染出非法文本）。
    """
    if not math.isfinite(s):
        return "-"
    if s <= 0:
        return "-"
    if s >= 1_000_000:
        return f"{s / 1_000_000:.1f}Mt/s"
    elif s >= 1_000:
        return f"{s / 1_000:.0f}kt/s"
    elif s >= 100:
        return f"{s:.0f}t/s"
    elif s >= 1:
        return f"{s:.1f}t/s"
    return f"{s:.2f}t/s"


__all__ = ["format_duration", "format_tokens", "format_speed"]
