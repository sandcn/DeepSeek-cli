#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""费用显示组件 — 向后兼容存根

变更说明：核心逻辑已迁移到 src/tui/core/cost.py 和 src/tui/components/_cost.py。
          此文件保留为向后兼容存根，从新位置重新导出。

导出内容：
  - compute_round_cost_data() — 纯函数，从 src.tui.core.cost 重新导出
  - show_round_cost() — 保持原接口的向后兼容包装函数
"""
from __future__ import annotations

import logging
import shutil

from ...tui.core.cost import compute_round_cost_data  # noqa: F401
from ...tui.core.ansi_utils import truncate_ansi_line
from ...core.constants import DARK_GRAY, RESET
from ...tui.core.text_formatter import TextFormatter
from ...tui.widgets.lock import _try_acquire_output_lock

_logger = logging.getLogger(__name__)


def show_round_cost(
    delta_in, delta_out, delta_calls, model, prices, total_stats,
    session_elapsed=0, messages=None,
):
    """显示每轮对话的 Token 消耗和费用（向后兼容包装）。

    保留原接口签名。内部委托给 CostDisplayComponent 的渲染逻辑，
    输出路径（ChatUIConsumer/print）与原行为一致。

    .. deprecated::
        请改用 CostDisplayComponent 组件（TUI 框架渲染路径）。
    """
    # 委托给 CostDisplayComponent 渲染（避免存根重复渲染逻辑）
    from ...tui.components._cost import CostDisplayComponent  # noqa: C0415

    comp = CostDisplayComponent(
        delta_in=delta_in,
        delta_out=delta_out,
        delta_calls=delta_calls,
        model=model,
        prices=prices,
        total_stats=total_stats,
        session_elapsed=session_elapsed,
        messages=messages,
    )

    with _try_acquire_output_lock(name="cost_display", timeout=1.0):
        chat_ui = None
        try:
            from ...tui.consumer import get_active_chat_ui
            chat_ui = get_active_chat_ui()
        except Exception:
            pass
        if chat_ui is not None:
            comp.render_to_adapter(chat_ui.output_adapter)
        else:
            print(comp.render())
