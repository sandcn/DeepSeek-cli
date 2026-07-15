#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""费用显示组件 — 显示每轮对话的 Token 消耗和费用。

将原 show_round_cost() 转为 TuiComponent 组件渲染逻辑。
组件化后可通过 TUI 框架的 OutputAdapter 统一输出，
同时保留 render() 方法供直接获取格式化字符串。
"""

from __future__ import annotations

import logging
import shutil

from ._base import TuiComponent
from ..core.cost import compute_round_cost_data
from ..core.ansi_utils import truncate_ansi_line
from ..core.style import Style

_logger = logging.getLogger(__name__)


class CostDisplayComponent(TuiComponent):
    """费用显示组件 — 渲染每轮对话的 Token 消耗和费用到终端。

    使用方式（组件渲染路径 — 推荐）：
        comp = CostDisplayComponent(...)
        adapter = get_output_adapter()
        comp.render_to_adapter(adapter)

    使用方式（直接获取格式化字符串）：
        comp = CostDisplayComponent(...)
        text = comp.render()
        print(text)
    """

    def __init__(
        self,
        delta_in: int,
        delta_out: int,
        delta_calls: int,
        model: str,
        prices: dict,
        total_stats: dict,
        session_elapsed: float = 0,
        messages: list | None = None,
    ):
        """初始化费用显示组件。

        Args:
            delta_in: 本轮输入 token 数。
            delta_out: 本轮输出 token 数。
            delta_calls: 本轮工具调用次数。
            model: 模型名称。
            prices: 价格字典，包含 "input" 和 "output" 键。
            total_stats: 累计统计，包含 "input" 和 "output" 键。
            session_elapsed: 会话已运行秒数（可选）。
            messages: 消息列表（可选），用于计算上下文使用百分比。
        """
        self.delta_in = delta_in
        self.delta_out = delta_out
        self.delta_calls = delta_calls
        self.model = model
        self.prices = prices
        self.total_stats = total_stats
        self.session_elapsed = session_elapsed
        self.messages = messages

    def render(self) -> str:
        """渲染费用显示行。

        调用纯函数 compute_round_cost_data() 计算数据，
        按原 show_round_cost 格式组装带 ANSI 颜色的显示行。

        Returns:
            str: 带 ANSI 颜色的格式化费用显示字符串。
        """
        data = compute_round_cost_data(
            self.delta_in,
            self.delta_out,
            self.delta_calls,
            self.model,
            self.prices,
            self.total_stats,
            self.session_elapsed,
            self.messages,
        )

        ctx_str = ""
        if data["ctx_pct"] > 0:
            ctx_str = f" ctx:{data['ctx_pct']:.0f}%{data['compress_hint']}"

        # 使用 core.formatter（零依赖层），避免循环依赖
        from ..core.formatter import format_token_count  # noqa: C0415

        line = Style(fg=244).apply(
            f"  {data['model']}{data['calls_str']}  "
            f"{format_token_count(data['delta_in'])}↑/"
            f"{format_token_count(data['delta_out'])}↓"
            f"  ${data['round_cost']:.4f}  "
            f"∑{format_token_count(data['total_input'])}/"
            f"{format_token_count(data['total_output'])}t"
            f"  ${data['total_cost']:.4f}{ctx_str}{data['duration_str']}"
        )
        return line

    def render_to_adapter(self, adapter) -> int:
        """通过 OutputAdapter 渲染费用显示行。

        处理终端宽度自适应截断后，通过 adapter.write_line() 输出。

        Args:
            adapter: OutputAdapter 实例。

        Returns:
            int: 渲染行数（通常为 1）。
        """
        line = self.render()
        # ★ 自适应终端宽度 — ANSI-aware 截断
        try:
            max_w = shutil.get_terminal_size().columns - 1
        except Exception:
            max_w = 80
        if max_w > 10:
            line = truncate_ansi_line(line, max_w)
        adapter.write_line(line)
        return 1
