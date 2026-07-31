"""费用计算存根 — 替换已删除的 core/cost.py（DEPRECATED）。

2026-07-29 TUI 重构：原 core/cost.py 已随 core/ 目录清理被删除，
此处提供最小化 compute_round_cost_data 函数。

⚠️ DEPRECATED（2026-07-31）：费用计算已移除。
本函数仅保留全零占位以兼容旧调用方（如 webui ws_handler），
调用方必须检测全零/空结果并降级处理（不展示假费用），
禁止将占位数据展示为真实费用。
当前白名单以旧版真实费用结构为基准，与占位函数返回键不一致；恢复费用计算时须两端统一（同步 webui/types.py _COST_FIELDS 键名）。

保留确认（2026-07-31 方向F）：src/webui/ws_handler/connection.py:170 仍调用
compute_round_cost_data 且已有全零降级（``if all(v in (0, 0.0, "", None)...) skip``），
**保留 DEPRECATED 占位，暂不删除**；恢复费用计算时须同步 webui/types.py
``_COST_FIELDS`` 键名。本模块无运行时代码变更（仅注释标注）。
"""

from __future__ import annotations

from typing import Any


def compute_round_cost_data(round_data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    """计算单轮对话的费用数据（DEPRECATED — 兼容占位）。

    ⚠️ 费用计算已移除，返回全零仅为兼容占位，不反映真实费用。

    历史调用方（如 webui ws_handler.connection._on_cost_update）按旧接口
    以关键字参数调用（delta_in/delta_out/delta_calls/model/prices/total_stats/
    session_elapsed/messages 等），本函数接受任意 kwargs 并忽略，
    统一返回全零占位结构，由调用方做降级处理。

    Args:
        round_data: 兼容旧位置参数（已忽略）。
        **kwargs: 兼容历史关键字调用参数（均已忽略）。

    Returns:
        全零占位 dict：{"input_tokens": 0, "output_tokens": 0,
        "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
    """
    # DEPRECATED: 费用计算已移除，返回全零仅为兼容占位。
    # 调用方须检测全零并降级（不展示假费用），勿将占位数据当作真实费用。
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "input_cost": 0.0,
        "output_cost": 0.0,
        "total_cost": 0.0,
    }


__all__ = ["compute_round_cost_data"]
