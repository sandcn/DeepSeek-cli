"""livelines — LiveLines 流式行批量渲染标准组件。

React Ink 标准组件：渲染**活跃区行列表**（``lines: list[Line]``）——流式
开放块的稳定行（已确定、内容不再变化的行）。经 host 机制批量发射：
measure 高度 = 行数，paint 直接写画布（Line 对象身份复用 → 行级 diff 身份
短路）。

设计动机（性能核心）：流式开放块的预览行可达 ``_PREVIEW_MAX_LINES``（200）
行，且每次流式增量都会重建 children。若逐行 ``h(TEXT)`` 组件化，每帧
reconciler / layout / paint 都要遍历 200 个 fiber（实测长代码块/大表格流式
~3ms/帧）；本组件把稳定行收敛为**单个 host fiber**，每帧只剩 1 次测量 +
O(行数) 次画布引用赋值（~0.1ms），行内容缓存（行级 Line 复用）由调用方
（``chat_view._OpenChildrenCache``）维护。

与 ``StaticLines`` 的区别：后者是**静态历史**语义（帧前缀缓存 + render_frame
前缀拼接，无变化帧跳过画布写入）；本组件是每帧都可能变化的活跃区，不做
前缀跳过——每次 paint 全量写画布（引用赋值成本极低，语义简单不会错位）。
"""

from __future__ import annotations

from ..element import h
from .staticlines import _coerce_line, _resolve_lines

__all__ = ["LiveLines"]


def _measure(fiber, avail_w) -> tuple[int, int]:
    """测量：宽度取可用宽度，高度 = 行数。"""
    return (avail_w, len(_resolve_lines(fiber)))


def _paint(fiber, canvas) -> None:
    """绘制：将行列表写入画布（``Line`` 对象直接赋值，跨帧身份复用）。

    ``box.x == 0`` 时整行替换；否则与已有画布行合并（保留父容器边框/前缀），
    与 ``StaticLines`` 的非零 x 分支同语义。
    """
    box = fiber.layout_box
    if box is None:
        return
    lines = _resolve_lines(fiber)
    # ★ 2026-10-23（绘制不越界，用户报障：流式输出偶发写到下屏最顶分割线）：
    #   写行数受**布局盒高度**约束——批量 host 修复前无条件把 ``len(lines)``
    #   行写入画布；数据与布局瞬时不一致（行数变化与布局测量不同步 / 缓存
    #   键陈旧）时，多出的行会写穿到下屏第一行（状态栏上方分割线），下一帧
    #   才恢复（视觉上「闪一下」）。裁剪到 ``box.h`` 后任何情况下都不会越过
    #   自身布局盒；正常帧 n == box.h，零行为变化。
    limit = max(0, int(getattr(box, "h", 0) or 0))
    if len(lines) > limit:
        lines = lines[:limit]
    y = box.y
    if box.x == 0:
        for line in lines:
            if 0 <= y < len(canvas):
                canvas[y] = _coerce_line(line)
            y += 1
        return
    from .._paint_canvas import _merge_line

    for line in lines:
        if 0 <= y < len(canvas):
            canvas[y] = _merge_line(canvas[y], box.x, _coerce_line(line))
        y += 1


def LiveLines(props: dict):
    """React Ink 标准组件：活跃区行批量渲染。

    Args:
        lines: ``list[Line]`` —— 行列表（每行宽度须 <= 布局宽度，调用方经
            ``wrap_runs_by_width`` 保证；``render_frame`` 的 E-OVERFLOW-GUARD
            仍会对超宽行兜底截断）。

    Returns:
        ``"live-lines"`` host 元素。
    """
    lines = props.get("lines") or []
    if not isinstance(lines, list):
        try:
            lines = list(lines)
        except TypeError:
            lines = []
        props = dict(props)
        props["lines"] = lines
    return h("live-lines", props)
