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
        with patch("src.tui._input._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            _measure(fiber, 80)
            _measure(fiber, 80)
            # 两次 measure：第二次命中缓存 → _compute_input_layout 不再调用 _wrap_by_width
            assert mock_wrap.call_count == 1, (
                f"缓存命中后不应重复换行计算，实际调用 {mock_wrap.call_count} 次"
            )

    def test_layout_cache_miss_on_text_change_regression(self):
        """text 变化时缓存键不同 → 重新计算。"""
        fiber = _input_fiber(text="hello", cursor_pos=5)
        with patch("src.tui._input._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
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
        with patch("src.tui._input._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
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


# ═══════════════════════════════════════════════════════════
# 方向4 — _build_lines 快照缓存（同快照返回同一 Line 列表对象）
# ═══════════════════════════════════════════════════════════

class TestSnapshotCache:
    """方向4 — 输入区快照缓存：同快照命中返回同一 Line 列表；状态变化重建。"""

    def _make_fiber(self, **extra):
        fiber = _input_fiber(text="hello", cursor_pos=3, **extra)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        return fiber

    def test_same_snapshot_returns_same_list(self):
        """同快照两次 _build_lines → 返回同一 Line 列表对象（缓存命中）。"""
        fiber = self._make_fiber()
        lines1 = _build_lines(fiber)
        lines2 = _build_lines(fiber)
        assert lines1 is lines2

    def test_text_change_rebuilds(self):
        """text 变化 → 重建（列表对象不同）。"""
        fiber = self._make_fiber()
        lines1 = _build_lines(fiber)
        fiber.props["text"] = "hello world"
        lines2 = _build_lines(fiber)
        assert lines1 is not lines2

    def test_selected_change_rebuilds(self):
        """补全 selected 变化 → 重建（高亮移动必须进 key）。"""
        from src.tui.app.model import CompletionState
        fiber = self._make_fiber(completion=CompletionState(
            visible=True, items=["a", "b"], texts=["a", "b"], selected=0,
        ))
        lines1 = _build_lines(fiber)
        fiber.props["completion"].selected = 1
        lines2 = _build_lines(fiber)
        assert lines1 is not lines2

    def test_cpu_change_rebuilds(self):
        """cpu 变化 → 重建。"""
        fiber = self._make_fiber()
        lines1 = _build_lines(fiber)
        fiber.props["cpu"] = 55
        lines2 = _build_lines(fiber)
        assert lines1 is not lines2

    def test_time_bucket_1s_granularity(self):
        """时间戳降级 1s 桶：同桶（<1s）命中缓存；跨桶重建。"""
        fiber = self._make_fiber()
        with patch("src.tui.app.input_area.time.monotonic") as mock_time:
            mock_time.return_value = 1000.0
            lines1 = _build_lines(fiber)
            mock_time.return_value = 1000.5  # 同桶（int(1000.5)=1000）
            lines2 = _build_lines(fiber)
            assert lines1 is lines2, "同 1s 桶应命中缓存"
            mock_time.return_value = 1001.5  # 跨桶（int(1001.5)=1001）
            lines3 = _build_lines(fiber)
            assert lines1 is not lines3, "跨桶应重建"


# ═══════════════════════════════════════════════════════════
# 方向5 — 光标算法单一真源收敛（input_area 从 _input 导入同一实现）
# ═══════════════════════════════════════════════════════════

class TestCursorAlgorithmSingleSource:
    """方向5 — input_area._compute_input_layout/_cursor_visual_from_layout 与 _input 同一对象。"""

    def test_input_area_imports_from_input_regression(self):
        """input_area 的换行/光标辅助函数与 _input 同一对象（删除本地副本）。"""
        import src.tui._input as _input_mod
        import src.tui.app.input_area as ia
        assert ia._compute_input_layout is _input_mod._compute_input_layout
        assert ia._cursor_visual_from_layout is _input_mod._cursor_visual_from_layout
        assert ia._compute_cursor_visual_pos is _input_mod._compute_cursor_visual_pos

    def test_input_area_no_local_cursor_duplicate_regression(self):
        """input_area 不再内联定义 _cursor_visual_from_layout（单一真源）。"""
        import inspect
        import src.tui.app.input_area as ia
        src = inspect.getsource(ia)
        # 本地副本已删除：函数体不再包含其核心逻辑（仅从 _input 导入）
        assert "def _cursor_visual_from_layout(" not in src
        assert "def _compute_input_layout(" not in src


# ═══════════════════════════════════════════════════════════
# 方向6 — _placeholder_fade_color 复用一次 time.monotonic
# ═══════════════════════════════════════════════════════════

class TestPlaceholderFadeSingleClock:
    """方向6 — _placeholder_fade_color 复用一次 time.monotonic（修复前两次调用）。"""

    def test_placeholder_fade_single_monotonic_regression(self):
        """占位符出现时 start 复用存储的 time.monotonic（修复前两次调用不一致）。"""
        from src.tui.app.input_area import _placeholder_fade_color
        fiber = _input_fiber(text="", cursor_pos=0)
        # 调用序列：T1=取起始（存储 + start 复用一次调用）、T2=算 elapsed
        with patch("src.tui.app.input_area.time.monotonic", side_effect=[100.0, 101.0]) as mock_t:
            color = _placeholder_fade_color(fiber, "ph", 242)
        # 存储键 == start（单一调用复用）；elapsed = 101-100 = 1.0 >= duration
        # → 返回 end_color（修复前 start 取第二次调用值 → elapsed=0 → 起始色）
        assert fiber._placeholder_fade_key == ("ph", 100.0)
        assert color == 242, (
            f"elapsed 应基于存储起始时间（修复前 start 取第二次调用值）: {color}"
        )
        assert mock_t.call_count == 2, (
            f"应 2 次调用（取起始 + 算 elapsed），实际 {mock_t.call_count}"
        )

    def test_placeholder_fade_elapsed_uses_stored_start(self):
        """同占位符持续显示时 elapsed 基于存储起始时间（复用路径）。"""
        from src.tui.app.input_area import _placeholder_fade_color
        fiber = _input_fiber(text="", cursor_pos=0)
        with patch("src.tui.app.input_area.time.monotonic", return_value=100.0):
            _placeholder_fade_color(fiber, "ph", 242)
        # 同占位符再次调用（不同时刻）→ 复用存储起始时间，不重置
        with patch("src.tui.app.input_area.time.monotonic", return_value=101.0):
            color = _placeholder_fade_color(fiber, "ph", 242)
        assert fiber._placeholder_fade_key == ("ph", 100.0)
        assert 238 <= color <= 242
