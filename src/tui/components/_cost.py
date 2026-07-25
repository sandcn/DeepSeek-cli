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
from ..render_buffer import RenderBuffer

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
        delta_in: int = 0,
        delta_out: int = 0,
        delta_calls: int = 0,
        model: str = "",
        prices: dict | None = None,
        total_stats: dict | None = None,
        session_elapsed: float = 0,
        messages: list | None = None,
        *,
        props: dict | None = None,
    ) -> None:
        """初始化费用显示组件。

        支持两种参数传入方式：
          方式一（位置参数兼容）：直接传入 delta_in, delta_out 等参数。
          方式二（props 统一模式）：通过 props dict 传入所有参数。

        Args:
            delta_in: 本轮输入 token 数（默认 0）。
            delta_out: 本轮输出 token 数（默认 0）。
            delta_calls: 本轮工具调用次数（默认 0）。
            model: 模型名称（默认 ""）。
            prices: 价格字典，包含 "input" 和 "output" 键（默认 {}）。
            total_stats: 累计统计，包含 "input" 和 "output" 键（默认 {}）。
            session_elapsed: 会话已运行秒数（可选，默认 0）。
            messages: 消息列表（可选），用于计算上下文使用百分比。
            props: 外部传入的属性字典。非 None 时从 props 读取参数值，
                   覆盖同名的位置参数（默认 None）。
        """
        # props 优先：合并 props 与位置参数
        merged = dict(props) if props else {}
        self.delta_in = merged.get("delta_in", delta_in)
        self.delta_out = merged.get("delta_out", delta_out)
        self.delta_calls = merged.get("delta_calls", delta_calls)
        self.model = merged.get("model", model)
        self.prices = merged.get("prices", prices or {})
        self.total_stats = merged.get("total_stats", total_stats or {})
        self.session_elapsed = merged.get("session_elapsed", session_elapsed)
        self.messages = merged.get("messages", messages)
        super().__init__(props=merged)

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染费用显示行。

        调用纯函数 compute_round_cost_data() 计算数据，
        按原 show_round_cost 格式组装带 ANSI 颜色的显示行。

        支持两种输出模式：
          - buffer 为 None：返回 str（向后兼容，用于直接获取文本）
          - buffer 不为 None：将内容写入 buffer，返回 None（Widget 统一模式）

        Args:
            buffer: 可选的 RenderBuffer 实例。传入时内容直接写入 buffer。

        Returns:
            str | None: buffer 为 None 时返回渲染字符串；否则返回 None。
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

        return self._finalize_render(line, buffer)

    def render_to_adapter(self, adapter) -> int:
        """通过 OutputAdapter 渲染费用显示行。

        委托 render(buffer) 写入临时 RenderBuffer，
        再从 buffer 取出内容进行终端宽度自适应截断后，通过 adapter.write_line() 输出。

        行为 100% 向后兼容。

        Args:
            adapter: OutputAdapter 实例。

        Returns:
            int: 渲染行数（通常为 1）。
        """
        # 先通过 render(buffer) 获取内容
        term_w = shutil.get_terminal_size().columns
        buf = RenderBuffer(term_w, 1)
        self.render(buf)
        line = buf.render()
        # ★ 自适应终端宽度 — ANSI-aware 截断（保留原逻辑）
        if term_w > 10:
            line = truncate_ansi_line(line, term_w)
        adapter.write_line(line)
        return 1
