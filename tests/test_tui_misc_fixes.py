"""本轮 review 修复回归测试（提示符单一真源 / 换行行宽不变量 / 截断边界 /
切片偏移 / render(stdin) 还原 / 参数格式化兜底）。

覆盖以下修复（均不得改变既有对外行为）：

1. **提示符单一真源**（`_input_layout._prompt_of`）：修复前
   `app/input_area._build_lines` 硬编码 `_PROMPT`（渲染 + `max_input`），而
   `ink/_cursor.position_cursor` 读 `props["prompt"]` —— 双源；自定义/None
   提示符时渲染与光标错位（`str(None) == "None"` 更会算出错误的可用宽度）。
2. **`wrap_runs_by_width` 行宽不变量**：修复前行首单字符超宽（width=1 遇 CJK
   宽 2）产出超宽行（违反「每行最大显示宽度」契约，渲染层不做水平裁剪 →
   溢出列覆盖相邻单元格）。现对齐 `_input_layout._wrap_by_width` 的
   「宁可窄不可宽」语义（跳过放不下的字符，不产出超宽行）。
3. **`truncate_runs` 边界口径**：与同族统一为 `<= 0`（修复前 `< 0`）。
4. **`_paint_canvas._slice_line_with_offset`**：overflow 水平裁剪时宽字符跨
   左边界整体保留，绘制列回退到真实位置（修复前按裁剪左边界绘制 → 该行
   右移 1 列、尾部越界）。
5. **`_render_api.render` 启动失败还原调用方 stdin**（修复前仅恢复控制台补丁，
   stdin 仍指向已失败会话；且还原函数定义在 try 之后无法调用）。
6. **`param_formatter.extract_key_params` 非 dict 入参兜底**（修复前
   `arguments.get(k)` 抛 AttributeError）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui._input_layout import _DEFAULT_PROMPT, _prompt_of
from src.tui.app.input_area import _build_mode_line, _build_lines
from src.tui.ink import Line, StyledRun, h
from src.tui.ink._runs_utils import truncate_runs, wrap_runs_by_width
from src.tui.ink._paint_canvas import _slice_line_with_offset


# ── 1. 提示符单一真源 ────────────────────────────────────

class TestPromptSingleSource:
    def test_prompt_of_fallbacks(self):
        assert _prompt_of({}) == _DEFAULT_PROMPT
        assert _prompt_of({"prompt": None}) == _DEFAULT_PROMPT
        assert _prompt_of({"prompt": ""}) == _DEFAULT_PROMPT
        assert _prompt_of(None) == _DEFAULT_PROMPT
        assert _prompt_of({"prompt": ">> "}) == ">> "

    def test_prompt_of_non_str_normalized(self):
        assert _prompt_of({"prompt": 1}) == "1"

    def _fiber(self, props, width=40):
        return SimpleNamespace(
            props=props,
            layout_box=SimpleNamespace(w=width, x=0, y=0),
        )

    def test_build_lines_uses_props_prompt(self):
        fiber = self._fiber({"text": "", "prompt": ">> ", "completion": None})
        lines = _build_lines(fiber, include_popup=False)
        input_line = next(ln for ln in lines if ln.plain.startswith(">>"))
        assert input_line.plain.startswith(">> ")
        assert "> " not in input_line.plain[:2] or input_line.plain[:3] == ">> "

    def test_build_lines_large_prompt_reduces_input_budget(self):
        """提示符占宽参与 max_input 计算（修复前用硬编码 "> " 的宽度）。"""
        long_prompt = ">" * 10
        fiber = self._fiber(
            {"text": "abcdefghijklmnop", "prompt": long_prompt, "completion": None},
            width=14,
        )
        lines = _build_lines(fiber, include_popup=False)
        input_line = next(ln for ln in lines if ln.plain.startswith(">"))
        assert input_line.width <= 14


# ── 2. 换行行宽不变量 ────────────────────────────────────

class TestWrapRunsWidthInvariant:
    def test_first_char_wider_than_max_width_no_overwide_line(self):
        out = wrap_runs_by_width([StyledRun("中", None)], 1)
        assert all(ln.width <= 1 for ln in out), [ln.width for ln in out]

    def test_mixed_cjk_narrow_width(self):
        out = wrap_runs_by_width([StyledRun("a가b", None)], 1)
        assert all(ln.width <= 1 for ln in out)
        assert "".join(ln.plain for ln in out) == "ab"

    def test_exact_fit_unchanged(self):
        out = wrap_runs_by_width([StyledRun("中文", None)], 2)
        assert [ln.plain for ln in out] == ["中", "文"]
        out2 = wrap_runs_by_width([StyledRun("ab中", None)], 2)
        assert all(ln.width <= 2 for ln in out2)
        assert "".join(ln.plain for ln in out2) == "ab中"

    def test_no_infinite_loop_all_too_wide(self):
        out = wrap_runs_by_width([StyledRun("中文中文", None)], 1)
        assert all(ln.width <= 1 for ln in out)


# ── 3. 截断边界口径 ──────────────────────────────────────

class TestTruncateRunsBoundary:
    def test_non_positive_returns_empty(self):
        runs = [StyledRun("abc", None)]
        assert truncate_runs(runs, 0) == []
        assert truncate_runs(runs, -1) == []

    def test_positive_truncates_by_width(self):
        out = truncate_runs([StyledRun("abcd", None)], 2)
        assert "".join(r.text for r in out) == "ab"


# ── 4. 切片偏移（宽字符跨左裁剪边界） ───────────────────

class TestSliceLineWithOffset:
    def test_wide_char_straddling_left_boundary_offset(self):
        line = Line([StyledRun("中C", None)])
        sliced, offset = _slice_line_with_offset(line, 1, 3)
        assert sliced.plain == "中C"
        assert offset == -1, "宽字符跨左边界应返回 -1（绘制列回退到真实列）"

    def test_no_straddle_offset_zero(self):
        line = Line([StyledRun("abc", None)])
        sliced, offset = _slice_line_with_offset(line, 1, 3)
        assert sliced.plain == "bc"
        assert offset == 0

    def test_empty_slice_offset_zero(self):
        line = Line([StyledRun("abc", None)])
        sliced, offset = _slice_line_with_offset(line, 10, 12)
        assert sliced.plain == ""
        assert offset == 0

    def test_paint_draws_wide_char_at_true_column(self):
        """集成：clip 左边界落在宽字符中间时，宽字符绘制在真实列。

        修复前 draw_x=s（裁剪左边界）→ 整行右移 1 列；修复后回退到真实列。
        """
        from src.tui.ink.components import _paint

        fiber = SimpleNamespace(
            type="text",
            props={"styled": [StyledRun("中C", None)]},
            layout_box=SimpleNamespace(x=1, y=0, w=3, h=1),
            _wrapped_lines=[Line([StyledRun("中C", None)])],
            is_host=True,
        )
        canvas = [None]
        _paint(fiber, canvas, clip=(2, 0, 2, 1))
        row = canvas[0]
        assert row is not None
        # 中 起始列 = 1（真实列），C 起始列 = 3
        assert 1 in row, f"宽字符未落在真实列: {sorted(row)}"
        assert sorted(row)[0] == 1


# ── 5. render() 启动失败还原 stdin ───────────────────────

class _FakeStdin:
    def __init__(self):
        self._cb = "ORIGINAL"
        self._routable = False
        self.key_pressed_cb = None

    def get_interrupt_callback(self):
        return self._cb

    def is_interrupt_routable(self):
        return self._routable

    def set_interrupt_callback(self, cb):
        self._cb = cb

    def set_interrupt_routable(self, v):
        self._routable = v

    def set_key_pressed_callback(self, cb):
        self.key_pressed_cb = cb


class TestRenderStartFailureRestoresStdin:
    def test_start_failure_restores_caller_stdin(self):
        import src.tui.ink._render_api as api
        from src.tui.ink.session import InkSession

        stdin = _FakeStdin()

        def _boom(self):
            raise RuntimeError("start boom")

        original_start = InkSession.start
        InkSession.start = _boom
        try:
            with pytest.raises(RuntimeError):
                api.render(
                    h("text", {"children": "x"}),
                    stream=__import__("io").StringIO(),
                    stdin=stdin,
                    patchConsole=False,
                )
        finally:
            InkSession.start = original_start
        assert stdin.get_interrupt_callback() == "ORIGINAL", (
            "启动失败路径未还原调用方 stdin 的 interrupt 配置"
        )
        assert stdin.is_interrupt_routable() is False

    def test_no_stdin_no_error(self):
        import src.tui.ink._render_api as api
        from src.tui.ink.session import InkSession

        def _boom(self):
            raise RuntimeError("start boom")

        original_start = InkSession.start
        InkSession.start = _boom
        try:
            with pytest.raises(RuntimeError):
                api.render(
                    h("text", {"children": "x"}),
                    stream=__import__("io").StringIO(),
                    patchConsole=False,
                )
        finally:
            InkSession.start = original_start


# ── 6. 参数格式化非 dict 兜底 ───────────────────────────

class TestExtractKeyParamsNonDict:
    def test_list_input_no_raise(self):
        from src.core.param_formatter import extract_key_params

        out = extract_key_params("read_file", [1, 2, 3])
        assert isinstance(out, str)
        assert "1" in out

    def test_int_input_no_raise(self):
        from src.core.param_formatter import extract_key_params

        assert isinstance(extract_key_params("bash", 5), str)

    def test_none_and_empty_unchanged(self):
        from src.core.param_formatter import extract_key_params

        assert extract_key_params("read_file", None) == ""
        assert extract_key_params("read_file", "") == ""
        assert extract_key_params("read_file", {}) == ""


# ── 7. 模块导入冒烟（死导入清理后公开面不变） ───────────

def test_cursor_and_render_api_public_api_intact():
    from src.tui.ink import _cursor, _render_api

    assert callable(_cursor.find_input_fiber)
    assert callable(_cursor.position_cursor)
    assert callable(_render_api.render)
    assert callable(_render_api.measureElement)
    assert _render_api._SimpleModel is not None


def test_find_input_fiber_locates_data_input_area():
    from src.tui.app.input_area import InputArea
    from src.tui.ink import _cursor
    from src.tui.ink.reconciler import Reconciler
    import src.tui.ink.layout as _layout

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    element = h(InputArea, {
        "text": "hi", "cursor_pos": 2, "prompt": "> ",
        "completion": None, "status_active": False, "cpu": 1, "mem": 2,
        "width": 80, "key": "input-area",
    })
    rec.render(root, element, 80, 24)
    _layout.layout_tree(root, 80)
    fiber = _cursor.find_input_fiber(root)
    assert fiber is not None
    assert fiber.props.get("dataInputArea")


def test_build_mode_line_width_invariant():
    """模式行宽度恒 = width（行级 diff 行宽不变量），含行首前缀/极窄屏。"""
    for width in (0, 1, 2, 5, 12, 20, 33, 80, 120):
        for empty_mode in (True, False):
            for ctx, bash, sub in ((None, 0, 0), (45.3, 2, 1), (100.0, 0, 3)):
                line = _build_mode_line(
                    width, empty_mode,
                    ctx_percent=ctx, bash_count=bash, subagent_count=sub,
                )
                if width > 0:
                    assert line.width == width, (
                        f"模式行宽 {line.width} != width {width} "
                        f"(empty={empty_mode} ctx={ctx} bash={bash} sub={sub})"
                    )
                else:
                    assert line.width >= 0


def test_build_mode_line_contains_prefix_and_mode_text():
    line = _build_mode_line(80, False, ctx_percent=45.3, bash_count=1, subagent_count=1)
    plain = line.plain
    assert "main" in plain and "45.3%" in plain
    assert "bash" in plain and "subagent" in plain
    assert "标准模式" in plain
    assert "空模式" in _build_mode_line(80, True).plain
