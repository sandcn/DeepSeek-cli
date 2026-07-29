"""费用计算存根 — 替换已删除的 core/cost.py。

2026-07-29 TUI 重构：原 core/cost.py 已随 core/ 目录清理被删除，
此处提供最小化 compute_round_cost_data 函数。
"""

from __future__ import annotations

from typing import Any


def compute_round_cost_data(round_data: dict[str, Any]) -> dict[str, Any]:
    """计算单轮对话的费用数据。

    原 cost.py 的核心函数，重构后返回空字典（费用计算已移除）。
    """
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
    }


__all__ = ["compute_round_cost_data"]
