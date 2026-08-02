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
    SubagentMarkdownCmd,
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

    def test_append_tool_output_parses_ansi_into_runs(self):
        """工具输出含 ANSI 高亮序列（read_file 经 Rich 上屏）→ 解析为带样式
        Run，宽度测量不含转义码，wrap 不会截断转义序列（防 ;49;00m 残留）。"""
        m = _model()
        # Rich 风格 TrueColor 高亮：\x1b[38;2;R;G;B;49m...\x1b[0m
        raw = "\x1b[38;2;102;217;239;49mdef\x1b[0m\x1b[38;2;248;248;242;49m f()\x1b[0m"
        m.open_tool_box("t1", "read_file")
        m.append_tool_output("t1", raw + "\n")
        block = m.tool_boxes["t1"]
        line = block.lines[1]
        # 转义序列已解析为样式：run.text 不含 \x1b 字符
        assert "\x1b" not in line.plain
        assert "def f()" in line.plain
        # 宽度按可见字符测量（不含转义码），超宽 wrap 不会产出残缺转义片段
        assert line.width == len("def f()") + 2  # 前缀 "  "
        styled = [r for r in line.runs if r.style is not None and r.style.fg == (102, 217, 239)]
        assert styled and styled[0].text == "def"

    def test_append_tool_output_ansi_wrap_keeps_sequences_intact(self):
        """ANSI 工具输出超宽 wrap 后，渲染结果不含裸露的转义残留片段。"""
        import re as _re
        m = _model()
        m.width = 8
        raw = (
            "\x1b[38;2;102;217;239;49mdef\x1b[0m\x1b[38;2;248;248;242;49m f()\x1b[0m"
            + "A" * 40
        )
        m.open_tool_box("t1", "read_file")
        m.append_tool_output("t1", raw + "\n")
        m.close_tool_box("t1", True)
        # 经 _block_to_ink_lines 提交到 committed_lines → 行级 wrap 后渲染
        rendered = "\n".join(l.render() for l in m.committed_lines)
        stripped = _re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", rendered)
        for frag in (";49", ";00m", ";49m", "8;248"):
            assert frag not in stripped, f"wrap 残留残缺转义片段 {frag!r}: {stripped!r}"

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

    def test_tool_output_incremental_commit_threshold(self):
        """工具输出 >64 行 → committed_line_count 推进、committed_lines 含已提交行。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        # 标题(1) + 65 行输出 = 66 行；超过阈值触发增量提交
        for i in range(65):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count >= 64, (
            f"增量提交应推进 committed_line_count，实际 {block.committed_line_count}"
        )
        assert len(m.committed_lines) >= 64  # committed_lines 含已提交行
        remaining = len(block.lines) - block.committed_line_count
        assert remaining < 64  # 块内仅留未提交尾

    def test_tool_output_incremental_close_no_duplicate(self):
        """增量提交后关闭 → 关闭后无重复行（关键不变量：committed_lines 与块不重叠）。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        for i in range(70):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count > 0  # 增量提交已发生
        m.close_tool_box("t1", True)
        # 关闭后全部行已提交（committed_line_count == len）
        assert block.committed_line_count == len(block.lines)
        # 无重复行：卡片结构下 committed_lines = 块行 + 角色头 + 卡片尾空行
        # （每 AnsiLine → 1 ink Line；头/空行各 1 行额外）
        assert len(m.committed_lines) == len(block.lines) + 2, (
            f"关闭后 committed_lines 应 = 块行 + 头 + 空行，committed={len(m.committed_lines)} lines={len(block.lines)}"
        )
        committed_plains = [l.plain for l in m.committed_lines]
        # 尾行为卡片空行；✔ 在正文标题行（去空行后）
        assert committed_plains[-1] == ""
        assert "✔" in "".join(committed_plains)
        assert any("line0" in p for p in committed_plains)
        assert any("line69" in p for p in committed_plains)
        # 内容顺序：line0 在前、line69 在后
        assert committed_plains.index(next(p for p in committed_plains if "line0" in p)) < \
               committed_plains.index(next(p for p in committed_plains if "line69" in p))

    def test_commit_open_block_header_once_trailer_once(self):
        """卡片结构：commit_open_block 多次增量提交角色头恰好一次；关闭后尾空行恰好一次。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        # 输出行数远超阈值 → 多次 commit_open_block 增量提交
        for i in range(200):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count > 0
        # 角色头仅首次提交（committed_line_count==0）发射一次
        headers = [l for l in m.committed_lines if l.plain.startswith("\u258e\u26a1")]
        assert len(headers) == 1, f"角色头应恰好一次，实际 {len(headers)}"
        # 开放块未关闭 → 尚无卡片尾空行（无空 plain 行）
        assert all(l.plain != "" for l in m.committed_lines), "开放块不应有尾空行"
        m.close_tool_box("t1", True)
        # 关闭提交（新增状态行）→ 卡片尾空行恰好一次
        assert m.committed_lines[-1].plain == "", "关闭后应有卡片尾空行"
        assert sum(1 for l in m.committed_lines if l.plain == "") == 1, "尾空行应恰好一次"

    def test_tool_output_under_threshold_no_incremental(self):
        """工具输出 <64 行 → 不触发增量提交（committed_line_count 保持 0）。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        for i in range(10):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count == 0

    def test_cached_ink_lines_frozen_uncommitted_tail(self):
        """close_tool_box 冻结仅未提交部分（已提交行在 committed_lines 中）。"""
        m = _model()
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        for i in range(70):
            m.append_tool_output("t1", f"line{i}\n")
        committed_before = block.committed_line_count
        assert committed_before > 0
        m.close_tool_box("t1", True)
        # 冻结缓存 = 未提交尾（不含已提交行）
        assert block._cached_ink_lines is not None
        assert len(block._cached_ink_lines) == len(block.lines) - committed_before


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

    def test_display_messages_strips_raw_ansi(self):
        """会话历史消息内容透传 ANSI → _content_str 消毒（防宽度膨胀/wrap 截断）。"""
        m = _model()
        raw = "hi \x1b[38;2;102;217;239;49mdef\x1b[0m tail"
        apply_cmd(m, DisplayMsgsCmd(
            messages=[{"role": "user", "content": raw}], speed=0,
        ))
        user_block = next(b for b in m.blocks if b.kind == "user")
        # run.text 无 ESC，宽度按可见字符测量
        assert "\x1b" not in user_block.lines[-1].plain
        assert user_block.lines[-1].width == len(user_block.lines[-1].plain)


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


class TestToolCountHelper:
    """方向5 — 工具计数单一真源（apply 与 _ink_bridge 共用 helper）。"""

    def test_tool_count_inc_helper(self):
        """tool_count_inc 递增 count/total 并启动 tool_phase_start。"""
        from src.tui.app.apply import tool_count_inc
        m = _model()
        st = m.status
        tool_count_inc(st)
        tool_count_inc(st)
        assert st.tool_count == 2
        assert st.tool_total == 2
        assert st.tool_phase_start > 0

    def test_tool_count_dec_helper(self):
        """tool_count_dec 递减并复位 tool_phase_start。"""
        from src.tui.app.apply import tool_count_dec, tool_count_inc
        m = _model()
        st = m.status
        tool_count_inc(st)
        assert st.tool_phase_start > 0
        tool_count_dec(st)
        assert st.tool_count == 0
        assert st.tool_phase_start == 0.0

    def test_tool_count_dec_never_negative(self):
        """tool_count_dec 不使计数为负。"""
        from src.tui.app.apply import tool_count_dec
        m = _model()
        tool_count_dec(m.status)
        assert m.status.tool_count == 0

    def test_tool_fail_inc_helper(self):
        """tool_fail_inc 递增 tool_fail。"""
        from src.tui.app.apply import tool_fail_inc
        m = _model()
        tool_fail_inc(m.status)
        assert m.status.tool_fail == 1

    def test_apply_and_ink_bridge_results_consistent(self):
        """InkBridge.increment_tool/decrement_tool/increment_tool_fail 与 apply 路径结果一致。"""
        import io
        from src.tui._ink_bridge import InkBridge
        from src.tui.ink.session import InkSession

        model = _model()
        stream = io.StringIO()
        session = InkSession(model=model, stream=stream)
        bridge = InkBridge(model, session)
        # 注入后 _request_redraw 不抛（session.request_bottom_redraw 安全）
        bridge.increment_tool()
        bridge.increment_tool()
        assert model.status.tool_count == 2
        assert model.status.tool_total == 2
        bridge.decrement_tool()
        assert model.status.tool_count == 1
        bridge.increment_tool_fail()
        assert model.status.tool_fail == 1

        # 与 apply_cmd 路径结果一致
        m2 = _model()
        apply_cmd(m2, ToolCountIncCmd())
        apply_cmd(m2, ToolCountIncCmd())
        apply_cmd(m2, ToolCountDecCmd())
        apply_cmd(m2, ToolFailIncCmd())
        assert (m2.status.tool_count, m2.status.tool_total, m2.status.tool_fail) == (
            model.status.tool_count, model.status.tool_total, model.status.tool_fail,
        )


class TestSubagentMarkdown:
    """SUBAGENT_MARKDOWN → 消息区块（kind "subagent"）。"""

    def test_subagent_markdown_appends_block(self):
        m = _model()
        apply_cmd(m, SubagentMarkdownCmd(text="### 1. [ex] t1\ndo the thing"))
        block = m.blocks[-1]
        assert block.kind == "subagent"
        assert block.closed is True  # 立即提交（已关闭块）
        # 标题渲染进块内行
        assert any("t1" in l.plain for l in block.lines)
        assert any("do the thing" in l.plain for l in block.lines)

    def test_subagent_markdown_empty_skipped(self):
        m = _model()
        apply_cmd(m, SubagentMarkdownCmd(text=""))
        assert m.blocks == []

    def test_subagent_markdown_whitespace_skipped(self):
        m = _model()
        apply_cmd(m, SubagentMarkdownCmd(text="   \n  "))
        assert m.blocks == []


class TestCommitBlockFreeze:
    """方向5 — commit_block 全块提交完成冻结（append_committed 立即关闭块）。"""

    def test_append_committed_freezes_cache_regression(self):
        """append_committed 创建的立即关闭块自动冻结 _cached_ink_lines。"""
        m = _model()
        apply_cmd(m, NotificationCmd(text="hi"))
        block = m.blocks[-1]
        assert block.closed is True
        assert block._cached_ink_lines is not None
        assert len(block._cached_ink_lines) == len(block.lines)

    def test_sandwiched_closed_block_freezes_after_commit_regression(self):
        """被开放块夹住的已关闭块：后续提交推进后提交并冻结。"""
        from src.tui._const import ContentCmd
        m = _model()
        # 开放 content 块在前（不关闭）
        apply_cmd(m, ContentCmd(text="streaming line\n"))
        assert m.blocks[0].closed is False
        # append_committed 创建的 closed 块被开放块夹住（未提交、未冻结）
        apply_cmd(m, NotificationCmd(text="sandwiched"))
        sand_block = m.blocks[-1]
        assert sand_block.closed is True
        assert sand_block._cached_ink_lines is None  # 被夹住未提交 → 未冻结
        # 开放块关闭（commit_block 仅处理到 content_block_index，夹住块未推进）
        apply_cmd(m, PhaseDoneCmd(phase="content"))
        assert m.blocks[0].closed is True
        assert sand_block._cached_ink_lines is None
        # 后续 append_committed 推进 commit_block → 夹住的已关闭块提交并冻结
        apply_cmd(m, NotificationCmd(text="after"))
        assert sand_block.committed_line_count == len(sand_block.lines)
        assert sand_block._cached_ink_lines is not None
        assert len(sand_block._cached_ink_lines) == len(sand_block.lines)


class TestDisplayMsgsSeparatorDedup:
    """方向6 — _do_display_messages 分隔线去重。"""

    def test_consecutive_unhandled_role_no_duplicate_separator(self):
        """连续 DISPLAY_MSGS：第二批为未处理 role（不产行）→ 仅一条分隔线。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        assert m.blocks[-1].kind == "write_line"  # 分隔线
        # 第二批 role=system（未处理 → 不产行）→ 上次提交行为分隔线 → 跳过
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "system", "content": "sys"}], speed=0))
        separators = [b for b in m.blocks if b.kind == "write_line"
                      and b.lines and b.lines[0].plain.startswith("  \u2500")]
        assert len(separators) == 1, (
            f"连续 DISPLAY_MSGS 应仅一条分隔线，实际 {len(separators)}"
        )

    def test_different_content_keeps_separator_per_batch(self):
        """不同消息内容两次 → 各消息行 + 各批分隔线（不误去重）。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "assistant", "content": "a2"}], speed=0))
        user_blocks = [b for b in m.blocks if b.kind == "user"]
        write_blocks = [b for b in m.blocks if b.kind == "write_line"]
        separators = [b for b in m.blocks if b.kind == "write_line"
                      and b.lines and b.lines[0].plain.startswith("  \u2500")]
        assert len(user_blocks) == 1
        assert any("m1" in b.lines[0].plain for b in user_blocks)
        # write_line 块 = assistant 行 + 两批各一条分隔线
        assert len(write_blocks) == 3, (
            f"write_line 块应含 assistant 行 + 2 条分隔线，实际 {len(write_blocks)}"
        )
        assert any("a2" in b.lines[0].plain for b in write_blocks)
        assert len(separators) == 2

    def test_user_message_line_not_mistaken_for_separator(self):
        """分隔线判定不误伤用户消息行（前缀 `  > ` 不是 `  ─`）。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        # 用户行渲染后 → 分隔线仍追加（用户行不被判为分隔线）
        assert m.blocks[-1].kind == "write_line"
        assert m.blocks[-1].lines[0].plain.startswith("  \u2500")


class TestReflowCommitted:
    """卡片结构 — 终端宽度变化重排（reflow_committed）。

    committed_lines 提交时按旧宽度 wrap；宽度变化后须按新宽度重建（重排），
    保证「行级 diff 宽度不变量」（committed 每行 ink Line 宽度 <= width）并
    保留卡片头/尾空行。重排产出新列表对象（前缀缓存自动失效）。
    """

    @staticmethod
    def _wide_content():
        from src.renderer.ansi.helpers import AnsiLine
        from src.renderer.ansi.style import Style
        return AnsiLine.of("a" * 100, Style(fg=1))  # 100 列 ASCII 超宽

    def test_shrink_rewraps_committed_lines(self):
        """宽→窄：重排后 committed_lines 各行 ≤ 新宽度，头/尾空行保留。"""
        m = _model()
        m.width = 40
        m.append_committed("content", [self._wide_content()])
        assert len(m.committed_lines) == 5  # 头 + 3 wrap 正文 + 空
        m.reflow_committed(20)
        assert all(ln.width <= 20 for ln in m.committed_lines), (
            "缩窄后 committed 每行宽度应 ≤ 20"
        )
        plains = [ln.plain for ln in m.committed_lines]
        assert plains[0] == "▎回答"
        assert plains[-1] == ""  # 尾空行保留
        assert plains[1].startswith("a")

    def test_grow_keeps_width_invariant(self):
        """窄→宽：重排后每行 ≤ 新宽度（行不重新合并，仅保证不变量）。"""
        m = _model()
        m.width = 20
        m.append_committed("content", [self._wide_content()])
        m.reflow_committed(80)
        assert all(ln.width <= 80 for ln in m.committed_lines)

    def test_open_block_tail_not_mixed(self):
        """open 块（增量提交）重排：仅已提交行重建，未提交尾不混入。"""
        m = _model()
        m.width = 40
        m.open_tool_box("t1", "read_file")
        block = m.blocks[-1]
        for i in range(70):
            m.append_tool_output("t1", f"line{i}" + "x" * 60 + "\n")
        assert block.committed_line_count > 0
        tail = [l.plain for l in block.lines[block.committed_line_count:]]
        assert any("line69" in p for p in tail)  # 未提交尾在块内
        m.reflow_committed(20)
        assert all(ln.width <= 20 for ln in m.committed_lines)
        plains = [ln.plain for ln in m.committed_lines]
        assert "line69" not in "".join(plains), "未提交尾不应混入 committed"
        assert plains[0] == "▎⚡ 工具 read_file"

    def test_closed_tool_header_icon_trailer_preserved(self):
        """关闭工具块重排：头/关闭图标/尾空行保留，_first_committed_offset 重建。"""
        m = _model()
        m.width = 40
        m.open_tool_box("t1", "bash")
        for i in range(70):
            m.append_tool_output("t1", f"out{i}\n")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        assert len(m.committed_lines) == len(block.lines) + 2
        m.reflow_committed(30)
        plains = [ln.plain for ln in m.committed_lines]
        assert plains[0] == "▎⚡ 工具 bash"
        assert plains[1].startswith("✔")  # 关闭图标保留
        assert plains[-1] == ""  # 尾空行保留
        assert sum(1 for p in plains if p == "") == 1
        offset = block.extra["_first_committed_offset"]
        assert m.committed_lines[offset].plain == "▎⚡ 工具 bash"
        assert m.committed_lines[offset + 1].plain.startswith("✔")

    def test_idempotent_same_width(self):
        """同宽度/非法宽度调用 → 不重建（引用不变）。"""
        from src.renderer.ansi.helpers import AnsiLine
        m = _model()
        m.width = 40
        m.append_committed("user", [AnsiLine.of("hi")])
        before = m.committed_lines
        m.reflow_committed(40)
        assert m.committed_lines is before
        m.reflow_committed(0)
        assert m.committed_lines is before

    def test_reflow_produces_new_list(self):
        """宽度变化重排产出新列表对象（前缀缓存自动失效的前提）。"""
        from src.renderer.ansi.helpers import AnsiLine
        m = _model()
        m.width = 40
        m.append_committed("user", [AnsiLine.of("hi")])
        before = m.committed_lines
        m.reflow_committed(60)
        assert m.committed_lines is not before
