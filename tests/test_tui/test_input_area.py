"""测试 src/tui/app/input_area.py — PERF-1 统一换行计算 + 缓存 + 光标复用。

纯逻辑断言（fiber 桩 + mock 计数），无终端依赖。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.ink.fiber import Fiber
from src.tui._input import _wrap_by_width
from src.tui.app.input_area import (
    _compute_input_layout,
    _compute_input_rows,
    _wrap_input_text,
    _cursor_visual_from_layout,
    _measure,
    _build_lines,
)


def _input_fiber(text: str = "", cursor_pos: int = -1, width: int = 80, **extra) -> Fiber:
    props = {
        "text": text,
        "cursor_pos": cursor_pos,
        "prompt": "> ",
        "completion": None,
        "status_active": False,
        "cpu": 0,
        "mem": 0,
    }
    props.update(extra)
    return Fiber("host", "input-area", props)


class _Box:
    """极简 LayoutBox 桩（含 x/y/w/h 属性）。"""

    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x=0, y=0, w=80, h=1):
        self.x = x
        self.y = y
        self.w = w
        self.h = h


class TestComputeInputLayout:
    """_compute_input_layout 与旧函数语义一致。"""

    def test_layout_rows_matches_compute_input_rows(self):
        for text, w in [("", 80), ("hello", 80), ("a" * 100, 30), ("l1\nl2\nl3", 80), ("a\n\nb", 10), ("你好world", 8)]:
            rows, wrapped = _compute_input_layout(text, w)
            assert rows == _compute_input_rows(text, w), (text, w)
            flat = [seg for segs in wrapped for seg in segs]
            assert flat == _wrap_input_text(text, w), (text, w)

    def test_layout_empty_text(self):
        rows, wrapped = _compute_input_layout("", 80)
        assert rows == 1
        assert wrapped == [[""]]

    def test_layout_multiline(self):
        rows, wrapped = _compute_input_layout("abc\ndefgh", 3)
        assert rows == 3  # abc / def / gh
        assert wrapped == [["abc"], ["def", "gh"]]


class TestLayoutCache:
    """_measure 建立 fiber 缓存，_build_lines 复用。"""

    def test_layout_cache_hit_regression(self):
        """同 text/max_input 二次 _measure 不重复调用 _wrap_by_width（mock 计数）。"""
        fiber = _input_fiber(text="hello world", cursor_pos=5)
        with patch("src.tui.app.input_area._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            _measure(fiber, 80)
            _measure(fiber, 80)
            # 两次 measure：第二次命中缓存 → _compute_input_layout 不再调用 _wrap_by_width
            assert mock_wrap.call_count == 1, (
                f"缓存命中后不应重复换行计算，实际调用 {mock_wrap.call_count} 次"
            )

    def test_layout_cache_miss_on_text_change_regression(self):
        """text 变化时缓存键不同 → 重新计算。"""
        fiber = _input_fiber(text="hello", cursor_pos=5)
        with patch("src.tui.app.input_area._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            _measure(fiber, 80)
            fiber.props["text"] = "hello world"
            _measure(fiber, 80)
            assert mock_wrap.call_count == 2

    def test_measure_sets_cache_on_fiber(self):
        fiber = _input_fiber(text="abc", cursor_pos=2)
        _measure(fiber, 80)
        assert hasattr(fiber, "_input_layout_cache")
        key, (rows, wrapped) = fiber._input_layout_cache
        assert key == ("abc", 80 - len("> "))

    def test_build_lines_reuses_cache(self):
        """_build_lines 从 fiber 缓存读取（未命中时回退单次计算）。"""
        fiber = _input_fiber(text="hello", cursor_pos=3)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        _measure(fiber, 80)
        with patch("src.tui.app.input_area._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            lines = _build_lines(fiber)
            assert len(lines) >= 3  # 分隔线 + 输入行 + 时间戳
            mock_wrap.assert_not_called()  # 命中缓存 → 不重新换行

    def test_build_lines_fallback_on_miss(self):
        """缓存未命中（未 measure）时 _build_lines 回退单次计算。"""
        fiber = _input_fiber(text="hello", cursor_pos=3)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        lines = _build_lines(fiber)  # 未 measure → 回退
        assert any("hello" in line.plain for line in lines)


class TestCursorFromLayout:
    """_cursor_visual_from_layout 与 _compute_cursor_visual_pos 结果一致。"""

    def _compare(self, text, cursor_pos, max_input):
        from src.tui._input import _compute_cursor_visual_pos
        _, wrapped = _compute_input_layout(text, max_input)
        new_row, new_col = _cursor_visual_from_layout(text, cursor_pos, wrapped)
        old_row, old_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        assert (new_row, new_col) == (old_row, old_col), (
            f"text={text!r} cursor={cursor_pos} w={max_input}: "
            f"layout={new_row},{new_col} old={old_row},{old_col}"
        )

    def test_cursor_pos_from_cached_layout_regression(self):
        """同一 text/cursor/max_input 下 _cursor_visual_from_layout 与旧实现一致。"""
        cases = [
            ("", 0, 80),
            ("hello", 5, 80),
            ("hello", -1, 80),
            ("a" * 100, 90, 30),
            ("a" * 100, 150, 30),
            ("line1\nline2\nline3", 11, 80),
            ("line1\nline2\nline3", 20, 80),
            ("a\tb", 2, 80),
            ("你好世界", 4, 80),
            ("abc\ndefgh", 8, 3),
            ("a\n\nb", 3, 10),
        ]
        for text, pos, w in cases:
            self._compare(text, pos, w)


# ═══════════════════════════════════════════════════════════
# 方向D 步骤14 — 反向历史搜索覆盖行渲染
# ═══════════════════════════════════════════════════════════

class TestReverseSearchOverlay:
    """方向D 步骤14 — input-area 反向历史搜索覆盖行渲染与测量。"""

    @staticmethod
    def _search_fiber(
        query="hello", matches=("hello world",), index=0, active=True,
        text="hello", **extra,
    ) -> Fiber:
        from src.tui.app.model import HistorySearchState
        fiber = _input_fiber(text=text, cursor_pos=len(text), **extra)
        fiber.props["history_search"] = HistorySearchState(
            query=query, matches=list(matches), index=index, active=active,
        )
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        return fiber

    def test_search_overlay_line_rendered(self):
        """搜索激活时渲染 (reverse-i-search)`query`: match 覆盖行。"""
        fiber = self._search_fiber()
        lines = _build_lines(fiber)
        plains = [line.plain for line in lines]
        assert any("(reverse-i-search)`hello`: hello world" in p for p in plains)

    def test_search_overlay_inactive_not_rendered(self):
        """搜索未激活时不渲染覆盖行。"""
        fiber = self._search_fiber(active=False)
        lines = _build_lines(fiber)
        plains = [line.plain for line in lines]
        assert not any("reverse-i-search" in p for p in plains)

    def test_search_overlay_no_match(self):
        """搜索激活但无匹配：渲染 (reverse-i-search)`query`: 空匹配。"""
        fiber = self._search_fiber(query="nope", matches=(), index=-1)
        lines = _build_lines(fiber)
        plains = [line.plain for line in lines]
        assert any("(reverse-i-search)`nope`:" in p for p in plains)

    def test_measure_includes_search_row(self):
        """_measure 读取 history_search 增行（激活时 +1）。"""
        w, h_active = _measure(self._search_fiber(), 80)
        w2, h_inactive = _measure(self._search_fiber(active=False), 80)
        assert w == w2 == 80
        assert h_active == h_inactive + 1
