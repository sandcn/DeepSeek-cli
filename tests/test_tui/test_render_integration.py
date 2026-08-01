"""test_render_integration — Render 线程 stdin 集成测试。

验证 InkSession._drain_queue() 在每帧渲染周期中正确调用
Input.process_events() → read_stdin_once()，
且 stdin 读取在 output_lock 之外执行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.tui.input import Input
from src.tui.ink.session import InkSession
from src.tui.app.model import AppModel


class TestRenderIntegration:
    """测试 InkSession._drain_queue() 与 Input.read_stdin_once() 的集成。"""

    @pytest.fixture
    def mock_input(self, tmp_path: Path) -> Input:
        """创建 mock Input 实例。"""
        fd = os.open("/dev/null", os.O_RDONLY)
        try:
            inp = Input(fd=fd, history_file=tmp_path / "history")
            yield inp
        finally:
            os.close(fd)

    def test_drain_queue_calls_process_events(self, mock_input: Input) -> None:
        """验证 InkSession._drain_queue() 调用 Input.process_events()。"""
        session = InkSession(model=AppModel())
        mock_input.process_events = MagicMock()
        session._input = mock_input
        session._phase_process_input()
        mock_input.process_events.assert_called_once()

    def test_drain_queue_without_input_no_crash(self) -> None:
        """验证 InkSession._drain_queue() 在无 _input 时不崩溃（向后兼容）。"""
        session = InkSession(model=AppModel())
        session._input = None
        session._phase_process_input()  # 不应抛异常

    def test_process_events_calls_read_stdin_once(self, mock_input: Input, tmp_path: Path) -> None:
        """验证 Input.process_events() 委托 read_stdin_once()。"""
        r_fd, w_fd = os.pipe()
        try:
            inp = Input(fd=r_fd, history_file=tmp_path / "test_history")
            os.write(w_fd, b"x")
            time.sleep(0.05)
            inp.process_events()
            assert inp.get_current_text() == "x"
        finally:
            os.close(w_fd)
            os.close(r_fd)

    def test_stdin_read_outside_output_lock(self) -> None:
        """验证 _phase_process_input() 在 output_lock 之前执行。"""
        import inspect

        source = inspect.getsource(InkSession._drain_queue)
        phase_pos = source.find("_phase_process_input()")
        lock_pos = source.find("_try_acquire_output_lock")
        assert phase_pos >= 0, "_phase_process_input() 应在 _drain_queue 中"
        assert lock_pos >= 0, "_try_acquire_output_lock 应在 _drain_queue 中"
        assert phase_pos < lock_pos, (
            "_phase_process_input() 应在 _try_acquire_output_lock 之前调用"
        )


# ═══════════════════════════════════════════════════════════
# 横切步骤18 — 组件树渲染含新 props（history_search / 工具状态）
# ═══════════════════════════════════════════════════════════

class TestRenderIntegrationNewProps:
    """横切步骤18 — build_app_element 传入新 props 后帧输出正确。"""

    def _render_frame_to_stream(self, model, width=80):
        """渲染一帧到 StringIO，返回 (stream, session)。"""
        import io

        from src.tui.ink.session import InkSession
        from src.tui.app.app import build_app_element
        from src.tui._config import TuiConfig

        cache = MagicMock()
        cache.get_width.return_value = width
        cache.get_height.return_value = 24
        stream = io.StringIO()
        session = InkSession(
            model=model,
            apply_cmd=None,
            build_tree=build_app_element,
            width_cache=cache,
            config=TuiConfig.defaults(),
            stream=stream,
        )
        session._render_frame()
        return stream, session

    def test_history_search_props_renders_search_line_regression(self):
        """history_search 激活时帧输出含 (reverse-i-search) 覆盖行。"""
        from src.tui.app.model import AppModel, HistorySearchState

        model = AppModel()
        model.history_search = HistorySearchState(
            query="foo", matches=["foobar"], index=0, active=True,
        )
        stream, session = self._render_frame_to_stream(model)
        try:
            out = stream.getvalue()
            assert "(reverse-i-search)" in out
            assert "foo" in out
        finally:
            session.stop()

    def test_history_search_inactive_no_search_line_regression(self):
        """history_search 为 None（未激活）时帧输出不含搜索覆盖行。"""
        from src.tui.app.model import AppModel

        model = AppModel()
        model.history_search = None
        stream, session = self._render_frame_to_stream(model)
        try:
            assert "(reverse-i-search)" not in stream.getvalue()
        finally:
            session.stop()

    def test_tool_card_status_props_render_regression(self):
        """工具块（done）超长输出完整渲染到帧输出（无折叠/截断）。"""
        from src.tui._const import ToolOpenCmd, ToolOutputCmd, ToolCloseCmd
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tools.registry import get_tool_display_name

        model = AppModel()
        apply_cmd(model, ToolOpenCmd(tool_id="t9", tool_name="bash"))
        for i in range(10):
            apply_cmd(model, ToolOutputCmd(tool_id="t9", text=f"line{i}"))
        apply_cmd(model, ToolCloseCmd(tool_id="t9", success=True))

        stream, session = self._render_frame_to_stream(model)
        try:
            out = stream.getvalue()
            # 超长输出完整显示（首尾行均在帧输出中，无折叠/截断）
            assert "line0" in out
            assert "line9" in out
            # 工具显示名标题渲染（工具名经 registry 显示名映射）
            display = get_tool_display_name("bash") or "bash"
            assert display in out
        finally:
            session.stop()


# ═══════════════════════════════════════════════════════════
# 方向1 P0-1 — 超宽行按 width wrap（committed 发射前）
# ═══════════════════════════════════════════════════════════

class TestOverwidthLineWrap:
    """方向1 P0-1 — 超宽行在 ``_block_to_ink_lines`` 按 width wrap（行级 diff 正确性）。"""

    def test_overwidth_line_wrapped_regression(self):
        """超宽 ASCII 行 append_committed 后 committed_lines 各行 ≤ width 且总行数正确。"""
        from src.renderer.ansi.helpers import AnsiLine
        from src.renderer.ansi.style import Style

        model = AppModel()
        model.width = 40
        line = AnsiLine.of("a" * 100, Style(fg=1))  # 100 列 ASCII，超宽
        model.append_committed("content", [line])
        assert len(model.committed_lines) == 3, (
            f"100 列 / width 40 应拆 3 行（40+40+20），实际 {len(model.committed_lines)}"
        )
        for ln in model.committed_lines:
            assert ln.width <= 40, f"committed ink Line 宽度 {ln.width} 应 <= 40"

    def test_overwidth_cjk_line_wrapped_regression(self):
        """超宽 CJK 行 append_committed 后 committed_lines 各行 ≤ width（不拆宽字符）。"""
        from src.renderer.ansi.helpers import AnsiLine
        from src.renderer.ansi.style import Style

        model = AppModel()
        model.width = 40
        line = AnsiLine.of("你" * 30, Style(fg=2))  # 30×2=60 列，超宽
        model.append_committed("content", [line])
        assert len(model.committed_lines) == 2, (
            f"60 列 / width 40 应拆 2 行（20+10），实际 {len(model.committed_lines)}"
        )
        for ln in model.committed_lines:
            assert ln.width <= 40, f"committed ink Line 宽度 {ln.width} 应 <= 40"

    def test_normal_width_line_unwrapped_regression(self):
        """宽度 ≤ width 的普通行行为不变（零回归：wrap 仅超宽行触发）。"""
        from src.renderer.ansi.helpers import AnsiLine
        from src.renderer.ansi.style import Style

        model = AppModel()
        model.width = 40
        line = AnsiLine.of("hello", Style(fg=1))
        model.append_committed("content", [line])
        assert len(model.committed_lines) == 1
        assert model.committed_lines[0].width == 5


# ═══════════════════════════════════════════════════════════
# 方向1 步骤1.6 — 长工具输出关闭后 committed_lines 标题行图标更新
# ═══════════════════════════════════════════════════════════

class TestToolTitleIconUpdate:
    """方向1 步骤1.6 — 长工具输出关闭后 committed_lines 标题行图标由 ● 更新为 ✔/✖。

    修复前：长工具输出（> _TOOL_INCREMENTAL_THRESHOLD）触发 commit_open_block
    增量提交时标题行已进入 committed_lines（状态 running ●）；close_tool_box
    仅冻结/提交未提交尾（不含标题行）→ committed_lines 标题行恒 ●。
    修复后：首次提交记录 ``_first_committed_offset``，close 时更新该行图标。
    """

    def _open_long_tool(self, tool_id, tool_name="bash", output_lines=None):
        from src.tui.app.model import AppModel, _TOOL_INCREMENTAL_THRESHOLD

        model = AppModel()
        model.open_tool_box(tool_id, tool_name)
        for i in range(_TOOL_INCREMENTAL_THRESHOLD + 1):
            model.append_tool_output(tool_id, f"line{i}")
        return model

    def test_tool_title_icon_updates_after_close_regression(self):
        """> _TOOL_INCREMENTAL_THRESHOLD 行工具输出关闭后 committed_lines 标题行
        图标由 ● 更新为 ✔（修复前恒 ●）。"""
        from src.tui.app.model import AppModel, _TOOL_INCREMENTAL_THRESHOLD

        model = self._open_long_tool("t1", "bash")
        block = model.blocks[-1]
        offset = block.extra.get("_first_committed_offset")
        assert offset is not None, "增量提交应记录 _first_committed_offset"
        assert 0 <= offset < len(model.committed_lines), "偏移应指向 committed_lines 内"
        # 关闭前：标题行图标为 running ●
        assert model.committed_lines[offset].plain.startswith("\u25cf"), (
            "关闭前 committed_lines 标题行应为 running ●"
        )
        model.close_tool_box("t1", True)
        # 关闭后：标题行图标为 done ✔
        assert model.committed_lines[offset].plain.startswith("\u2714"), (
            "关闭后 committed_lines 标题行图标应更新为 ✔"
        )
        # 标题文本保留（图标替换不丢失标题内容；bash 显示名经 registry 缩写为 bs）
        assert len(model.committed_lines[offset].plain) > len("\u2714"), (
            "标题行图标更新后应保留标题文本"
        )

    def test_tool_title_icon_fail_updates_after_close_regression(self):
        """fail 场景：长工具输出关闭后标题行图标更新为 ✖。"""
        from src.tui.app.model import AppModel, _TOOL_INCREMENTAL_THRESHOLD

        model = self._open_long_tool("t2", "bash")
        block = model.blocks[-1]
        offset = block.extra.get("_first_committed_offset")
        assert offset is not None
        model.close_tool_box("t2", False)
        assert model.committed_lines[offset].plain.startswith("\u2716"), (
            "关闭后 committed_lines 标题行图标应更新为 ✖"
        )

    def test_short_tool_title_icon_no_offset_unchanged(self):
        """短工具输出（未增量提交）关闭后 committed_lines 标题行直接带 ✔（行为不变）。"""
        from src.tui.app.model import AppModel

        model = AppModel()
        model.open_tool_box("t3", "read_file")
        model.append_tool_output("t3", "brief")
        model.close_tool_box("t3", True)
        # 关闭后标题行（committed_lines 首行）直接带 ✔——未触发增量提交时
        # close 经 commit_block 提交的标题行已带 done 图标，无需 offset 更新路径。
        assert model.committed_lines, "关闭后 committed_lines 不应为空"
        assert model.committed_lines[0].plain.startswith("\u2714"), (
            "短工具关闭后 committed_lines 标题行应为 ✔"
        )


# ═══════════════════════════════════════════════════════════
# 方向1 步骤4 — chat_view 非顶部 committed-prefix 缓存
# ═══════════════════════════════════════════════════════════

class TestCommittedPrefixNonTop:
    """方向1 步骤4 — chat_view._paint 非顶部路径接入 committed-prefix 缓存。

    修复前 box.y != 0 分支每帧 O(n) 引用缓存行且不设 _committed_prefix →
    render_frame 全量重建画布。修复后非顶部同样维护 _committed_prefix
    （key 含 box.y），命中即跳过画布重写；render_frame 消费守卫校验
    layout_box.y == 0（非顶部填前缀后全量转换，committed 行不丢失）。
    """

    def _make_root(self, lines, header_text: str = "header"):
        """构造非顶部 committed-chat（上方 header 占 y=0 → committed 在 y>=1）。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        import src.tui.app.chat_view as _cv
        _cv.register()  # 幂等：注册 committed-chat host
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": header_text}),
            h("committed-chat", {"lines": lines}),
            h(TEXT, {"children": "tail"}),
        ])
        return r, root, el

    def test_committed_prefix_non_top_regression(self):
        """非顶部 committed-chat：帧内容与目标一致 + 前缀缓存 key 含 box.y。"""
        from src.tui.ink.output import StyledRun, Line
        from src.tui.ink import components as _components
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(100)]
        r, root, el = self._make_root(lines)
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        # 顶部 header + 100 committed + tail
        assert f1.height == 102
        assert f1.lines[0].plain == "header"
        assert f1.lines[1].plain == "line 0"
        assert f1.lines[100].plain == "line 99"
        assert f1.lines[101].plain == "tail"
        # 非顶部 committed-chat 维护 _committed_prefix（key 含 box.y=1）
        cc = _components._find_committed_chat(root)
        assert cc is not None
        assert cc._committed_prefix is not None, "非顶部 committed 应维护前缀缓存"
        assert cc._committed_prefix[0][2] == 1, (
            f"前缀键应含 box.y（非顶部），实际 box.y={cc._committed_prefix[0][2]}"
        )
        # 同引用再渲染 → 前缀命中（跳过画布重写），帧内容仍一致
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert [ln.plain for ln in f2.lines] == [ln.plain for ln in f1.lines]
        # committed 行 Line 对象身份复用（非顶部路径不再每帧重建）
        assert f2.lines[1] is f1.lines[1]
        assert f2.lines[100] is f1.lines[100]

    def test_committed_prefix_non_top_growth_regression(self):
        """非顶部 committed_lines 原地增长 → 前缀增量追加（帧内容正确）。"""
        from src.tui.ink.output import StyledRun, Line
        from src.tui.ink import components as _components
        lines = [Line([StyledRun(f"line {i}", None)]) for i in range(10)]
        r, root, el = self._make_root(lines)
        r.render(root, el, 80, 24)
        f1 = _components.render_frame(root, 80)
        assert f1.height == 12  # header + 10 + tail
        # 原地 extend（增量提交）
        lines.extend(Line([StyledRun(f"new {i}", None)]) for i in range(3))
        r.render(root, el, 80, 24)
        f2 = _components.render_frame(root, 80)
        assert f2.height == 15
        assert f2.lines[11].plain == "new 0"
        assert f2.lines[13].plain == "new 2"
        assert f2.lines[14].plain == "tail"
