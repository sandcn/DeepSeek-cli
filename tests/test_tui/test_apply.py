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
    ClearMsgsCmd,
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

    def test_splash_icon_and_version(self):
        """BEAUTY-12 — splash 品牌屏：✦ 图标 + 版本号（VERSION 已含 v 前缀）。"""
        m = _model()
        apply_cmd(m, SplashCmd())
        plain = m.blocks[-1].lines[0].plain
        assert "\u2726" in plain, f"splash 应含 ✦ 图标: {plain!r}"
        assert "v" in plain
        # VERSION 已含 ``v`` 前缀——不允许 ``vv`` 重复
        assert "vv" not in plain, f"版本号不应出现 vv 重复: {plain!r}"

    def test_user_message(self):
        m = _model()
        apply_cmd(m, UserMsgCmd(text="hello"))
        block = m.blocks[-1]
        assert block.kind == "user"
        assert block.lines[0].plain == "> hello"

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
        # 无分隔线（对齐 Claude Code：消息间仅空行分隔）；末行为渲染内容
        assert not any(l.plain.startswith("  \u2500") for l in m.blocks[0].lines)

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
        m.open_tool_box("t1", "web_search")
        m.open_tool_box("t2", "bash")
        m.append_tool_output("t1", "a1\n")
        m.append_tool_output("t2", "b1")
        m.append_tool_output("t1", "a2\n")
        m.append_tool_output("t2", "b2")
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
        m.open_tool_box("t1", "web_search")
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
        m.open_tool_box("t1", "web_search")
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
        m.open_tool_box("t1", "web_search")
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

    def test_tool_open_same_id_reuses_box_no_orphan(self):
        """同一非空 tool_id 重复 open → 复用同一 box（不新建块）。

        回归：修复前重复 open 覆盖 tool_boxes[tool_id]，旧块成为孤儿
        （● 空卡永不关闭、只渲染一个顶边框，TUI 显示多一行）。
        """
        m = _model()
        m.width = 40
        first = m.open_tool_box("t1", "bash", "make build")
        second = m.open_tool_box("t1", "bash", "make build")
        # 复用同一 block，不新建
        assert second is first
        assert len(m.blocks) == 1
        assert m.tool_boxes["t1"] is first
        # 输出/关闭均作用于该 box，无孤儿残留
        for i in range(10):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        assert m.blocks[-1].closed is True
        assert m.blocks[-1].extra["tool_status"] == "done"
        assert m.tool_boxes == {}
        # 渲染后无重复顶边框（committed_lines 仅一个 `┌─` 首行）
        tops = [ln.plain for ln in m.committed_lines if ln.plain.startswith("\u250c")]
        assert len(tops) == 1, f"重复 open 不应产生孤儿顶边框: {tops}"

    def test_tool_open_after_output_fallback_reuses_box(self):
        """append_tool_output 兜底建 box 后 ToolStartedEvent 后到 → 复用并补全标题。

        回归：修复前兜底 box（tool_name=""）与后到 open 的 box 并存，兜底 box
        成为孤儿（● 空卡），后到 open 的标题信息（Bash·detail）只出现在新块。
        """
        from src.tools.registry import get_tool_display_name
        m = _model()
        m.width = 40
        # 输出先到（兜底建 box，tool_name 为空）
        m.append_tool_output("t1", "line0")
        fallback = m.blocks[-1]
        assert fallback.extra.get("tool_name") == ""
        # ToolStartedEvent 后到 → 复用兜底 box 并补全标题
        reopened = m.open_tool_box("t1", "bash", "make build")
        assert reopened is fallback
        assert len(m.blocks) == 1
        assert reopened.extra.get("tool_name") == "bash"
        assert "make build" in reopened.lines[0].plain
        assert get_tool_display_name("bash") in reopened.lines[0].plain
        # 输出累积到同一 box（无孤儿）
        m.append_tool_output("t1", "line1")
        m.close_tool_box("t1", True)
        assert m.blocks[-1].closed is True
        assert m.tool_boxes == {}

    def test_tool_open_empty_id_still_separate_boxes(self):
        """空 tool_id 重复 open 仍创建独立 box（行为不变，不触发复用路径）。"""
        m = _model()
        first = m.open_tool_box("", "first")
        second = m.open_tool_box("", "second")
        assert first is not second
        assert len(m.blocks) == 2
        # 最近者关闭，最早者保留（倒序语义，与既有 close("") 行为一致）
        m.close_tool_box("", True)
        assert m.blocks[0].closed is False
        assert m.blocks[1].closed is True

    def test_bash_output_tail_display(self):
        """bash 输出超过 3 行 → 只保留最后 3 行 + 省略提示「… 前 N 行省略」。"""
        m = _model()
        m.width = 40
        m.open_tool_box("t1", "bash", "make build")
        for i in range(10):
            m.append_tool_output("t1", f"line{i}")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        # block.lines 修剪为 标题 + 最后 3 行 + 状态行
        assert len(block.lines) == 1 + 3 + 1
        assert "line9" in block.lines[-2].plain
        # 省略计数记录（10 输出 - 3 保留 = 7）
        assert block.extra["_bash_omitted_lines"] == 7
        # 卡片渲染：顶边框 + 省略提示 + 最后 3 行 + 底边框；前置行不显示
        plains = [l.plain for l in m.committed_lines]
        assert "前 7 行省略" in "".join(plains)
        assert any("line7" in p for p in plains)
        assert any("line9" in p for p in plains)
        assert not any("line0" in p for p in plains)

    def test_bash_output_tail_narrow_terminal_no_overflow(self):
        """窄终端 + bash 大量输出 → 省略提示行截断至内宽，卡片不撑破（无超宽行）。

        回归：修复前省略提示行「… 前 N 行省略」未按内宽截断，width≤16 时
        提示文本（宽 18）撑破卡片边框（超宽行显示错乱）。
        """
        for width in (20, 16, 14, 12, 10, 8, 6):
            m = _model()
            m.width = width
            m.open_tool_box("t1", "bash", "make build")
            for i in range(50):
                m.append_tool_output("t1", f"line{i}-" + "y" * 20)
            m.close_tool_box("t1", True)
            # 省略提示已触发
            assert m.blocks[-1].extra.get("_bash_omitted_lines", 0) > 0
            # 卡片所有行宽度 ≤ 终端宽度（无撑破边框的超宽行）
            for i, ln in enumerate(m.committed_lines):
                assert ln.width <= width, (
                    f"width={width} committed_lines[{i}] 宽度 {ln.width} 超宽: {ln.plain!r}"
                )

    def test_head_tools_display(self):
        """find/search/ls/read_file 输出超过 3 行 → 只保留前 3 行 + 省略提示「… 后 N 行省略」。"""
        for tool in ("find", "search", "ls", "read_file"):
            m = _model()
            m.width = 40
            m.open_tool_box("t1", tool)
            for i in range(10):
                m.append_tool_output("t1", f"line{i}")
            m.close_tool_box("t1", True)
            block = m.blocks[-1]
            # block.lines 修剪为 标题 + 前 3 行 + 状态行
            assert len(block.lines) == 1 + 3 + 1, tool
            assert "line2" in block.lines[-2].plain, tool
            # 省略计数记录（10 输出 - 3 保留 = 7）
            assert block.extra["_head_omitted_lines"] == 7, tool
            # 卡片渲染：顶边框 + 前 3 行 + 省略提示 + 底边框；后置行不显示
            plains = [l.plain for l in m.committed_lines]
            assert "后 7 行省略" in "".join(plains), tool
            assert any("line0" in p for p in plains), tool
            assert any("line2" in p for p in plains), tool
            assert not any("line3" in p for p in plains), tool
            assert not any("line9" in p for p in plains), tool

    def test_head_tools_under_three_lines_unchanged(self):
        """find/search/ls/read_file 输出 ≤3 行 → 不修剪（无省略提示）。"""
        for tool in ("find", "search", "ls", "read_file"):
            m = _model()
            m.open_tool_box("t1", tool)
            for i in range(3):
                m.append_tool_output("t1", f"line{i}")
            m.close_tool_box("t1", True)
            block = m.blocks[-1]
            # 标题 + 3 行输出 + 状态行（不修剪）
            assert len(block.lines) == 1 + 3 + 1, tool
            assert "_head_omitted_lines" not in block.extra, tool
            assert "_bash_omitted_lines" not in block.extra, tool

    def test_head_output_narrow_terminal_no_overflow(self):
        """窄终端 + find 大量输出 → 省略提示行截断至内宽，卡片不撑破（无超宽行）。

        对齐 bash 尾显示回归：省略提示文本超内宽会撑破卡片边框，窄终端错乱。
        """
        for width in (20, 16, 14, 12, 10, 8, 6):
            m = _model()
            m.width = width
            m.open_tool_box("t1", "find")
            for i in range(50):
                m.append_tool_output("t1", f"line{i}-" + "y" * 20)
            m.close_tool_box("t1", True)
            # 省略提示已触发
            assert m.blocks[-1].extra.get("_head_omitted_lines", 0) > 0
            # 卡片所有行宽度 ≤ 终端宽度（无撑破边框的超宽行）
            for i, ln in enumerate(m.committed_lines):
                assert ln.width <= width, (
                    f"width={width} committed_lines[{i}] 宽度 {ln.width} 超宽: {ln.plain!r}"
                )

    def test_tool_summary_closes_open_box(self):
        m = _model()
        apply_cmd(m, ToolOpenCmd(tool_name="x", tool_id="t1"))
        apply_cmd(m, ToolSummaryCmd(successful=("x",), failed=()))
        assert m.blocks[-1].closed is True

    def test_tool_output_incremental_commit_threshold(self):
        """工具输出 >64 行 → committed_line_count 推进、committed_lines 含已提交行。"""
        m = _model()
        m.open_tool_box("t1", "web_search")
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
        m.open_tool_box("t1", "web_search")
        block = m.blocks[-1]
        for i in range(70):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count > 0  # 增量提交已发生
        m.close_tool_box("t1", True)
        # 关闭后全部行已提交（committed_line_count == len）
        assert block.committed_line_count == len(block.lines)
        # 无重复行：卡片结构下 committed_lines = 顶边框 + 主体行（标题行被顶边框
        # 替代、状态行被跳过——移入底边框）+ 底边框 + 卡片尾空行 → 块行 +1
        assert len(m.committed_lines) == len(block.lines) + 1, (
            f"关闭后 committed_lines 应 = 块行 + 底边框，committed={len(m.committed_lines)} lines={len(block.lines)}"
        )
        committed_plains = [l.plain for l in m.committed_lines]
        # 尾行为卡片空行；✔ 在顶边框与底边框 `✔ 完成`（去空行后）
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
        m.open_tool_box("t1", "web_search")
        block = m.blocks[-1]
        # 输出行数远超阈值 → 多次 commit_open_block 增量提交
        for i in range(200):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count > 0
        # 工具卡片顶边框仅首次提交（committed_line_count==0）发射一次
        # （卡片化后顶边框替代 `▎⚡` 角色头，无独立角色头行）
        borders = [l for l in m.committed_lines if l.plain.startswith("\u250c")]
        assert len(borders) == 1, f"工具卡片顶边框应恰好一次，实际 {len(borders)}"
        # 开放块未关闭 → 尚无卡片尾空行（无空 plain 行）
        assert all(l.plain != "" for l in m.committed_lines), "开放块不应有尾空行"
        m.close_tool_box("t1", True)
        # 关闭提交（新增状态行）→ 卡片尾空行恰好一次
        assert m.committed_lines[-1].plain == "", "关闭后应有卡片尾空行"
        assert sum(1 for l in m.committed_lines if l.plain == "") == 1, "尾空行应恰好一次"

    def test_tool_output_under_threshold_no_incremental(self):
        """工具输出 <64 行 → 不触发增量提交（committed_line_count 保持 0）。"""
        m = _model()
        m.open_tool_box("t1", "web_search")
        block = m.blocks[-1]
        for i in range(10):
            m.append_tool_output("t1", f"line{i}\n")
        assert block.committed_line_count == 0

    def test_cached_ink_lines_frozen_uncommitted_tail(self):
        """close_tool_box 冻结仅未提交部分（已提交行在 committed_lines 中）。"""
        m = _model()
        m.open_tool_box("t1", "web_search")
        block = m.blocks[-1]
        for i in range(70):
            m.append_tool_output("t1", f"line{i}\n")
        committed_before = block.committed_line_count
        assert committed_before > 0
        m.close_tool_box("t1", True)
        # 冻结缓存 = 未提交尾（不含已提交行 + 底边框；状态行跳过——已移入底
        # 边框，故与「未提交行数」恰好相等）
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
        assert m.blocks[-1].kind == "user"  # 无消息间分隔线（对齐 Claude Code）

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
        # 卡片结构：冻结缓存 = 顶边框 + 主体行 + 底边框（标题行被顶边框替代、
        # 状态行跳过移入底边框 → 与块行数相等）
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


class TestDisplayMsgsNoSeparator:
    """方向6 — _do_display_messages 无消息间分隔线（对齐 Claude Code：仅空行分隔）。"""

    def test_no_separator_after_user_message(self):
        """用户消息后不追加分隔线块（仅 user 块）。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        assert m.blocks[-1].kind == "user"
        assert not any(b.kind == "write_line" for b in m.blocks)

    def test_system_role_produces_no_block(self):
        """未处理 role（system）不产块、无分隔线。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        n = len(m.blocks)
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "system", "content": "sys"}], speed=0))
        assert len(m.blocks) == n  # 不追加任何块
        assert not any(l.plain.startswith("  \u2500") for b in m.blocks for l in b.lines)

    def test_user_and_assistant_messages_no_separator(self):
        """user + assistant 消息 → user 块 + assistant（write_line）块，无分隔线。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "m1"}], speed=0))
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "assistant", "content": "a2"}], speed=0))
        kinds = [b.kind for b in m.blocks]
        assert kinds == ["user", "write_line"]
        assert any("a2" in b.lines[0].plain for b in m.blocks if b.kind == "write_line")
        assert not any(l.plain.startswith("  \u2500") for b in m.blocks for l in b.lines)


class TestClearMsgs:
    """CLEAR_MSGS — 清空消息区显示（编辑会话重渲染前使用）。"""

    def test_clear_resets_blocks_committed(self):
        """clear 后 blocks/committed_lines/committed_count 全部清空。"""
        m = _model()
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "old"}], speed=0))
        apply_cmd(m, WriteLineCmd(text="  \u2500 old separator"))
        assert len(m.blocks) == 2
        assert len(m.committed_lines) > 0
        assert m.committed_count > 0

        apply_cmd(m, ClearMsgsCmd())

        assert m.blocks == []
        assert m.committed_lines == []
        assert m.committed_count == 0

    def test_clear_keeps_status_and_input(self):
        """clear 保留底部栏状态与输入缓冲（reset_display 语义）。"""
        m = _model()
        m.status.model_name = "deepseek-chat"
        m.input_text = "partial input"
        m.input_cursor = 5
        apply_cmd(m, DisplayMsgsCmd(messages=[{"role": "user", "content": "old"}], speed=0))

        apply_cmd(m, ClearMsgsCmd())

        assert m.status.model_name == "deepseek-chat"
        assert m.input_text == "partial input"
        assert m.input_cursor == 5

    def test_clear_then_display_rerenders_fresh(self):
        """clear + display 同批：旧显示消失，剩余消息全新渲染一次（无残留副本）。"""
        m = _model()
        # 模拟编辑前旧显示（user + assistant 消息）
        apply_cmd(m, DisplayMsgsCmd(messages=[
            {"role": "user", "content": "old user"},
            {"role": "assistant", "content": "old assistant"},
        ], speed=0))
        old_blocks = m.blocks

        # 编辑生效后：clear → 只重渲染剩余消息
        apply_cmd(m, ClearMsgsCmd())
        apply_cmd(m, DisplayMsgsCmd(messages=[
            {"role": "user", "content": "kept user"},
        ], speed=0))

        assert m.blocks != old_blocks
        assert len(m.blocks) == 1
        assert m.blocks[0].kind == "user"
        assert "kept user" in m.blocks[0].lines[0].plain
        # 被编辑掉的旧内容不再显示
        plains = [l.plain for b in m.blocks for l in b.lines]
        assert not any("old user" in p for p in plains)
        assert not any("old assistant" in p for p in plains)


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
        """宽→窄：重排后 committed_lines 各行 ≤ 新宽度，尾空行保留（无头）。"""
        m = _model()
        m.width = 40
        m.append_committed("content", [self._wide_content()])
        assert len(m.committed_lines) == 4  # 3 wrap 正文 + 空（content 无角色头）
        m.reflow_committed(20)
        assert all(ln.width <= 20 for ln in m.committed_lines), (
            "缩窄后 committed 每行宽度应 ≤ 20"
        )
        plains = [ln.plain for ln in m.committed_lines]
        assert plains[0].startswith("a")
        assert plains[-1] == ""  # 尾空行保留

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
        m.open_tool_box("t1", "web_search")
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
        # 卡片化：重排后首行为工具卡片顶边框（running ● 图标 + 显示名 WebSearch）
        assert plains[0].startswith("\u250c"), "重排后卡片首行应为顶边框"
        assert "\u25cf" in plains[0], "顶边框应含 running ● 状态图标"
        assert "WebSearch" in plains[0], "顶边框应含工具显示名"

    def test_closed_tool_header_icon_trailer_preserved(self):
        """关闭工具块重排：头/关闭图标/尾空行保留，_first_committed_offset 重建。"""
        m = _model()
        m.width = 40
        # web_search（非 bash，不受输出尾截断影响——通用长工具重排路径）
        m.open_tool_box("t1", "web_search")
        for i in range(70):
            m.append_tool_output("t1", f"out{i}\n")
        m.close_tool_box("t1", True)
        block = m.blocks[-1]
        # 卡片结构：顶边框 + 主体行（状态行跳过移入底边框）+ 底边框 + 尾空行
        assert len(m.committed_lines) == len(block.lines) + 1
        m.reflow_committed(30)
        plains = [ln.plain for ln in m.committed_lines]
        assert plains[0].startswith("\u250c"), "关闭工具卡首行应为顶边框"
        assert "\u2714" in plains[0], "顶边框应含关闭 ✔ 状态图标"
        assert plains[-1] == ""  # 尾空行保留
        assert sum(1 for p in plains if p == "") == 1
        offset = block.extra["_first_committed_offset"]
        assert m.committed_lines[offset].plain.startswith("\u250c"), "offset 应指向顶边框"
        assert "\u2714" in m.committed_lines[offset].plain, "顶边框应含关闭 ✔ 图标"

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
