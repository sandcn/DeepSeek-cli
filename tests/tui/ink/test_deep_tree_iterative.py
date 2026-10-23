"""深嵌套组件树：迭代调和/布局/绘制回归测试（2026-10-23）。

调和（``_reconcile_tree``）、布局（``_measure_gen`` + ``_drive_task``）、绘制
（``_paint_tree``）均为显式栈迭代实现，不受 Python 递归深度限制。
"""

from __future__ import annotations

from src.tui.ink import Box, Text, h, renderToString
from src.tui.ink.helpers import strip_ansi


def _nested(depth: int, leaf: str = "leaf"):
    element = h(Text, None, leaf)
    for index in range(depth):
        element = h(Box, {"key": str(index)}, element)
    return element


def test_200_level_tree_renders() -> None:
    text = strip_ansi(renderToString(_nested(200), {"columns": 20}))
    assert "leaf" in text


def test_500_level_tree_renders() -> None:
    text = strip_ansi(renderToString(_nested(500, "deep-leaf"), {"columns": 20}))
    assert "deep-leaf" in text


def test_1000_level_tree_renders_without_truncation() -> None:
    """旧实现 180 层即 RecursionError；迭代化后 1000 层正常渲染、无占位截断。"""
    text = strip_ansi(renderToString(_nested(1000, "very-deep"), {"columns": 20}))
    assert "very-deep" in text
    assert "depth truncated" not in text


def test_deep_tree_has_no_depth_overflow_marker() -> None:
    from src.tui.ink.components import render_frame
    from src.tui.ink.reconciler import Reconciler

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, _nested(800), 20, 10)
    render_frame(root, 20)
    stack = [root]
    overflow = 0
    while stack:
        fiber = stack.pop()
        if getattr(fiber, "_depth_overflow", False):
            overflow += 1
            continue
        child = fiber.child
        while child is not None:
            stack.append(child)
            child = child.sibling
    assert overflow == 0
