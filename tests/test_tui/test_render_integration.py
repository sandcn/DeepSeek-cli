"""test_render_integration — Render 线程 stdin 集成测试。

验证 InkSession._drain_queue() 在每帧渲染周期中正确调用
Input.process_events() → read_stdin_once()，
且 stdin 读取在 output_lock 之外执行。
"""

from __future__ import annotations

import io
import os
import re
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
        """工具块（done，非 bash）超长输出完整渲染到帧输出（无折叠/截断）。"""
        from src.tui._const import ToolOpenCmd, ToolOutputCmd, ToolCloseCmd
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tools.registry import get_tool_display_name

        model = AppModel()
        apply_cmd(model, ToolOpenCmd(tool_id="t9", tool_name="web_search"))
        for i in range(10):
            apply_cmd(model, ToolOutputCmd(tool_id="t9", text=f"line{i}"))
        apply_cmd(model, ToolCloseCmd(tool_id="t9", success=True))

        stream, session = self._render_frame_to_stream(model)
        try:
            out = stream.getvalue()
            # 超长输出完整显示（首尾行均在帧输出中，无折叠/截断；bash/read_file
            # 等修剪工具例外——bash 按最后 3 行尾显示，find/search/ls/read_file
            # 按前 3 行头显示，见 TestBashTailDisplay / head 显示测试）
            assert "line0" in out
            assert "line9" in out
            # 工具显示名标题渲染（工具名经 registry 显示名映射）
            display = get_tool_display_name("web_search") or "web_search"
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
        # 卡片结构：3 行 wrap 正文 + 卡片尾空行 = 4（content 无角色头）
        assert len(model.committed_lines) == 4, (
            f"100 列 / width 40 应拆 3 行（40+40+20）+ 空行，实际 {len(model.committed_lines)}"
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
        # 卡片结构：2 行 wrap 正文 + 卡片尾空行 = 3（content 无角色头）
        assert len(model.committed_lines) == 3, (
            f"60 列 / width 40 应拆 2 行（20+10）+ 空行，实际 {len(model.committed_lines)}"
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
        # 卡片结构：正文 + 卡片尾空行 = 2（content 无角色头）；正文（下标 0）宽度 == 5
        assert len(model.committed_lines) == 2
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

        # web_search（非 bash/头显示工具，不触发截断）→ 保持增量提交行为
        model = self._open_long_tool("t1", "web_search")
        block = model.blocks[-1]
        offset = block.extra.get("_first_committed_offset")
        assert offset is not None, "增量提交应记录 _first_committed_offset"
        assert 0 <= offset < len(model.committed_lines), "偏移应指向 committed_lines 内"
        # 卡片结构：offset 指向工具卡片顶边框（`┌─` 起，含状态图标）
        assert model.committed_lines[offset].plain.startswith("\u250c"), (
            "offset 应指向工具卡片顶边框"
        )
        # 关闭前：顶边框状态图标为 running ●
        assert "\u25cf" in model.committed_lines[offset].plain, (
            "关闭前 committed_lines 顶边框应为 running ●"
        )
        model.close_tool_box("t1", True)
        # 关闭后：顶边框状态图标为 done ✔（原位翻转）
        assert "\u2714" in model.committed_lines[offset].plain, (
            "关闭后 committed_lines 顶边框图标应更新为 ✔"
        )
        # 标题文本保留（图标替换不丢失标题内容；web_search 显示完整名 WebSearch）
        assert "WebSearch" in model.committed_lines[offset].plain, (
            "顶边框图标更新后应保留标题文本"
        )

    def test_tool_title_icon_fail_updates_after_close_regression(self):
        """fail 场景：长工具输出关闭后标题行图标更新为 ✖。"""
        from src.tui.app.model import AppModel, _TOOL_INCREMENTAL_THRESHOLD

        model = self._open_long_tool("t2", "web_search")
        block = model.blocks[-1]
        offset = block.extra.get("_first_committed_offset")
        assert offset is not None
        model.close_tool_box("t2", False)
        assert model.committed_lines[offset].plain.startswith("\u250c"), (
            "offset 应指向工具卡片顶边框"
        )
        assert "\u2716" in model.committed_lines[offset].plain, (
            "关闭后 committed_lines 顶边框图标应更新为 ✖"
        )

    def test_short_tool_title_icon_no_offset_unchanged(self):
        """短工具输出（未增量提交）关闭后 committed_lines 标题行直接带 ✔（行为不变）。"""
        from src.tui.app.model import AppModel

        model = AppModel()
        model.open_tool_box("t3", "read_file")
        model.append_tool_output("t3", "brief")
        model.close_tool_box("t3", True)
        # 关闭后顶边框（committed_lines[0]）直接带 ✔——未触发增量提交时
        # close 经 commit_block 提交的顶边框已带 done 图标，无需 offset 更新路径。
        assert model.committed_lines, "关闭后 committed_lines 不应为空"
        assert model.committed_lines[0].plain.startswith("\u250c"), (
            "短工具关闭后 committed_lines 首行应为顶边框"
        )
        assert "\u2714" in model.committed_lines[0].plain, (
            "短工具关闭后顶边框状态图标应为 ✔"
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
        """构造非顶部 StaticLines（上方 header 占 y=0 → committed 在 y>=1）。"""
        from src.tui.ink.element import h, BOX, TEXT
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import StaticLines
        r = Reconciler()
        root = r.create_root()
        el = h(BOX, None, [
            h(TEXT, {"children": header_text}),
            h(StaticLines, {"lines": lines}),
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


class TestOpenBlockCommitOrder:
    """BUG-4 — 开放块增量提交不得打乱 committed_lines 块顺序。

    流式期间 content 块（索引在 reasoning 之后）若被 commit_open_block 提前
    写入 committed_lines，reasoning 关闭提交时被插到 content 之后——形成
    content 前半 + reasoning + content 后半的内容交错。
    """

    def test_content_not_committed_before_preceding_reasoning(self):
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui._const import (
            UserMsgCmd, ContentCmd, PhaseDoneCmd, ReasoningCmd,
        )

        model = AppModel()
        apply_cmd(model, UserMsgCmd(text="用户问题"))
        # 两个开放块：reasoning（前） + content（后）——content 流式写入时
        # reasoning 尚未关闭，content 不得被增量提交到 committed_lines。
        apply_cmd(model, ReasoningCmd(text="思考前半"))
        apply_cmd(model, ReasoningCmd(text="思考后半"))
        apply_cmd(model, ContentCmd(text="回答正文前半"))
        apply_cmd(model, ContentCmd(text="回答正文后半"))
        # 此时 committed_lines 只有 user（reasoning/content 均未关闭）
        assert model.committed_count == 1, (
            f"开放窗口期间 committed_count 应为 1，实际 {model.committed_count}"
        )
        # 关闭 reasoning → 提交 user + reasoning
        apply_cmd(model, PhaseDoneCmd(phase="reasoning"))
        assert model.committed_count == 2, (
            f"关闭 reasoning 后 committed_count 应为 2，实际 {model.committed_count}"
        )
        plains = [l.plain for l in model.committed_lines]
        assert any("思考前半" in p for p in plains), (
            f"reasoning 内容应已提交: {plains}"
        )
        assert not any("回答正文" in p for p in plains), (
            f"content 不应在 reasoning 之前提交（块顺序保持）: {plains}"
        )
        # 关闭 content → 提交全部，顺序 = user, reasoning, content
        apply_cmd(model, PhaseDoneCmd(phase="content"))
        assert model.committed_count == 3
        plains = [l.plain for l in model.committed_lines]
        idx_user = next(i for i, p in enumerate(plains) if "用户问题" in p)
        idx_reason = next(i for i, p in enumerate(plains) if "思考前半" in p)
        idx_content = next(i for i, p in enumerate(plains) if "回答正文前半" in p)
        assert idx_user < idx_reason < idx_content, (
            f"committed_lines 顺序应为 user < reasoning < content，实际 {plains}"
        )

    def test_commit_open_block_identity_not_value_eq(self):
        """BUG-11 — commit_open_block 定位块索引须用身份比较而非值比较。

        ChatBlock 为 dataclass，默认 ``__eq__`` 是**值比较**——两个开放块若
        字段相同（kind/lines/extra/closed/committed_line_count/缓存均相同）
        会互相相等（lines 为共享同一列表引用时成立——AnsiLine 自身身份比较，
        独立创建的两个行不相等，故需共享引用构造相等场景）。旧实现
        ``self.blocks.index(block)`` 对第二个块返回第一个的位置（0），与
        ``committed_count==0`` 相等 → 错误允许第二个块增量提交（违反 BUG-4
        连续窗口不变式：b1 在前面未关闭，b2 行被提前写入 committed_lines）。
        修复后按 ``b is block`` 身份查找，idx=1 != 0 → 正确阻止。
        """
        from src.tui.app.model import AppModel
        from src.renderer.ansi.helpers import AnsiLine

        model = AppModel()
        shared_lines = [AnsiLine.of("行1")]
        b1 = model.append_block("content", shared_lines)
        b2 = model.append_block("content", shared_lines)  # 与 b1 所有字段完全相同
        # 前置断言：dataclass 值相等（bug 复现前提）
        assert b1 == b2
        # 两个开放块：committed_count=0，b2 索引=1 不在连续提交窗口内
        model.commit_open_block(b2)
        assert model.committed_lines == [], (
            f"b2 不在连续提交窗口内，不应增量提交，实际 {model.committed_lines}"
        )
        assert b2.committed_line_count == 0
        # b1 在窗口内（idx=0 == committed_count=0）→ 允许增量提交
        model.commit_open_block(b1)
        assert len(model.committed_lines) == 1, (
            f"b1 在连续窗口内应增量提交，实际 {len(model.committed_lines)}"
        )
        assert b1.committed_line_count == 1


class TestToolCloseFrozenTailOnly:
    """BUG-21 — 关闭块冻结仅未提交尾（增量提交后不重复存储已提交行）。"""

    def test_close_frozen_tail_only_after_incremental_commit(self):
        """工具输出触发增量提交（>64 行）→ 关闭冻结长度 = 未提交尾 + 底边框。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui._const import ToolOpenCmd, ToolOutputCmd, ToolCloseCmd

        model = AppModel()
        model.width = 80
        apply_cmd(model, ToolOpenCmd(tool_id="t1", tool_name="my_tool", detail=""))
        for i in range(70):
            apply_cmd(model, ToolOutputCmd(tool_id="t1", text=f"output line {i}"))
        blk = model.tool_boxes["t1"]
        # 触发增量提交（>64 行）
        assert blk.committed_line_count >= 64
        committed_before_close = blk.committed_line_count
        total = len(blk.lines)
        apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
        # 冻结长度 = 未提交尾（+1 底边框）；修复前全量冻结 → 长度 ≈ total
        frozen_len = len(blk._cached_ink_lines or [])
        tail = total - committed_before_close + 1  # 未提交尾 + 底边框
        assert frozen_len <= tail + 1, (
            f"关闭后冻结应为未提交尾（非全量）: frozen={frozen_len} "
            f"total={total} committed_before_close={committed_before_close} tail≈{tail}"
        )
        assert frozen_len < total // 2, (
            f"增量提交后冻结应远小于全量（内存优化）: frozen={frozen_len} total={total}"
        )


class TestToolBoxReuseTitleSync:
    """BUG-22 — open_tool_box 复用已增量提交 box 时同步 committed 顶边框标题。"""

    def test_reuse_updates_committed_header(self):
        """同一 tool_id 复用且已增量提交 → committed_lines 顶边框标题更新。"""
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd
        from src.tui._const import ToolOpenCmd, ToolOutputCmd

        model = AppModel()
        model.width = 80
        # 先以空名打开（兜底 box），输出触发增量提交
        apply_cmd(model, ToolOpenCmd(tool_id="t1", tool_name="", detail=""))
        for i in range(70):
            apply_cmd(model, ToolOutputCmd(tool_id="t1", text=f"output line {i}"))
        blk = model.tool_boxes["t1"]
        offset = blk.extra.get("_first_committed_offset")
        assert offset is not None and offset < len(model.committed_lines)
        old_plain = model.committed_lines[offset].plain
        # 复用更新标题（后到 ToolStartedEvent 补全工具名）
        apply_cmd(model, ToolOpenCmd(tool_id="t1", tool_name="bash", detail="ls -la"))
        new_plain = model.committed_lines[offset].plain
        assert new_plain != old_plain or "Bash" in new_plain or "bash" in new_plain, (
            f"复用后 committed 顶边框应更新标题: {old_plain!r} → {new_plain!r}"
        )
        assert ("Bash" in new_plain or "bash" in new_plain), (
            f"新标题应含工具名 bash: {new_plain!r}"
        )


# ═══════════════════════════════════════════════════════════
# 2026-08-05 — 空状态欢迎提示
# ═══════════════════════════════════════════════════════════

class TestEmptyStateWelcome:
    """空状态（启动/清屏后）显示欢迎引导行。"""

    def test_empty_model_renders_welcome(self) -> None:
        """空模型渲染欢迎提示（✦ 欢迎使用 DeepSeek CLI）。"""
        from src.tui.app.app import build_app_element
        model = AppModel()
        model.status.model_name = "deepseek-chat"
        session = InkSession(model=model, build_tree=build_app_element, stream=io.StringIO())
        session._width_cache._width = 80
        session._width_cache._height = 40
        session._render_frame()
        plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", session._ink_renderer._stream.getvalue())
        assert "欢迎使用 DeepSeek CLI" in plain, f"空状态应显示欢迎提示: {plain!r}"

    def test_non_empty_model_no_welcome(self) -> None:
        """有消息时不显示欢迎提示。"""
        from src.tui.app.app import build_app_element
        from src.tui.app.apply import build_user_line
        model = AppModel()
        model.append_committed("user", build_user_line("你好"))
        session = InkSession(model=model, build_tree=build_app_element, stream=io.StringIO())
        session._width_cache._width = 80
        session._width_cache._height = 40
        session._render_frame()
        plain = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", session._ink_renderer._stream.getvalue())
        assert "欢迎使用 DeepSeek CLI" not in plain, f"有消息时不应显示欢迎提示: {plain!r}"
