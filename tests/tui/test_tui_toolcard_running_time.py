"""工具卡运行时间前缀单元测试（2026-10-09 用户需求）。

需求：
  1. 运行中的工具卡标题行以**实时运行时间**（``0.1 UserSelect``）替代 ``●``
     图标，所有工具一致、实时刷新（0.1s 粒度）；
  2. 工具完成后**保留最终运行时间**（``13.4 UserSelect …``，不再显示 ✔/✖），
     且尾部元信息不再重复耗时（``· 13.4s`` 去掉）。

覆盖：
  - ``_format.format_elapsed`` 格式（<60s 纯数字 / ≥60s ``m:ss`` /
    ≥1h ``h:mm:ss`` / 非有限 ``-`` / 负值钳制）；
  - ``_tool_running_prefix_text`` / ``_tool_finished_prefix_text``；
  - ``_tool_icon_runs``（运行中时间前缀 / 完成后保留时间 / 无时间戳回退图标）；
  - ``tool_card_lines`` 标题行前缀（实时变化、所有注册工具、完成后保留）；
  - ``AppModel.refresh_running_tool_titles``（已增量提交标题行实时刷新）；
  - ``close_tool_box`` 关闭后标题行（保留最终时间、去除耗时元信息）；
  - 渲染帧循环接线（``_render_frame_impl`` 每帧调用刷新钩子）。
"""

from __future__ import annotations

import io
import time

from src.tui._format import format_elapsed
from src.tui.app.model import AppModel
from src.tui.app.toolcard import (
    _card_bg_style,
    _tool_finished_prefix_text,
    _tool_icon_runs,
    _tool_running_prefix_text,
    tool_card_lines,
)

_DOT = "\u25cf"
_CHECK = "\u2714"
_CROSS = "\u2716"


def _text(runs) -> str:
    return "".join(r.text for r in runs)


def _row_width(runs) -> int:
    return sum(r.width for r in runs)


class TestFormatElapsed:
    """``format_elapsed``：<60s 纯数字（无 s 后缀）；≥60s ``m:ss``。"""

    def test_sub_minute_plain_number(self):
        assert format_elapsed(0.0) == "0.0"
        assert format_elapsed(0.14) == "0.1"
        assert format_elapsed(59.0) == "59.0"

    def test_rounds_before_bucket_boundary(self):
        """59.96 → ``1:00``（不出现 ``60.0``）。"""
        assert format_elapsed(59.96) == "1:00"

    def test_minutes(self):
        assert format_elapsed(60.0) == "1:00"
        assert format_elapsed(65.4) == "1:05"
        assert format_elapsed(3599.0) == "59:59"

    def test_hours(self):
        assert format_elapsed(3600.0) == "1:00:00"
        assert format_elapsed(3725.0) == "1:02:05"

    def test_non_finite_and_negative(self):
        assert format_elapsed(float("inf")) == "-"
        assert format_elapsed(float("nan")) == "-"
        assert format_elapsed(-3.0) == "0.0"


class TestRunningPrefixText:
    """``_tool_running_prefix_text``：运行时间前缀文本（尾随空格）。"""

    def test_uses_started_at(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        text = _tool_running_prefix_text(block)
        assert text is not None and text.endswith(" ")
        assert text.strip().startswith("5.")

    def test_minute_format(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 65.0
        assert _tool_running_prefix_text(block).strip() == "1:05"

    def test_missing_timestamp_returns_none(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        del block.extra["_tool_started_at"]
        assert _tool_running_prefix_text(block) is None

    def test_invalid_timestamp_returns_none(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = "abc"
        assert _tool_running_prefix_text(block) is None
        block.extra["_tool_started_at"] = True
        assert _tool_running_prefix_text(block) is None


class TestFinishedPrefixText:
    """``_tool_finished_prefix_text``：完成后最终运行时间前缀文本。"""

    def test_from_recorded_duration(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_duration"] = 13.4
        assert _tool_finished_prefix_text(block) == "13.4 "

    def test_minute_format(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_duration"] = 65.0
        assert _tool_finished_prefix_text(block) == "1:05 "

    def test_missing_or_invalid_duration_returns_none(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        assert _tool_finished_prefix_text(block) is None
        block.extra["_tool_duration"] = "x"
        assert _tool_finished_prefix_text(block) is None
        block.extra["_tool_duration"] = float("nan")
        assert _tool_finished_prefix_text(block) is None


class TestIconRuns:
    """``_tool_icon_runs``：运行中/完成后均为运行时间；无时间戳回退图标。"""

    def test_running_shows_time_not_dot(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 2.0
        runs = _tool_icon_runs(block)
        assert _DOT not in runs[0].text
        assert runs[0].text.strip().startswith("2.")

    def test_fallback_dot_without_timestamp(self):
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        del block.extra["_tool_started_at"]
        assert _tool_icon_runs(block)[0].text == f"{_DOT} "

    def test_done_shows_check_and_final_time(self):
        """完成后 = ✔ + 最终运行时间（用户需求：``✔ 13.4 UserSelect``）。"""
        from src.tui.core.style import Style, StyleSheet

        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_duration"] = 13.4
        block.extra["tool_status"] = "done"
        runs = _tool_icon_runs(block)
        assert runs[0].text == f"{_CHECK} "
        assert runs[1].text == "13.4 "
        # 图标 + 时间同用状态语义色（成功绿——主题解析值）
        expected = StyleSheet.resolve("success", Style(fg=41)).fg
        assert runs[0].style.fg == expected
        assert runs[1].style.fg == expected

    def test_fail_shows_cross_and_final_time(self):
        from src.tui.core.style import Style, StyleSheet

        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_duration"] = 2.5
        block.extra["tool_status"] = "fail"
        runs = _tool_icon_runs(block)
        assert runs[0].text == f"{_CROSS} "
        assert runs[1].text == "2.5 "
        expected = StyleSheet.resolve("error", Style(fg=196, bold=True)).fg
        assert runs[0].style.fg == expected
        assert runs[1].style.fg == expected

    def test_done_fallback_glyph_without_duration(self):
        """无 ``_tool_duration``（旧块/外部构造）→ 回退仅 ✔/✖ 图标。"""
        m = AppModel()
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["tool_status"] = "done"
        assert [r.text for r in _tool_icon_runs(block)] == [f"{_CHECK} "]
        block.extra["tool_status"] = "fail"
        assert [r.text for r in _tool_icon_runs(block)] == [f"{_CROSS} "]


class TestToolCardTitleRunningTime:
    """``tool_card_lines`` 标题行：运行中前缀 = 实时运行时间。"""

    def test_title_prefix_is_time(self):
        from src.tools.registry import get_tool_display_name

        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "user_select", "q")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 3.0
        title = _text(tool_card_lines(block, 80)[0])
        assert title.startswith("3.")
        assert _DOT not in title
        assert (get_tool_display_name("user_select") or "user_select") in title

    def test_title_time_refreshes_when_started_changes(self):
        """帧缓存 key 含时间文本 → 时间推进不被旧缓存命中（实时刷新）。"""
        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 3.0
        first = _text(tool_card_lines(block, 80)[0])
        block.extra["_tool_started_at"] = time.monotonic() - 9.0
        second = _text(tool_card_lines(block, 80)[0])
        assert first.startswith("3.")
        assert second.startswith("9.")
        assert first != second

    def test_long_running_minutes_format(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "bash", "ls")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 65.0
        title = _text(tool_card_lines(block, 80)[0])
        assert title.startswith("1:05 ")

    def test_title_keeps_check_and_final_time_after_close(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "bash", "ls")
        m.append_tool_output("t", "out")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 4.0
        m.close_tool_box("t", True)
        title = _text(tool_card_lines(block, 80)[0])
        assert title.startswith(f"{_CHECK} 4.")
        assert "4.0s" not in title  # 尾部不重复耗时

    def test_all_registered_tools_show_time_prefix(self):
        from src.tools.registry import ToolRegistry

        names = list(ToolRegistry.default().get_tools() or [])
        assert names, "工具注册表为空（自动发现失败）"
        for name in names:
            m = AppModel()
            m.width = 80
            m.open_tool_box("t", name, "d")
            block = m.tool_boxes["t"]
            block.extra["_tool_started_at"] = time.monotonic() - 1.0
            title = _text(tool_card_lines(block, 80)[0])
            assert title.startswith("1."), (name, title)
            assert _DOT not in title, (name, title)


class _IncrementalRunningTool:
    """构造「运行中 + 已增量提交标题行」的工具卡（输出 > 阈值 64 行）。"""

    def __init__(self, width: int = 80, rows: int = 70):
        self.m = AppModel()
        self.m.width = width
        # custom_tool 不在 bash/head 修剪名单 → 输出不被 trim，触发增量提交
        self.m.open_tool_box("t", "custom_tool", "detail")
        self.m.append_tool_output("t", "\n".join(f"row {i}" for i in range(rows)))
        self.block = self.m.tool_boxes["t"]
        assert self.block.committed_line_count > 0, "未触发增量提交"
        self.offset = self.block.extra.get("_first_committed_offset")
        assert isinstance(self.offset, int)


class TestRefreshRunningToolTitles:
    """``AppModel.refresh_running_tool_titles``：已提交标题行实时刷新。"""

    def test_refresh_updates_committed_title(self):
        ctx = _IncrementalRunningTool()
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 3.0
        m.refresh_running_tool_titles()
        assert m.committed_lines[offset].plain.lstrip().startswith("3.")

        block.extra["_tool_started_at"] = time.monotonic() - 12.0
        m.refresh_running_tool_titles()
        assert m.committed_lines[offset].plain.lstrip().startswith("12.")

    def test_refresh_same_bucket_is_noop(self):
        ctx = _IncrementalRunningTool()
        m, block = ctx.m, ctx.block
        block.extra["_tool_started_at"] = time.monotonic() - 3.0
        m.refresh_running_tool_titles()
        snapshot = m.committed_lines
        assert _row_width(snapshot[ctx.offset].runs) <= 80
        m.refresh_running_tool_titles()  # 同 0.1s 桶 → 不替换
        assert m.committed_lines is snapshot

    def test_refresh_skips_unsubmitted_title(self):
        m = AppModel()
        m.width = 80
        m.open_tool_box("t", "custom_tool", "d")  # 无输出 → 标题行未提交
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        m.refresh_running_tool_titles()  # 不抛 + 不产生 committed 行
        assert m.committed_lines == []

    def test_refresh_skips_closed_block(self):
        ctx = _IncrementalRunningTool()
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        m.close_tool_box("t", True)
        title = m.committed_lines[offset].plain
        m.refresh_running_tool_titles()  # 已关闭 → 不变
        assert m.committed_lines[offset].plain == title
        assert title.lstrip().startswith(f"{_CHECK} 5.")

    def test_refresh_keeps_row_width_invariant(self):
        ctx = _IncrementalRunningTool(width=60)
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 65.0
        m.refresh_running_tool_titles()
        row = m.committed_lines[offset]
        assert _row_width(row.runs) == 60
        assert row.runs[-1].style is not None
        assert row.runs[-1].style.bg == _card_bg_style().bg


class TestCloseKeepsFinalTimePrefix:
    """``close_tool_box``：关闭后标题行 ``✔ <最终时间>`` + 去掉耗时元信息。"""

    def test_close_keeps_check_and_final_time(self):
        ctx = _IncrementalRunningTool()
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        m.refresh_running_tool_titles()
        assert m.committed_lines[offset].plain.lstrip().startswith("5.")
        m.close_tool_box("t", True)
        title = m.committed_lines[offset].plain
        assert title.lstrip().startswith(f"{_CHECK} 5.")
        assert _row_width(m.committed_lines[offset].runs) <= 80

    def test_close_keeps_cross_and_final_time_on_failure(self):
        ctx = _IncrementalRunningTool()
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        m.refresh_running_tool_titles()
        m.close_tool_box("t", False)
        title = m.committed_lines[offset].plain
        assert title.lstrip().startswith(f"{_CROSS} 5.")
        assert "\u5931\u8d25" in title  # 失败标记保留

    def test_close_drops_duration_meta_keeps_lines(self):
        ctx = _IncrementalRunningTool()
        m, block, offset = ctx.m, ctx.block, ctx.offset
        block.extra["_tool_started_at"] = time.monotonic() - 5.0
        m.close_tool_box("t", True)
        title = m.committed_lines[offset].plain
        assert f"{_CHECK} 5." in title  # 图标 + 时间保留
        assert "5.0s" not in title      # 耗时不显示
        assert "\u884c" in title        # 行数保留


class TestRenderFrameInvokesRefresh:
    """渲染帧循环每帧调用宿主刷新钩子（实时刷新的驱动源）。"""

    def test_frame_loop_invokes_refresh(self):
        from src.tui._config import TuiConfig
        from src.tui._screen import TerminalWidthCache
        from src.tui.ink import h, TEXT
        from src.tui.ink._render_api import _SimpleModel
        from src.tui.ink.session import InkSession

        calls: list = []

        class _ModelWithRefresh(_SimpleModel):
            def refresh_running_tool_titles(self) -> None:
                calls.append(1)

        session = InkSession(
            model=_ModelWithRefresh(),
            build_tree=lambda m, w: h(TEXT, {"children": "x"}),
            stream=io.StringIO(),
            width_cache=TerminalWidthCache(),
            config=TuiConfig.defaults(),
        )
        session.start()
        try:
            time.sleep(0.3)
        finally:
            session.stop()
        assert len(calls) >= 5, f"刷新钩子未按帧调用：{len(calls)}"


class TestEndToEndFrameRunningTime:
    """端到端：真实渲染帧中工具卡标题行显示运行时间（完成后保留）。"""

    def _frame(self, m: AppModel, width: int = 90):
        from src.tui.app.app import App
        from src.tui.ink import components as _components, h
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.renderer import InkRenderer

        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        rec.render(root, h(App, {"model": m, "width": width}), width, 40)
        frame = _components.render_frame(root, width)
        InkRenderer(stream=io.StringIO(), height=40).render(frame)
        return frame

    def test_frame_title_shows_running_time(self):
        m = AppModel()
        m.width = 90
        m.open_tool_box("t", "user_select", "q")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 4.0
        m.append_tool_output("t", "out")
        frame = self._frame(m)
        title = next(ln for ln in frame.lines if "UserSelect" in ln.plain)
        assert title.plain.lstrip().startswith("4.")
        assert "\u25cf" not in title.plain
        assert all(ln.width <= 90 for ln in frame.lines)

    def test_frame_title_keeps_check_and_final_time_after_close(self):
        m = AppModel()
        m.width = 90
        m.open_tool_box("t", "user_select", "q")
        m.append_tool_output("t", "out")
        block = m.tool_boxes["t"]
        block.extra["_tool_started_at"] = time.monotonic() - 2.0
        m.close_tool_box("t", True)
        frame = self._frame(m)
        title = next(ln for ln in frame.lines if "UserSelect" in ln.plain)
        assert title.plain.lstrip().startswith(f"{_CHECK} 2.")
        assert "\u25cf" not in title.plain
        assert all(ln.width <= 90 for ln in frame.lines)
