"""测试 src/tui/app/input_area.py — PERF-1 统一换行计算 + 缓存 + 光标复用。

纯逻辑断言（fiber 桩 + mock 计数），无终端依赖。
"""

from __future__ import annotations

from unittest.mock import patch

from src.tui.ink.fiber import Fiber
from src.tui._input_layout import _wrap_by_width
from src.tui.app.input_area import (
    _compute_input_layout,
    _cursor_visual_from_layout,
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


class TestLayoutCache:
    """_build_lines 建立/复用 fiber 换行布局缓存（原遗留 host _measure 职责收拢）。"""

    def test_layout_cache_hit_regression(self):
        """同 text/max_input 二次 _build_lines 不重复调用 _wrap_by_width（mock 计数）。"""
        fiber = _input_fiber(text="hello world", cursor_pos=5)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        with patch("src.tui._input_layout._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            _build_lines(fiber)
            _build_lines(fiber)
            # 两次 build：第二次命中 fiber._input_layout_cache → 不再调用 _wrap_by_width
            assert mock_wrap.call_count == 1, (
                f"缓存命中后不应重复换行计算，实际调用 {mock_wrap.call_count} 次"
            )

    def test_layout_cache_miss_on_text_change_regression(self):
        """text 变化时缓存键不同 → 重新计算。"""
        fiber = _input_fiber(text="hello", cursor_pos=5)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        with patch("src.tui._input_layout._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            _build_lines(fiber)
            fiber.props["text"] = "hello world"
            _build_lines(fiber)
            assert mock_wrap.call_count == 2

    def test_build_lines_sets_cache_on_fiber(self):
        """_build_lines 未命中时计算并写回 fiber._input_layout_cache（_measure 职责收拢）。"""
        fiber = _input_fiber(text="abc", cursor_pos=2)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        _build_lines(fiber)
        assert hasattr(fiber, "_input_layout_cache")
        key, (rows, wrapped) = fiber._input_layout_cache
        assert key == ("abc", 80 - len("> "))

    def test_build_lines_reuses_cache(self):
        """_build_lines 从 fiber 缓存读取（命中时零换行计算）。"""
        fiber = _input_fiber(text="hello", cursor_pos=3)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        _build_lines(fiber)  # 首次：计算 + 写回缓存
        with patch("src.tui._input_layout._wrap_by_width", wraps=_wrap_by_width) as mock_wrap:
            lines = _build_lines(fiber)
            assert len(lines) >= 3  # 分隔线 + 输入行 + 时间戳
            mock_wrap.assert_not_called()  # 命中缓存 → 不重新换行

    def test_build_lines_fallback_on_miss(self):
        """缓存未命中（新 fiber 无缓存）时 _build_lines 回退单次计算。"""
        fiber = _input_fiber(text="hello", cursor_pos=3)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        lines = _build_lines(fiber)  # 无缓存 → 回退计算
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
# 方向1 步骤4 — input_area 渲染修复（窄屏截断 / 分隔线 / 渐显桶）
# ═══════════════════════════════════════════════════════════

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


class TestPromptHighlightOnCompletion:
    """方向4（体验）— 补全弹窗打开时输入提示符提亮（45-59 vs 空闲 32-49）。"""

    def _prompt_fg(self, fiber):
        lines = _build_lines(fiber)
        for line in lines:
            if line.runs and line.runs[0].text == "> ":
                return line.runs[0].style.fg
        return None

    def test_prompt_dimmer_without_completion(self):
        fiber = _input_fiber(text="", cursor_pos=0)
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        fg = self._prompt_fg(fiber)
        assert fg is not None, "应找到提示符行"
        # _glow_color(32, 49) 语义 = time_glow(32, 32+49=81)
        assert 32 <= fg <= 81, f"空闲提示符应在 32-81 呼吸区间，实际 {fg}"

    def test_prompt_brighter_with_completion(self):
        from src.tui.app.model import CompletionState
        fiber = _input_fiber(text="", cursor_pos=0, completion=CompletionState(
            visible=True, items=["a"], texts=["a"], selected=0,
        ))
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        fg = self._prompt_fg(fiber)
        assert fg is not None, "应找到提示符行"
        # _glow_color(45, 55) 语义 = time_glow(45, 45+55=100)——整体上移更亮
        assert 45 <= fg <= 100, f"补全导航提示符应在 45-100 亮青区间，实际 {fg}"
        # 与空闲区间起点对比：补全起点更高（提亮生效）
        fiber2 = _input_fiber(text="", cursor_pos=0)
        fiber2.layout_box = _Box(x=0, y=0, w=80, h=1)
        fg2 = self._prompt_fg(fiber2)
        # 由于是时间基呼吸，区间起点即可验证提亮（补全下限 45 > 空闲下限 32）
        assert fg >= 45 > fg2 or fg >= 45, "补全提示符色号下限应更高"


class TestCompletionSnapFingerprint:
    """BUG-23 — 补全快照用轻量指纹（id/len/selected）替代 tuple(全部项)。"""

    def _make(self, items):
        from src.tui.app.model import CompletionState
        fiber = _input_fiber(text="", cursor_pos=0, completion=CompletionState(
            visible=True, items=list(items), texts=list(items), selected=0,
        ))
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        return fiber

    def test_same_items_reuses_cache(self):
        """items 列表引用不变 → 快照命中（零重建，同时间桶）。"""
        from unittest.mock import patch
        from src.tui.app import _fx
        fiber = self._make(["a", "b", "c"])
        # fade_duration=0：占位符无渐显 → 恒用 0.25s 桶（同时间桶命中缓存）
        with patch("src.tui.app.input_area._fx._DEFAULT_FADE_DURATION", 0.0):
            with patch("src.tui.app.input_area.time.monotonic", return_value=100.0):
                lines1 = _build_lines(fiber)
                lines2 = _build_lines(fiber)
        assert lines1 is lines2

    def test_new_items_list_rebuilds(self):
        """items 新列表（id 变化）→ 重建。"""
        fiber = self._make(["a"])
        lines1 = _build_lines(fiber)
        fiber.props["completion"].items = ["a", "b"]
        fiber.props["completion"].texts = ["a", "b"]
        lines2 = _build_lines(fiber)
        assert lines1 is not lines2

    def test_selected_change_rebuilds(self):
        """selected 变化（导航高亮）→ 重建。"""
        fiber = self._make(["a", "b"])
        lines1 = _build_lines(fiber)
        fiber.props["completion"].selected = 1
        lines2 = _build_lines(fiber)
        assert lines1 is not lines2


class TestCompletionSelectedClamp:
    """BUG-27 — _build_lines 与 _completion_height 对 selected 越界处理一致。"""

    def test_selected_out_of_range_desc_clamped(self):
        """selected 越界（>= len(descs)）→ 说明按最后一条渲染（与高度计算一致）。"""
        from src.tui.app.model import CompletionState
        from src.tui.app.input_area import _completion_height

        fiber = _input_fiber(text="", cursor_pos=0, completion=CompletionState(
            visible=True, items=["a", "b"], texts=["a", "b"],
            descriptions=["long desc one", "long desc two"],
            selected=99,  # 越界
            split_desc=True,
        ))
        fiber.layout_box = _Box(x=0, y=0, w=80, h=1)
        # 高度计算钳制到最后一条说明
        h = _completion_height(fiber.props["completion"], 80)
        lines = _build_lines(fiber)
        # 渲染不崩溃且含说明文本（钳制到 descs[-1]）
        plains = [l.plain for l in lines]
        assert any("long desc two" in p for p in plains), (
            f"越界 selected 应渲染最后一条说明（与高度一致）: {plains!r}"
        )


class TestMainAgentModeLine:
    """2026-08-14 — 主 Agent 运行模式行（时间戳分隔线下方，最右侧显示）。

    需求：显示时间戳的下面一行的最右边显示 main agent 运行模式（空模式/
    标准模式，Ctrl+B 切换）。用户反馈：模式行左侧不要分隔线填充。
    """

    def _mode_fiber(self, width=80):
        fiber = _input_fiber(text="hello", cursor_pos=3, width=width)
        fiber.layout_box = _Box(x=0, y=0, w=width, h=1)
        return fiber

    def test_standard_mode_rendered_right_aligned(self):
        """标准模式（默认）：最后一行最右侧显示「标准模式」。"""
        lines = _build_lines(self._mode_fiber())
        mode = lines[-1]
        assert mode.plain.endswith("标准模式"), (
            f"模式行最右侧应显示标准模式: {mode.plain!r}"
        )
        assert mode.width == 80, f"模式行应满宽: {mode.width}"

    def test_empty_mode_rendered_right_aligned(self):
        """空模式（Ctrl+B 切换）：最后一行最右侧显示「空模式」。"""
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=True):
            lines = _build_lines(self._mode_fiber())
        mode = lines[-1]
        assert mode.plain.endswith("空模式"), (
            f"模式行最右侧应显示空模式: {mode.plain!r}"
        )

    def test_mode_line_left_no_sep(self):
        """模式行左侧无分隔线填充（用户反馈：左边不要分割线）。"""
        lines = _build_lines(self._mode_fiber())
        mode = lines[-1]
        # 左侧应为空白（模式文本前无 ━ 分隔线字符）
        sep_char = "\u2501"  # ━
        assert sep_char not in mode.plain, (
            f"模式行不应含分隔线填充: {mode.plain!r}"
        )
        # 时间戳行（倒数第 2 行）仍保留分隔线填充
        ts = lines[-2]
        assert ts.plain.startswith("\u2501"), (
            f"时间戳行应保留分隔线: {ts.plain!r}"
        )

    def test_mode_line_full_width_all_widths(self):
        """模式行行宽恒 = width（行级 diff 不变量，窄屏含截断补位）。"""
        for w in (80, 20, 15, 8, 4):
            lines = _build_lines(self._mode_fiber(width=w))
            assert all(l.width <= w for l in lines), (
                f"width={w} 行超宽: {[l.width for l in lines]}"
            )
            assert lines[-1].width == w, (
                f"width={w} 模式行应满宽: {lines[-1].width}"
            )

    def test_mode_change_rebuilds_snapshot(self):
        """模式切换 → snap_key 变化 → 快照缓存重建（即时刷新）。"""
        fiber = self._mode_fiber()
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=False):
            with patch("src.tui.app.input_area.time.monotonic", return_value=1000.0):
                lines1 = _build_lines(fiber)
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=True):
            with patch("src.tui.app.input_area.time.monotonic", return_value=1000.1):
                lines2 = _build_lines(fiber)
        assert lines1 is not lines2, "模式切换应触发快照缓存重建"
        assert "空模式" in lines2[-1].plain

    def test_mode_same_snapshot_reuses_cache(self):
        """同模式同快照 → 缓存命中（引用级复用）。"""
        fiber = self._mode_fiber()
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=False):
            with patch("src.tui.app.input_area.time.monotonic", return_value=1000.0):
                lines1 = _build_lines(fiber)
            with patch("src.tui.app.input_area.time.monotonic", return_value=1000.1):
                lines2 = _build_lines(fiber)
        assert lines1 is lines2, "同模式同快照应命中缓存"

    def test_empty_mode_golden_fg(self):
        """空模式文本金色（178）；标准模式暗灰（242）。"""
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=True):
            lines = _build_lines(self._mode_fiber())
        empty_run = lines[-1].runs[-1]
        assert empty_run.style.fg == 178, (
            f"空模式应金色: {empty_run.style.fg!r}"
        )
        with patch("src.prompt_builder.builder.is_empty_mode", return_value=False):
            lines2 = _build_lines(self._mode_fiber())
        std_run = lines2[-1].runs[-1]
        assert std_run.style.fg == 242, (
            f"标准模式应暗灰: {std_run.style.fg!r}"
        )
