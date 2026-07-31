"""底部栏状态模块 — 阶段显示映射 + 状态文本构建。

从 ``_bottom_bar.py`` 提取为独立子模块。
"""

from __future__ import annotations

import time


# ═══════════════════════════════════════════════════════════
# 阶段→显示文本映射
# ═══════════════════════════════════════════════════════════

_PHASE_DISPLAY: dict[str, str] = {
    "thinking": "思考",
    "answering": "回答",
    "parsing": "接收工具参数",
}


def _build_status_text(status_active: bool, main_phase: str, main_phase_start: float,
                       tool_count: int, tool_phase_start: float) -> str:
    """构建分隔线状态文本（纯文本，不含 ANSI 颜色）。"""
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
    return f"\u00b7 {status} {elapsed:.2f}s"


__all__ = ["_PHASE_DISPLAY", "_build_status_text"]
