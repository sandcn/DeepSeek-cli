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

    def test_measure_bad_width_fallback(self):
        """畸形 width 兜底（不抛异常，回退可用宽度）——与其他布局解析一致。"""
        fiber = _input_fiber(text="hello", cursor_pos=3, width="bad-width")
        w, h = _measure(fiber, 80)
        assert w == 80, f"畸形 width 应回退可用宽度 80，实际 {w}"
        assert h >= 2


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

    def test_time_bucket_025s_granularity(self):
        """时间戳降级 0.25s 桶（方向3 呼吸平滑）：同桶（<0.25s）命中缓存；跨桶重建。"""
        fiber = self._make_fiber()
        with patch("src.tui.app.input_area.time.monotonic") as mock_time:
            mock_time.return_value = 1000.0
            lines1 = _build_lines(fiber)
            mock_time.return_value = 1000.2  # 同桶（int(1000.2/0.25)=4000）
            lines2 = _build_lines(fiber)
            assert lines1 is lines2, "同 0.25s 桶应命中缓存"
            mock_time.return_value = 1000.3  # 跨桶（int(1000.3/0.25)=4001）
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


# ═══════════════════════════════════════════════════════════
# 方向1 步骤4 — input_area 渲染修复（CJK merge / 窄屏截断 / 分隔线 / 渐显桶）
# ═══════════════════════════════════════════════════════════

class TestMergeCjkColumnAdvance:
    """方向1 步骤4 — _merge 按显示宽度推进列（CJK 宽 2 推进 2）。"""

    def test_merge_cjk_column_advance_regression(self):
        """CJK 字符在画布行中占 2 列（col 1 留空），后续字符不错位。"""
        from src.tui.app.input_area import _merge
        from src.tui.ink.output import Line
        row = {}
        line = Line()
        line.append("你", None)  # 宽 2
        line.append("a", None)   # 宽 1
        _merge(row, 0, line)
        # "你" 占 col 0（宽 2 → col 1 留空）；"a" 在 col 2（修复前 col 1 被覆盖）
        assert row[0] == ("你", None)
        assert row[2] == ("a", None), (
            f"CJK 后字符应在 col 2（显示宽度推进），实际 row={row!r}"
        )
        assert 1 not in row or row[1] != ("a", None)

    def test_merge_cjk_then_cjk(self):
        """连续 CJK：每个占 2 列（col 0、col 2）。"""
        from src.tui.app.input_area import _merge
        from src.tui.ink.output import Line
        row = {}
        line = Line()
        line.append("你", None)
        line.append("好", None)
        _merge(row, 0, line)
        assert row[0] == ("你", None)
        assert row[2] == ("好", None)


class TestNarrowTerminalTruncation:
    """方向1 步骤4 — 窄屏（width=20/15）各行宽度 ≤ width（补全/搜索/占位符/分隔线）。"""

    def _plain_widths(self, fiber):
        lines = _build_lines(fiber)
        return [line.width for line in lines]

    def test_popup_narrow_terminal_regression(self):
        """width=20：补全弹窗（含超长命令描述）各行宽度 ≤ width。"""
        from src.tui.app.model import CompletionState
        fiber = _input_fiber(
            text="", cursor_pos=0, width=20,
            completion=CompletionState(
                visible=True,
                items=["/model", "/help"],
                texts=["/model", "/help"],
                selected=0,
                title="命令",
                descriptions=[
                    "这是一个非常非常长的命令描述文本用于测试截断行为是否正确",
                    "short",
                ],
                types=["command", "command"],
            ),
        )
        fiber.layout_box = _Box(x=0, y=0, w=20, h=1)
        widths = self._plain_widths(fiber)
        assert all(w <= 20 for w in widths), (
            f"窄屏补全弹窗行超宽: {widths}"
        )

    def test_search_overlay_narrow_regression(self):
        """width=20：反向历史搜索覆盖行（超长 match）宽度 ≤ width。"""
        from src.tui.app.model import HistorySearchState
        fiber = _input_fiber(
            text="", cursor_pos=0, width=20,
            history_search=HistorySearchState(
                query="foo", matches=["foobar" * 20], index=0, active=True,
            ),
        )
        fiber.layout_box = _Box(x=0, y=0, w=20, h=1)
        widths = self._plain_widths(fiber)
        assert all(w <= 20 for w in widths), (
            f"窄屏搜索覆盖行超宽: {widths}"
        )

    def test_placeholder_narrow_regression(self):
        """width=15：占位符（超长）截断至剩余输入区宽度（行宽 ≤ width）。"""
        fiber = _input_fiber(text="", cursor_pos=0, width=15)
        fiber.layout_box = _Box(x=0, y=0, w=15, h=1)
        widths = self._plain_widths(fiber)
        assert all(w <= 15 for w in widths), (
            f"窄屏占位符行超宽: {widths}"
        )

    def test_separator_narrow_no_overflow_regression(self):
        """width=20/15：上下分隔线行总宽 ≤ width（CPU/MEM/时间戳截断）。"""
        for w in (20, 15):
            fiber = _input_fiber(text="", cursor_pos=0, width=w, cpu=100, mem=99)
            fiber.layout_box = _Box(x=0, y=0, w=w, h=1)
            widths = self._plain_widths(fiber)
            assert all(ln <= w for ln in widths), (
                f"width={w} 分隔线行超宽: {widths}"
            )


class TestPopupSplitDesc:
    """分栏说明模式（user_select）：左栏选项列表 + 右栏当前选中项说明。"""

    def _split_fiber(self, selected=0, width=60):
        from src.tui.app.model import CompletionState
        fiber = _input_fiber(
            text="", cursor_pos=0, width=width,
            completion=CompletionState(
                visible=True,
                items=["/model", "/help"],
                texts=["/model", "/help"],
                selected=selected,
                title="选择",
                descriptions=["切换当前模型", "显示帮助"],
                split_desc=True,
            ),
        )
        fiber.layout_box = _Box(x=0, y=0, w=width, h=1)
        return fiber

    def test_split_desc_right_column_shows_selected(self):
        """左栏显示选项，右栏显示当前选中项说明，行宽 ≤ width。"""
        lines = _build_lines(self._split_fiber(selected=0, width=60))
        assert "选择" in lines[0].plain, lines[0].plain
        body = lines[1].plain
        assert "/model" in body, f"左栏应显示选项: {body!r}"
        assert "│" in body, f"应含左右栏分隔线: {body!r}"
        assert "切换当前模型" in body, f"右栏应显示选中项说明: {body!r}"
        assert body.index("/model") < body.index("│") < body.index("切换当前模型"), (
            f"选项应在左栏、说明应在右栏: {body!r}"
        )
        assert all(line.width <= 60 for line in lines), (
            f"分栏行超宽: {[l.width for l in lines]}"
        )

    def test_split_desc_updates_with_selection(self):
        """高亮移动到不同选项时右栏说明随之更新。"""
        # 选中第 2 项 → 右栏显示其说明
        fiber = self._split_fiber(selected=1, width=60)
        lines = _build_lines(fiber)
        assert "显示帮助" in lines[1].plain, lines[1].plain
        # 快照缓存键含 split_desc/descriptions/selected：改选中后重新渲染
        from src.tui.app.model import CompletionState
        comp = fiber.props["completion"]
        comp.selected = 0
        del fiber._lines_cache  # 清除快照缓存（模拟下一帧）
        lines2 = _build_lines(fiber)
        assert "切换当前模型" in lines2[1].plain, lines2[1].plain

    def test_split_desc_command_popup_unchanged(self):
        """命令补全（split_desc=False）仍保持右侧灰显描述（不分栏）。"""
        from src.tui.app.model import CompletionState
        fiber = _input_fiber(
            text="", cursor_pos=0, width=60,
            completion=CompletionState(
                visible=True,
                items=["/model", "/help"],
                texts=["/model", "/help"],
                selected=0,
                title="命令",
                descriptions=["切换当前模型", "显示帮助"],
                types=["command", "command"],
                split_desc=False,
            ),
        )
        fiber.layout_box = _Box(x=0, y=0, w=60, h=1)
        lines = _build_lines(fiber)
        body = lines[1].plain
        # 不分栏：无分隔线，描述在选项右侧
        assert "│" not in body, f"命令补全不应分栏: {body!r}"
        assert "切换当前模型" in body, f"描述应在右侧灰显: {body!r}"
        assert body.index("/model") < body.index("切换当前模型"), body


class TestPlaceholderFadeSmoothBucket:
    """方向1 步骤4 / 方向3 — 占位符渐显期 0.1s 桶（平滑渐显）、结束后 0.25s 桶。"""

    def _fade_fiber(self):
        from src.tui.app.input_area import _placeholder_fade_color, _PLACEHOLDER_TEXT
        fiber = _input_fiber(text="", cursor_pos=0, width=80)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        # 建立渐显起始（同占位符持续显示）
        with patch("src.tui.app.input_area.time.monotonic", return_value=1000.0):
            _placeholder_fade_color(fiber, _PLACEHOLDER_TEXT, 242)
        return fiber

    def test_placeholder_fade_smooth_regression(self):
        """渐显期（elapsed < fade_duration）：snap_key 每 0.1s 变化。"""
        fiber = self._fade_fiber()
        keys = []
        with patch("src.tui.app.input_area.time.monotonic") as mock_time:
            # 渐显期 0.6s 内：1000.0 ~ 1000.55，每 0.1s 变化
            for t in (1000.05, 1000.15, 1000.25, 1000.35):
                mock_time.return_value = t
                keys.append(_snap_key_of(fiber))
        assert len(set(keys)) >= 3, (
            f"渐显期 snap_key 应随 0.1s 桶变化，实际 keys={keys}"
        )

    def test_placeholder_fade_ends_025s_bucket(self):
        """渐显结束后（elapsed >= fade_duration）：回退 0.25s 桶（方向3 呼吸平滑）。"""
        fiber = self._fade_fiber()
        keys = []
        with patch("src.tui.app.input_area.time.monotonic") as mock_time:
            # 渐显结束后（>0.6s）：同 0.25s 桶（1000.7、1000.72 → int(/0.25)=4002）
            mock_time.return_value = 1000.7
            keys.append(_snap_key_of(fiber))
            mock_time.return_value = 1000.72
            keys.append(_snap_key_of(fiber))
            # 跨 0.25s 桶 → 变化
            mock_time.return_value = 1001.0
            keys.append(_snap_key_of(fiber))
        assert keys[0] == keys[1], "渐显结束后同 0.25s 桶 snap_key 应不变"
        assert keys[1] != keys[2], "跨 0.25s 桶 snap_key 应变化"


def _snap_key_of(fiber):
    """读取 fiber 快照缓存键（_build_lines 写回的 snap_key）。"""
    from src.tui.app.input_area import _build_lines
    _build_lines(fiber)
    cached = getattr(fiber, "_lines_cache", None)
    assert cached is not None
    return cached[0][-1]  # 最后一项为 time_bucket
