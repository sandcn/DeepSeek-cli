"""工具卡整行占满终端宽度（2026-10-05 用户需求）回归测试。

需求：
  1. 工具卡标题行参数显示到终端宽度——core ``extract_key_params`` 不再做
     固定字符截断（原已知工具 ≤60 / 未知工具 ≤80），标题行按终端宽度
     ``truncate_runs`` 截断；
  2. 工具卡整行（标题/内容/省略行）右侧以背景色空格填充，延伸到终端右边缘
     （即使内容较短，行显示宽度 == 终端宽度）；
  3. 终端宽度变化（resize）→ 工具卡按新宽度重排（随窗口加宽同步变宽）；
  4. 内容行按终端宽度换行/截断到终端宽度；
  5. 显示一行超过终端宽度就截断到最大宽度（``_apply_line_bg`` 硬上限钳制，
     2026-10-05 用户需求）。
"""

from __future__ import annotations

from src.core.param_formatter import extract_key_params
from src.tui.app.model import AppModel
from src.tui.app.toolcard import _apply_line_bg, _card_bg_style, tool_card_lines


def _row_width(runs) -> int:
    """一行 runs 的显示宽度。"""
    return sum(r.width for r in runs)


def _last_run_bg(runs):
    """行尾填充 run 的背景色号（无填充返回 None）。"""
    if not runs:
        return None
    style = runs[-1].style
    return None if style is None else style.bg


class TestNoFixedTruncation:
    """core 参数提取不再固定截断（标题行参数显示到终端宽度）。"""

    def test_known_tool_long_value_kept_whole(self):
        assert extract_key_params("bash", {"command": "a" * 300}) == "a" * 300

    def test_unknown_tool_long_value_kept_whole(self):
        out = extract_key_params("unknown_tool", {"k": "b" * 300})
        assert out == "k=" + "b" * 300

    def test_raw_non_json_kept_whole(self):
        assert extract_key_params("read_file", "c" * 200) == "c" * 200


class TestToolCardFillsTerminalWidth:
    """工具卡每行占满终端宽度（背景色填充到右边缘）。"""

    def test_long_detail_title_reaches_width(self):
        m = AppModel()
        m.width = 120
        m.open_tool_box("t1", "bash", "cd /home/lmy/simple/mc/src && " + "x" * 200)
        block = m.tool_boxes["t1"]
        rows = tool_card_lines(block, 120)
        assert _row_width(rows[0]) == 120
        assert _last_run_bg(rows[0]) == _card_bg_style().bg

    def test_short_detail_title_still_reaches_width(self):
        m = AppModel()
        m.width = 100
        m.open_tool_box("t2", "bash", "ls")
        block = m.tool_boxes["t2"]
        rows = tool_card_lines(block, 100)
        assert _row_width(rows[0]) == 100
        assert _last_run_bg(rows[0]) == _card_bg_style().bg

    def test_content_rows_reach_width(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t3", "bash", "pwd")
        m.append_tool_output("t3", "short")
        block = m.tool_boxes["t3"]
        rows = tool_card_lines(block, 80)
        assert len(rows) == 2
        assert all(_row_width(r) == 80 for r in rows)
        assert all(_last_run_bg(r) == _card_bg_style().bg for r in rows)

    def test_bash_omitted_line_reaches_width(self):
        """省略提示行同样填充到终端宽度。"""
        m = AppModel()
        m.width = 70
        m.open_tool_box("t4", "bash", "run")
        # bash tail 修剪：输出 > 3 行 → 前置「… 前 N 行省略」提示行
        m.append_tool_output("t4", "\n".join(f"line-{i}" for i in range(20)))
        block = m.tool_boxes["t4"]
        rows = tool_card_lines(block, 70)
        assert any("行省略" in "".join(r.text for r in row) for row in rows)
        assert all(_row_width(r) == 70 for r in rows)

    def test_long_parameter_shown_past_60_chars(self):
        """长参数在标题行显示远超 60 字符（不再提前截断）。"""
        m = AppModel()
        m.width = 120
        m.open_tool_box("t5", "bash", "a" * 300)
        block = m.tool_boxes["t5"]
        title = "".join(r.text for r in tool_card_lines(block, 120)[0])
        assert "a" * 100 in title
        assert _row_width(tool_card_lines(block, 120)[0]) == 120


class TestToolCardResizeSelfAdapt:
    """终端 resize：工具卡按新宽度重排（自适应加宽）。"""

    def test_reflow_widens_committed_tool_card(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t6", "bash", "ls")
        m.append_tool_output("t6", "out")
        m.close_tool_box("t6", True)
        # 初始提交：宽度 80
        assert m.committed_lines[0].width == 80
        m.reflow_committed(140)
        # resize 后重排：标题行达到新终端宽度
        assert m.committed_lines[0].width == 140
        assert all(ln.width <= 140 for ln in m.committed_lines)

    def test_tool_card_lines_track_width_change(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t7", "bash", "ls")
        block = m.tool_boxes["t7"]
        assert _row_width(tool_card_lines(block, 80)[0]) == 80
        # 同一块按新宽度渲染（缓存 key 含宽度 → 不返回旧行）
        assert _row_width(tool_card_lines(block, 150)[0]) == 150
        assert _row_width(tool_card_lines(block, 80)[0]) == 80

    def test_narrow_width_defensive(self):
        m = AppModel()
        m.width = 1
        m.open_tool_box("t8", "bash", "ls")
        block = m.tool_boxes["t8"]
        rows = tool_card_lines(block, 1)
        assert all(_row_width(r) <= 1 for r in rows)

    def test_background_renders_to_ansi(self):
        """背景色真正进入 ANSI 渲染输出（整行满宽背景可见）。"""
        from src.tui.ink import Line
        m = AppModel()
        m.width = 60
        m.open_tool_box("t9", "bash", "ls")
        block = m.tool_boxes["t9"]
        rendered = Line(tool_card_lines(block, 60)[0]).render()
        assert "\033[48;5;236m" in rendered


class TestEndToEndFrameWidth:
    """端到端：真实渲染帧中工具卡行达到终端宽度。"""

    def test_frame_tool_card_reaches_terminal_width(self):
        import io

        from src.tui.app.app import App
        from src.tui.ink import components as _components, h
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.renderer import InkRenderer

        m = AppModel()
        m.width = 90
        m.open_tool_box("t", "bash", "echo hello")
        m.append_tool_output("t", "hello")
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=40)
        element = h(App, {"model": m, "width": 90})
        rec.render(root, element, 90, 40)
        frame = _components.render_frame(root, 90)
        renderer.render(frame)
        card_rows = [ln for ln in frame.lines if "Bash" in ln.plain or "\u2502" in ln.plain]
        assert card_rows, "未找到工具卡渲染行"
        assert all(ln.width <= 90 for ln in frame.lines)
        title = next(ln for ln in frame.lines if "Bash" in ln.plain)
        assert title.width == 90


class TestToolCardHardTruncateToMaxWidth:
    """显示一行超过终端宽度就截断到最大宽度 + 末尾省略号 ``…``。"""

    def _bg(self):
        return _card_bg_style()

    def test_ascii_overwide_truncated_to_width_with_ellipsis(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        runs = [StyledRun("x" * 50, Style(fg=1))]
        out = _apply_line_bg(runs, 20, self._bg())
        assert _row_width(out) == 20
        assert "".join(r.text for r in out).startswith("x" * 19 + "\u2026")
        assert _last_run_bg(out) == self._bg().bg

    def test_cjk_overwide_truncated_without_splitting_wide_char(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        # 30 个全角字符（宽 60）截断到宽 15 → 保留 7 个宽字符（14）+ ``…``
        runs = [StyledRun("中" * 30, Style(fg=2))]
        out = _apply_line_bg(runs, 15, self._bg())
        assert _row_width(out) == 15
        assert "".join(r.text for r in out) == "中" * 7 + "\u2026"

    def test_underwidth_runs_unchanged_and_padded_no_ellipsis(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        runs = [StyledRun("abc", Style(fg=3))]
        out = _apply_line_bg(runs, 10, self._bg())
        assert out[0].text == "abc"
        assert _row_width(out) == 10
        assert "\u2026" not in "".join(r.text for r in out)

    def test_exact_width_no_extra_fill_no_ellipsis(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        runs = [StyledRun("z" * 8, Style(fg=4))]
        out = _apply_line_bg(runs, 8, self._bg())
        assert _row_width(out) == 8
        assert len(out) == 1
        assert "\u2026" not in out[0].text

    def test_nonpositive_width_returns_input_identity(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        runs = [StyledRun("x" * 50, Style(fg=1))]
        assert _apply_line_bg(runs, 0, self._bg()) is runs

    def test_mixed_style_truncation_keeps_bg_and_ellipsis(self):
        from src.tui.core.style import Style
        from src.tui.ink import StyledRun

        runs = [
            StyledRun("\u2714 ", Style(fg=41)),
            StyledRun("name " + "y" * 60, Style(fg=81, bold=True)),
        ]
        out = _apply_line_bg(runs, 24, self._bg())
        assert _row_width(out) == 24
        assert all(r.style is not None for r in out)
        assert "".join(r.text for r in out).endswith("\u2026")

    def test_title_line_truncated_with_ellipsis(self):
        """工具卡标题行超宽 → 截断到 width 且末尾 ``…``。"""
        m = AppModel()
        m.width = 40
        m.open_tool_box("t", "bash", "a" * 300)
        block = m.tool_boxes["t"]
        title = "".join(r.text for r in tool_card_lines(block, 40)[0])
        assert title.endswith("\u2026")
        assert _row_width(tool_card_lines(block, 40)[0]) == 40

    def test_title_line_short_no_ellipsis(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        title = "".join(r.text for r in tool_card_lines(block, 80)[0])
        assert "\u2026" not in title

    def test_fuzz_rows_never_exceed_width(self):
        """任意内容/宽度下工具卡行宽恒 <= width（硬上限不变量）。"""
        import random

        random.seed(11)
        chars = "aA1 \t中文字🙂-_.:/"
        for _ in range(400):
            width = random.choice([1, 2, 3, 5, 8, 13, 21, 40, 80])
            detail = "".join(random.choice(chars) for _ in range(random.randint(0, 60)))
            m = AppModel()
            m.width = width
            m.open_tool_box("t", "bash", detail)
            m.append_tool_output(
                "t", "".join(random.choice(chars) for _ in range(random.randint(0, 120))),
            )
            block = m.tool_boxes["t"]
            for row in tool_card_lines(block, width):
                assert _row_width(row) <= width


def _registered_tool_names() -> list:
    """全部已注册工具名（工具卡渲染路径对每个工具都应生效）。"""
    from src.tools.registry import ToolRegistry

    try:
        return list(ToolRegistry.default().get_tools() or [])
    except Exception:
        return []


class TestAllToolsToolCardTruncate:
    """所有工具的 toolcard 都走同一渲染路径（超宽截断 + 末尾省略号）。"""

    def test_all_registered_tools_title_truncated_with_ellipsis(self):
        from src.tools.registry import get_tool_display_name

        names = _registered_tool_names()
        assert names, "工具注册表为空（自动发现失败）"
        for name in names:
            for width in (24, 40, 80):
                m = AppModel()
                m.width = width
                m.open_tool_box("t", name, "z" * 300)
                block = m.tool_boxes["t"]
                title_row = tool_card_lines(block, width)[0]
                assert _row_width(title_row) == width, (name, width)
                title = "".join(r.text for r in title_row)
                assert title.endswith("\u2026"), (name, width, title)
                assert (get_tool_display_name(name) or name) in title

    def test_all_registered_tools_rows_within_width(self):
        names = _registered_tool_names()
        assert names, "工具注册表为空（自动发现失败）"
        for name in names:
            for width in (13, 40, 100):
                m = AppModel()
                m.width = width
                m.open_tool_box("t", name, "z" * 200)
                m.append_tool_output("t", "y" * 500 + "\n" + "中" * 100)
                block = m.tool_boxes["t"]
                for row in tool_card_lines(block, width):
                    assert _row_width(row) <= width, (name, width)

    def test_all_registered_tools_frame_within_width(self):
        """端到端：每个注册工具的真实渲染帧行宽 <= 终端宽度。"""
        import io

        from src.tui.app.app import App
        from src.tui.ink import components as _components, h
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.renderer import InkRenderer

        names = _registered_tool_names()
        assert names, "工具注册表为空（自动发现失败）"
        for name in names:
            for width in (40, 90):
                m = AppModel()
                m.width = width
                m.open_tool_box("t", name, "z" * 300)
                m.append_tool_output("t", "y" * 400)
                rec = Reconciler(schedule_callback=None)
                root = rec.create_root()
                rec.render(root, h(App, {"model": m, "width": width}), width, 60)
                frame = _components.render_frame(root, width)
                InkRenderer(stream=io.StringIO(), height=60).render(frame)
                assert all(ln.width <= width for ln in frame.lines), (name, width)
                assert any(ln.plain.endswith("\u2026") for ln in frame.lines), (
                    name, width,
                )

