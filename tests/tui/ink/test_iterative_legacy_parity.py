"""整帧错误帧输出 + 迭代调和幂等性（2026-10-23）回归测试。

旧递归调和路径（``_begin_work``/``_create_and_begin``）已删除，生产与测试
统一走 ``_reconcile_tree`` 显式栈。
"""

from __future__ import annotations

import io

from src.tui.ink import Box, Text, h
from src.tui.ink._session_frame_mixin import _SessionFrameMixin
from src.tui.ink.components import render_frame
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.renderer import InkRenderer


class _WidthCache:
    def get_width(self) -> int:
        return 40


def test_render_failure_frame_writes_visible_error() -> None:
    mixin = object.__new__(_SessionFrameMixin)
    stream = io.StringIO()
    mixin._width_cache = _WidthCache()
    mixin._ink_renderer = InkRenderer(stream=stream, height=10)
    mixin._last_frame = None
    mixin._last_frame_lines = 0

    mixin._render_failure_frame(RuntimeError("boom"))

    assert "render error" in stream.getvalue()
    assert "RuntimeError" in stream.getvalue()


def _text_lines(frame) -> list[str]:
    return [line.plain for line in frame.lines]


def test_iterative_reconcile_is_idempotent() -> None:
    element = h(
        Box, None,
        h(Text, None, "alpha"),
        h(Box, None, h(Text, None, "beta")),
    )

    reconciler = Reconciler(None)
    root = reconciler.create_root()
    reconciler.render(root, element, 20, 10)
    first = _text_lines(render_frame(root, 20))

    reconciler.render(root, element, 20, 10)
    second = _text_lines(render_frame(root, 20))

    assert first == second
    assert "alpha" in "".join(first)
