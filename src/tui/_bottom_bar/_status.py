"""底部栏状态模块 — 阶段显示映射 + 状态文本构建。

从 ``_bottom_bar.py`` 提取为独立子模块。

状态文本构建单一入口收敛（2026-07-31 方向E）：_build_status_text 为
「阶段/工具耗时文本」（如「· 思考 3.20s」纯文本，无 ANSI）的唯一构建入口；
_render 分隔线分支调用本函数内嵌状态文本，_bar._format_status 仅组装
模型名/工具计数/总耗时/token/速度段（基于 snapshot 数据，与阶段耗时文本
职责不同），不重复阶段文本逻辑。共用 _PHASE_DISPLAY 映射与耗时格式化。
"""

from __future__ import annotations

import time


# ═══════════════════════════════════════════════════════════
# 阶段→显示文本映射（状态文本收敛唯一真源，_render/_bar 共用）
# ═══════════════════════════════════════════════════════════

_PHASE_DISPLAY: dict[str, str] = {
    "thinking": "思考",
    "answering": "回答",
    "parsing": "接收工具参数",
}


def _format_duration(seconds: float, precision: int = 1) -> str:
    """格式化耗时（_status 与 _bar 两处复用的共享实现，P3-15）。

    - <60s：``X.Xs``（精度由 precision 控制：_status 用 .2f、_bar 用 .1f）
    - >=60s：``mins:secs``（>=1h 时 ``hours:min:sec``，秒两位补零）
    """
    if seconds < 60:
        return f"{seconds:.{precision}f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins < 60:
        return f"{mins}:{secs:02d}"
    return f"{mins // 60}:{mins % 60:02d}:{secs:02d}"


def _build_status_text(snap: dict) -> str:
    """构建分隔线状态文本（纯文本，不含 ANSI 颜色）。

    「阶段/工具耗时文本」唯一构建入口（2026-07-31 方向E 收敛）：
    供 _render 分隔线内嵌；_bar._format_status 不重复此逻辑。

    P1-4 修复：参数从 5 个分散字段改为一次性 snapshot dict——调用方
    （_render._build_separator_line）在分隔线构建开头取一次
    ``bb._status.snapshot()``，避免跨字段 5 次独立快照（5 次加锁）导致
    「tool_count>0 用 tool_phase_start 否则用 main_phase_start」的一致性
    假设被破坏。snap 中 ``status_active`` 为预留字段（P3-14：分隔线分支
    在调用前已判断激活态，本函数不消费）。
    """
    main_phase = snap.get("main_phase", "")
    main_phase_start = snap.get("main_phase_start", 0.0)
    tool_count = snap.get("tool_count", 0)
    tool_phase_start = snap.get("tool_phase_start", 0.0)

    if tool_count > 0:
        status = "工具调用中"
        start_time = tool_phase_start
    elif main_phase in _PHASE_DISPLAY:
        status = _PHASE_DISPLAY[main_phase]
        start_time = main_phase_start
    else:
        return ""
    if start_time <= 0.0:
        return ""
    elapsed = time.monotonic() - start_time
    return f"\u00b7 {status} {_format_duration(elapsed, precision=2)}"


__all__ = ["_PHASE_DISPLAY", "_build_status_text", "_format_duration"]
