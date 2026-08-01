"""测试 src/tui/app/apply.py — RenderCmd → AppModel 迁移。

覆盖：每类 RenderCmd 的模型变更、阶段状态机（推理 INACTIVE/ACTIVE/CLOSED）、
content close/reopen、tool 组开闭、状态计数。
"""

from __future__ import annotations

from src.tui.app.model import AppModel, ReasoningState
from src.tui.app.apply import apply_cmd
from src.tui._const import (
    RenderCommand,
    ReasoningCmd,
    ContentCmd,
    PhaseDoneCmd,
    ToolOutputCmd,
    ToolSummaryCmd,
    ToolOpenCmd,
    ToolCloseCmd,
    UserMsgCmd,
    ParseInfoCmd,
    NotificationCmd,
    WriteLineCmd,
    ErrorCmd,
    ToolCountIncCmd,
    ToolCountDecCmd,
    ToolFailIncCmd,
    MainPhaseCmd,
    SubagentFrameCmd,
    SplashCmd,
    DisplayMsgsCmd,
    _CLEAR_PARSE_LINE,
)


def _model() -> AppModel:
    return AppModel()


class TestBasicCommands:
    def test_notification(self):
        m = _model()
        apply_cmd(m, NotificationCmd(text="hi"))
        assert m.blocks[-1].kind == "notification"
        assert m.blocks[-1].lines[0].plain == "  \u2502 hi"

    def test_write_line(self):
        m = _model()
        apply_cmd(m, WriteLineCmd(text="raw line"))
        assert m.blocks[-1].kind == "write_line"
        assert m.blocks[-1].lines[0].plain == "raw line"

    def test_error(self):
        m = _model()
        apply_cmd(m, ErrorCmd(message="boom"))
        assert m.blocks[-1].kind == "error"
        assert "boom" in m.blocks[-1].lines[0].plain

    def test_error_empty_skipped(self):
        m = _model()
        apply_cmd(m, ErrorCmd(message=""))
        assert m.blocks == []

    def test_splash(self):
        m = _model()
        apply_cmd(m, SplashCmd())
        assert m.blocks[-1].kind == "splash"
        assert "DeepSeek" in m.blocks[-1].lines[0].plain

    def test_user_message(self):
        m = _model()
        apply_cmd(m, UserMsgCmd(text="hello"))
        block = m.blocks[-1]
        assert block.kind == "user"
        assert block.lines[0].plain == "  > hello"

    def test_subagent_frame(self):
        m = _model()
        apply_cmd(m, SubagentFrameCmd(frame_lines=("line1", "line2")))
        assert m.subagent_lines == ["line1", "line2"]

    def test_parse_info(self):
        m = _model()
        apply_cmd(m, ParseInfoCmd(tool_names="rf", tokens=100, elapsed=0.5))
        # 同位置刷新：更新实时行而非追加块
        assert m.parse_line is not None
        assert "rf 100t 0.50s" in m.parse_line.plain
        assert not any(b.kind == "parse_info" for b in m.blocks)

    def test_parse_info_refreshes_in_place(self):
        """多次 ParseInfo 更新同一实时行（不追加新行）。"""
        m = _model()
        apply_cmd(m, ParseInfoCmd(tool_names="ls", tokens=0, elapsed=0.0))
        assert "ls 0t 0.00s" in m.parse_line.plain
        apply_cmd(m, ParseInfoCmd(tool_names="ls", tokens=3, elapsed=0.12))
        assert "ls 3t 0.12s" in m.parse_line.plain
        assert not any(b.kind == "parse_info" for b in m.blocks)

    def test_parse_info_done_commits_single_line(self):
        """ParseInfoDone 提交最终进度行为一个块并清空实时行。"""
        m = _model()
        apply_cmd(m, ParseInfoCmd(tool_names="ls", tokens=3, elapsed=0.12))
        apply_cmd(m, ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE, elapsed=0.0))
        assert m.parse_line is None
        assert m.blocks[-1].kind == "parse_info"
        assert "ls 3t 0.12s" in m.blocks[-1].lines[0].plain


class TestReasoningStateMachine:
    """推理通道状态机。"""

    def test_inactive_to_active_on_first_chunk(self):
        m = _model()
        apply_cmd(m, ReasoningCmd(text="think"))
        assert m.reasoning_state == ReasoningState.ACTIVE
        assert len(m.blocks) == 1
        assert m.blocks[0].kind == "reasoning"

    def test_chunks_accumulate_in_block(self):
        m = _model()
        # 空行闭合段落 → 段落 token 产出 → 行固化到块
        apply_cmd(m, ReasoningCmd(text="think one\n\n"))
        apply_cmd(m, ReasoningCmd(text="think two\n\n"))
        assert m.blocks[0].lines  # 有已渲染行
        assert any("think one" in l.plain for l in m.blocks[0].lines)

    def test_close_reasoning_finalizes(self):
        m = _model()
        apply_cmd(m, ReasoningCmd(text="think"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        assert m.reasoning_state == ReasoningState.CLOSED
        assert m.reasoning_renderer is None
        # 分隔线已追加
        assert m.blocks[0].lines[-1].plain.startswith("  ─")

    def test_reasoning_discarded_after_close(self):
        m = _model()
        apply_cmd(m, ReasoningCmd(text="think"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        n_blocks = len(m.blocks)
        n_lines = len(m.blocks[0].lines)
        apply_cmd(m, ReasoningCmd(text="late"))
        assert len(m.blocks) == n_blocks  # 不新建块
        assert len(m.blocks[0].lines) == n_lines  # 不追加行

    def test_reopen_reasoning(self):
        m = _model()
        apply_cmd(m, ReasoningCmd(text="t1"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        apply_cmd(m, MainPhaseCmd(phase="thinking"))
        assert m.reasoning_state == ReasoningState.INACTIVE
        apply_cmd(m, ReasoningCmd(text="t2"))
        assert m.reasoning_state == ReasoningState.ACTIVE
        assert len(m.blocks) == 2

    def test_phase_done_unknown_phase(self):
        m = _model()
        apply_cmd(m, PhaseDoneCmd(phase="unknown"))
        assert m.reasoning_state == ReasoningState.INACTIVE


class TestContentChannel:
    """content 通道开闭。"""

    def test_content_accumulates(self):
        m = _model()
        apply_cmd(m, ContentCmd(text="# Hi\n"))
        apply_cmd(m, ContentCmd(text="\nbody\n"))
        assert m.blocks[0].kind == "content"
        assert any("Hi" in l.plain for l in m.blocks[0].lines)

    def test_close_content(self):
        m = _model()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.content_closed is True
        assert m.content_renderer is None

    def test_content_discarded_after_close(self):
        m = _model()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        n_blocks = len(m.blocks)
        apply_cmd(m, ContentCmd(text="late"))
        assert len(m.blocks) == n_blocks

    def test_reopen_content(self):
        m = _model()
        apply_cmd(m, ContentCmd(text="x"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        apply_cmd(m, MainPhaseCmd(phase="answering"))
        assert m.content_closed is False
        apply_cmd(m, ContentCmd(text="new"))
        assert len(m.blocks) == 2


class TestToolBox:
    """每工具一个分组（无边框：标题圆点 + 缩进输出 + 状态行，增量刷新）。"""

    def test_tool_open_creates_box(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        block = m.blocks[-1]
        assert block.kind == "tool"
        assert block.lines[0].plain.startswith("  · ")
        # 无边框字符
        assert not any(ch in block.lines[0].plain for ch in "┌─┐│╰╯")
        # 显示名（get_tool_display_name 缩写）
        from src.tools.registry import get_tool_display_name
        assert get_tool_display_name("read_file") in block.lines[0].plain
        assert block.closed is False  # 开放分组

    def test_tool_output_appends_to_box(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="line1", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="line2", tool_id="t1"))
        block = m.blocks[-1]
        assert block.kind == "tool"
        assert "line1" in block.lines[1].plain
        assert "line2" in block.lines[2].plain

    def test_tool_output_unknown_id_auto_opens_box(self):
        """无 ToolOpen 但 tool_id 非空的输出自动创建分组（兼容）。"""
        m = _model()
        apply_cmd(m, ToolOutputCmd(text="running X", tool_id="t1"))
        block = m.blocks[-1]
        assert block.kind == "tool"
        assert block.lines[0].plain.startswith("  · ")

    def test_append_tool_output_routes_by_tool_id(self):
        """两个 tool_id 分别追加输出 → 各自 block 行数正确、互不污染。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        m.open_tool_box("t2", "bash")
        m.append_tool_output("t1", "a1\n")
        m.append_tool_output("t2", "b1\n")
        m.append_tool_output("t1", "a2\n")
        m.append_tool_output("t2", "b2\n")
        boxes = {b.extra.get("tool_id"): b for b in m.blocks if b.kind == "tool"}
        t1_plains = [l.plain for l in boxes["t1"].lines]
        t2_plains = [l.plain for l in boxes["t2"].lines]
        assert any("a1" in p for p in t1_plains)
        assert any("a2" in p for p in t1_plains)
        assert any("b1" in p for p in t2_plains)
        assert any("b2" in p for p in t2_plains)
        # 互不污染：t1 不含 b 行、t2 不含 a 行
        assert not any("b1" in p or "b2" in p for p in t1_plains)
        assert not any("a1" in p or "a2" in p for p in t2_plains)

    def test_append_tool_output_unknown_id_creates_anonymous_box(self):
        """未知 tool_id 追加 → 创建匿名 box 且输出不丢。"""
        m = _model()
        m.append_tool_output("t-unknown", "data\n")
        block = m.blocks[-1]
        assert block.kind == "tool"
        assert block.extra.get("tool_id") == "t-unknown"
        assert "data" in block.lines[1].plain

    def test_append_tool_output_empty_id_discarded(self):
        """空 tool_id 追加 → 不创建块、不抛异常。"""
        m = _model()
        m.append_tool_output("", "orphan\n")
        assert m.blocks == []
        assert m.tool_boxes == {}

    def test_close_tool_box_unknown_id_noop(self):
        """未知 tool_id close → 不抛异常、不影响其他块。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        m.close_tool_box("t-unknown", True)  # 不抛
        assert len(m.blocks) == 1
        assert m.tool_boxes.get("t1") is not None
        m.close_tool_box("t1", True)
        assert m.blocks[-1].closed is True

    def test_tool_close_commits_box(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="data", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        block = m.blocks[-1]
        assert block.closed is True
        assert block.lines[-1].plain.strip() == "✔"
        # 无边框字符
        assert not any(ch in block.lines[-1].plain for ch in "┌─┐│╰╯")

    def test_tool_close_fail(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="x", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=False))
        assert "✖" in m.blocks[-1].lines[-1].plain

    def test_tool_summary_closes_open_box(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="x", tool_id="t1"))
        apply_cmd(m, ToolSummaryCmd(successful=("x",), failed=()))
        assert m.blocks[-1].closed is True


class TestStatusCounts:
    """状态计数。"""

    def test_tool_count_inc_dec(self):
        m = _model()
        apply_cmd(m, ToolCountIncCmd())
        apply_cmd(m, ToolCountIncCmd())
        assert m.status.tool_count == 2
        assert m.status.tool_total == 2
        apply_cmd(m, ToolCountDecCmd())
        assert m.status.tool_count == 1

    def test_tool_count_never_negative(self):
        m = _model()
        apply_cmd(m, ToolCountDecCmd())
        assert m.status.tool_count == 0

    def test_tool_fail_inc(self):
        m = _model()
        apply_cmd(m, ToolFailIncCmd())
        assert m.status.tool_fail == 1

    def test_main_phase(self):
        m = _model()
        apply_cmd(m, MainPhaseCmd(phase="thinking"))
        assert m.status.main_phase == "thinking"


class TestBatchOrder:
    """同批命令块顺序（保留 rendered_cids 排序不变式）。"""

    def test_order_reasoning_content_phase(self):
        m = _model()
        apply_cmd(m, ReasoningCmd(text="r1\n"))
        apply_cmd(m, PhaseDoneCmd(phase="reasoning"))
        apply_cmd(m, ContentCmd(text="c1\n"))
        apply_cmd(m, ContentCmd(text="c2\n"))
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        kinds = [b.kind for b in m.blocks]
        assert kinds == ["reasoning", "content"]


class TestDisplayMsgs:
    def test_display_messages(self):
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        assert m.blocks[-1].kind == "write_line"  # 分隔线
        assert m.blocks[-2].kind == "user"


class TestToolCardState:
    """方向D 步骤15 — 工具调用卡片状态标记。"""

    def test_tool_open_sets_running_state(self):
        """open 后 extra 记录 running。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        block = m.blocks[-1]
        assert block.extra["tool_status"] == "running"

    def test_tool_close_sets_status_and_freeze(self):
        """close 后状态 done + 冻结缓存建立。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="data", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        block = m.blocks[-1]
        assert block.extra["tool_status"] == "done"
        assert block._cached_ink_lines is not None
        assert len(block._cached_ink_lines) == len(block.lines)

    def test_tool_close_fail_status(self):
        """close 失败 → status=fail。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="x", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=False))
        assert m.blocks[-1].extra["tool_status"] == "fail"
