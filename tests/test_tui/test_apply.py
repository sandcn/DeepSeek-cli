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
        assert boxes["t1"].extra["tool_output_count"] == 2
        assert boxes["t2"].extra["tool_output_count"] == 2
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
    """方向D 步骤15 — 工具调用卡片状态/折叠/截断标记。"""

    def test_tool_open_sets_running_state(self):
        """open 后 extra 记录 running + 默认展开。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        block = m.blocks[-1]
        assert block.extra["tool_status"] == "running"
        assert block.extra["tool_expanded"] is True
        assert block.extra["tool_output_count"] == 0

    def test_tool_close_sets_status_and_expanded(self):
        """close 后状态 done + 输出少不折叠 + 冻结缓存建立。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="data", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        block = m.blocks[-1]
        assert block.extra["tool_status"] == "done"
        assert block.extra["tool_expanded"] is True  # 1 行 < 阈值 8
        assert block._cached_ink_lines is not None
        assert len(block._cached_ink_lines) == len(block.lines)

    def test_tool_close_fail_status(self):
        """close 失败 → status=fail。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="x", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=False))
        assert m.blocks[-1].extra["tool_status"] == "fail"

    def test_tool_output_count_accumulates(self):
        """append_tool_output 维护输出行计数（空段不计）。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="a\n", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="b\nc\n", tool_id="t1"))
        assert m.blocks[-1].extra["tool_output_count"] == 3

    def test_tool_close_auto_collapse(self):
        """输出行数 > 折叠阈值 → tool_expanded=False，块行保留完整输出行。"""
        from src.tui._config import TuiConfig
        m = AppModel(config=TuiConfig.defaults().with_overrides(
            tool_auto_collapse_threshold=3))
        m.open_tool_box("t1", "read_file")
        for i in range(5):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra["tool_expanded"] is False
        assert block.extra["tool_status"] == "done"
        # Bug B 修复：折叠块保留完整输出行（不重写 block.lines）
        assert len(block.lines) == 7  # 标题 + 5 输出 + 状态
        # 折叠提示在可见形式中（标题 + 前 2 行 + 提示），不在 block.lines
        assert "已折叠（5 行输出）" not in block.lines[1].plain
        assert "line0" in block.lines[1].plain
        # 原始标题行保持（渲染图标是装饰，不改动模型原文）
        assert block.lines[0].plain.startswith("  · ")

    def test_tool_output_truncated(self):
        """超长输出截断：Claude Code 风格——保留开头 + 省略 + 状态（无 tail）。"""
        from src.tui._config import TuiConfig
        m = AppModel(config=TuiConfig.defaults().with_overrides(
            tool_output_max_lines=4,
            tool_auto_collapse_threshold=1000,  # 关闭折叠，仅观察截断
        ))
        m.open_tool_box("t1", "bash")
        for i in range(1, 11):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra["tool_output_count"] == 10
        assert block.extra["tool_output_truncated"] is True
        assert block.extra["tool_expanded"] is True  # 10 ≤ 1000 不折叠
        # 标题 + head(4) + 省略 + 状态 = 7 行（无 tail）
        assert len(block.lines) == 7
        assert "line1" in block.lines[1].plain
        assert "line2" in block.lines[2].plain
        assert "line3" in block.lines[3].plain
        assert "line4" in block.lines[4].plain
        assert "已截断（10 行输出）" in block.lines[5].plain
        # 无 tail：尾部输出不保留（line10 已截断）
        assert not any("line10" in l.plain for l in block.lines)
        assert block.lines[-1].plain.strip() == "✔"

    def test_tool_collapse_preserves_output_lines(self):
        """输出 10 行（>8）→ 折叠但 block.lines 保留完整行（长度不减）。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        for i in range(10):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra["tool_expanded"] is False
        # 标题 + 10 输出 + 状态 = 12 行（完整保留）
        assert len(block.lines) == 12

    def test_tool_collapsed_visible_form_head_preview(self):
        """折叠块可见形式 = 标题 + 前 2 行 + 提示（Bug B 修复目标）。"""
        from src.tui.app.model import _visible_tool_ansi_lines
        from src.tui._config import TuiConfig
        m = _model()
        m.open_tool_box("t1", "read_file")
        for i in range(10):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        visible = _visible_tool_ansi_lines(block, TuiConfig.defaults())
        plains = [l.plain for l in visible]
        assert len(plains) == 4  # 标题 + 前 2 行 + 提示
        assert plains[0].startswith("  · ")
        assert "line0" in plains[1]
        assert "line1" in plains[2]
        assert "已折叠（10 行输出）· Space 展开" in plains[3]

    def test_tool_truncation_head_style_no_tail(self):
        """输出 60 行（>50）→ 截断块 = 标题 + head(50) + 省略 + 状态（无 tail）。"""
        from src.tui._config import TuiConfig
        m = AppModel(config=TuiConfig.defaults().with_overrides(
            tool_auto_collapse_threshold=1000,  # 关闭折叠，仅观察截断
        ))
        m.open_tool_box("t1", "bash")
        for i in range(1, 61):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra["tool_output_truncated"] is True
        # 标题 + head(50) + 省略 + 状态 = 53 行
        assert len(block.lines) == 53
        assert "line1" in block.lines[1].plain
        assert "line50" in block.lines[50].plain
        assert "已截断（60 行输出）" in block.lines[51].plain
        assert block.lines[-1].plain.strip() == "✔"
        # 无 tail：line60 不保留
        assert not any("line60" in l.plain for l in block.lines)

    def test_tool_collapse_preview_lines_respected(self):
        """with_overrides(tool_collapse_preview_lines=3) → 折叠可见形式前 3 行。"""
        from src.tui.app.model import _visible_tool_ansi_lines
        from src.tui._config import TuiConfig
        cfg = TuiConfig.defaults().with_overrides(tool_collapse_preview_lines=3)
        m = AppModel(config=cfg)
        m.open_tool_box("t1", "read_file")
        for i in range(10):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra["tool_expanded"] is False
        visible = _visible_tool_ansi_lines(block, cfg)
        plains = [l.plain for l in visible]
        assert len(plains) == 5  # 标题 + 前 3 行 + 提示
        assert "line0" in plains[1]
        assert "line1" in plains[2]
        assert "line2" in plains[3]
        assert "已折叠（10 行输出）· Space 展开" in plains[4]

    def test_tool_no_truncation_within_limit(self):
        """输出 ≤ max_lines 不截断（全部保留）。"""
        from src.tui._config import TuiConfig
        m = AppModel(config=TuiConfig.defaults().with_overrides(
            tool_output_max_lines=4,
            tool_auto_collapse_threshold=1000,
        ))
        m.open_tool_box("t1", "bash")
        for i in range(1, 4):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert block.extra.get("tool_output_truncated") is None
        assert len(block.lines) == 5  # 标题 + 3 输出 + 状态


class TestToolToggle:
    """方向④ — 交互式折叠/展开（toggle_tool_box）。"""

    @staticmethod
    def _make_collapsed_box(m=None, n=10):
        m = m if m is not None else _model()
        m.open_tool_box("t1", "read_file")
        for i in range(n):
            m.append_tool_output("t1", f"line{i}\n")
        m.close_tool_box("t1", True)
        return m

    def test_toggle_tool_box_expands_collapsed(self):
        """构造折叠块 → toggle → tool_expanded=True 且缓存失效重建为完整形式。"""
        from src.tui.app.model import _visible_tool_ansi_lines
        from src.tui._config import TuiConfig
        m = self._make_collapsed_box()
        block = m.blocks[-1]
        assert block.extra["tool_expanded"] is False
        assert block._cached_ink_lines is not None
        result = m.toggle_tool_box("t1")
        assert result is True
        assert block.extra["tool_expanded"] is True
        # 冻结缓存失效（下次渲染按新状态重建）
        assert block._cached_ink_lines is None
        # committed_lines 已全量重建（含展开可见形式行）
        assert m.committed_lines
        # 可见形式恢复完整行
        visible = _visible_tool_ansi_lines(block, TuiConfig.defaults())
        assert len(visible) == len(block.lines)  # 完整 12 行

    def test_toggle_tool_box_collapses_expanded(self):
        """展开块（超阈值）→ toggle → tool_expanded=False 且可见形式为折叠形式。"""
        from src.tui.app.model import _visible_tool_ansi_lines
        from src.tui._config import TuiConfig
        m = self._make_collapsed_box()
        block = m.blocks[-1]
        # 先展开
        m.toggle_tool_box("t1")
        assert block.extra["tool_expanded"] is True
        # 再折叠
        result = m.toggle_tool_box("t1")
        assert result is False
        assert block.extra["tool_expanded"] is False
        visible = _visible_tool_ansi_lines(block, TuiConfig.defaults())
        assert len(visible) == 4  # 标题 + 前 2 行 + 提示

    def test_recent_collapsed_tool_id_returns_latest(self):
        """多个折叠块 → 返回最近关闭者。"""
        m = _model()
        for i in range(3):
            m.open_tool_box(f"t{i}", "read_file")
            for j in range(10):
                m.append_tool_output(f"t{i}", f"line{j}\n")
            m.close_tool_box(f"t{i}", True)
        assert m._recent_collapsed_tool_id() == "t2"
        # 展开最近者后返回上一个
        m.toggle_tool_box("t2")
        assert m._recent_collapsed_tool_id() == "t1"

    def test_toggle_unknown_tool_id_noop(self):
        """未知 tool_id toggle → 返回 None、不抛异常。"""
        m = _model()
        assert m.toggle_tool_box("t-unknown") is None

    def test_toggle_no_collapsed_returns_none(self):
        """无折叠块时 _recent_collapsed_tool_id 返回 None（按键处理器 no-op）。"""
        m = _model()
        assert m._recent_collapsed_tool_id() is None


class TestRebuildCommittedOpenBlock:
    """方向1 L1 — _rebuild_committed 重建保留开放块已提交行。

    覆盖：开放 content 块已提交段落行在 toggle 折叠工具块（触发
    _rebuild_committed）后仍存在于 committed_lines（修复前清空重建只提交
    已关闭块，开放块已提交行从缓存丢失）。
    """

    def test_rebuild_keeps_open_block_committed_lines(self):
        """开放 content 块已提交行在 toggle 折叠工具块后仍存在。"""
        m = _model()
        # 已关闭块：工具块
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="out", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        # 开放块：content 块（流式中，段落闭合已提交）
        apply_cmd(m, ContentCmd(text="para one\n\n"))
        apply_cmd(m, ContentCmd(text="para two\n\n"))
        block = m.blocks[-1]
        assert block.kind == "content"
        assert block.closed is False
        assert block.committed_line_count > 0
        assert len(m.committed_lines) > 0
        # toggle 折叠工具块 → _rebuild_committed
        m.toggle_tool_box("t1")
        # 开放块已提交行仍存在（不丢）
        assert block.committed_line_count > 0
        plains = [l.plain for l in m.committed_lines]
        assert any("para one" in p for p in plains)
        assert any("para two" in p for p in plains)

    def test_rebuild_without_open_block_unchanged(self):
        """无开放块已提交行时 _rebuild_committed 行为不变（纯关闭块场景）。"""
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="read_file", tool_id="t1"))
        apply_cmd(m, ToolOutputCmd(text="out", tool_id="t1"))
        apply_cmd(m, ToolCloseCmd(tool_id="t1", success=True))
        committed_before = len(m.committed_lines)
        assert committed_before > 0
        m.toggle_tool_box("t1")
        assert len(m.committed_lines) > 0  # 已关闭块仍提交

    def test_open_block_zero_committed_skipped(self):
        """开放块无已提交行（committed_line_count == 0）→ 跳过保留，不报错。"""
        m = _model()
        # 仅一个开放 content 块（无段落闭合 → committed_line_count == 0）
        m.ensure_content()
        block = m.blocks[-1]
        assert block.committed_line_count == 0
        m._rebuild_committed()  # 不抛异常
        assert block.committed_line_count == 0
