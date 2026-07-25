from __future__ import annotations

import logging

from ..widget_base import Widget
from ..render_buffer import RenderBuffer

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# Padding — 内边距布局
# ═══════════════════════════════════════════════════════════


class Padding(Widget):
    """内边距控件。

    在子控件周围添加空白边距。

    Args:
        child: 子控件。
        left: 左边距（列数），默认 1。
        right: 右边距（列数），默认 1。
        top: 上边距（行数），默认 0。
        bottom: 下边距（行数），默认 0。
        padding: 统一边距（四边相等），优先级低于独立参数。
    """

    def __init__(
        self,
        child: Widget,
        left: int | None = None,
        right: int | None = None,
        top: int | None = None,
        bottom: int | None = None,
        padding: int = 1,
        key: str | None = None,
    ) -> None:
        # 确定各方向边距
        p_left = left if left is not None else padding
        p_right = right if right is not None else padding
        p_top = top if top is not None else 0
        p_bottom = bottom if bottom is not None else 0
        super().__init__(props={
            "left": p_left, "right": p_right,
            "top": p_top, "bottom": p_bottom,
        }, key=key)
        self._children_source: list[Widget] = [child]
        self._renders_children = True

    def compose(self) -> list[Widget]:
        """返回声明的子控件列表（始终为单元素列表）。"""
        return self._children_source

    def render(self, buffer: RenderBuffer) -> None:
        """在子控件周围添加空白边距。

        子控件渲染到内部区域（扣除边距后的区域），
        通过临时缓冲区合并。
        """
        pl = self._props.get("left", 1)
        pr = self._props.get("right", 1)
        pt = self._props.get("top", 0)
        pb = self._props.get("bottom", 0)
        inner_w = max(0, buffer.width - pl - pr)
        inner_h = max(0, buffer.height - pt - pb)
        if inner_w <= 0 or inner_h <= 0:
            return
        child = self._children[0] if self._children else (self._children_source[0] if self._children_source else None)
        if child is not None:
            # 渲染到临时缓冲区，然后写入父缓冲区正确位置
            tmp = RenderBuffer(inner_w, inner_h)
            try:
                child.render(tmp)
            except Exception as e:
                _logger.debug("Padding: child.render failed: %s", e)
            # 合并回父缓冲区
            buffer.merge(tmp, pl, pt)

    def __repr__(self) -> str:
        p = self._props
        return (
            f"Padding(l={p['left']} r={p['right']} "
            f"t={p['top']} b={p['bottom']})"
        )
