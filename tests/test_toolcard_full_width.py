"""工具卡整行占满终端宽度（2026-10-05 用户需求）回归测试。

需求：
  1. 工具卡标题行参数显示到终端宽度——core ``extract_key_params`` 不再做
     固定字符截断（原已知工具 ≤60 / 未知工具 ≤80），标题行按终端宽度
     ``truncate_runs`` 截断；
  2. 工具卡整行（标题/内容/省略行）右侧以背景色空格填充，延伸到终端右边缘
     （即使内容较短，行显示宽度 == 终端宽度）；
  3. 终端宽度变化（resize）→ 工具卡按新宽度重排（随窗口加宽同步变宽）；
  4. 内容行按终端宽度换行/截断到终端宽度。
"""

from __future__ import annotations

from src.core.param_formatter import extract_key_params
from src.tui.app.model import AppModel
from src.tui.app.toolcard import _card_bg_style, tool_card_lines


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

