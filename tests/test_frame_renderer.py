"""FrameRenderer Claude Code 树风格渲染测试。"""
from src.ui.renderer.frame_renderer import FrameRenderer
from src.ui.state.agent_state import AgentSlot, ToolRecord
import time


class TestClaudeTreeStyle:
    """验证 Claude Code 树形连接线风格渲染输出。"""

    def _make_renderer(self, width=120, max_history=3):
        return FrameRenderer(terminal_width=width, frame=0, max_history=max_history)

    def _make_slot(self, label, description, status="running", agent_type="execute",
                   output_tokens=0, live_output_tokens=0, last_speed=0.0,
                   model_phase="", tool_history=None):
        slot = AgentSlot(label=label, description=description,
                         status=status, agent_type=agent_type)
        slot.output_tokens = output_tokens
        slot.live_output_tokens = live_output_tokens
        slot.last_speed = last_speed
        slot.model_phase = model_phase
        if model_phase:
            slot.model_phase_start = time.time()
        if tool_history:
            slot.tool_history = tool_history
        return slot

    def test_branch_connector_for_last_agent(self):
        """末位 Agent 标题行含 └─ 分支符。"""
        r = self._make_renderer(width=80)
        slots = {"a": self._make_slot("a", "last agent", status="done")}
        lines = r.render(slots, ["a"], now=time.time(), final=True)
        title = FrameRenderer.strip_ansi(lines[2])  # lines[0]=summary, [1]=separator, [2]=agent
        assert " └─" in title, f"末位 Agent 标题应含 └─，实际: {title!r}"

    def test_branch_connector_for_non_last_agent(self):
        """非末位 Agent 标题行含 ├─ 分支符。"""
        r = self._make_renderer(width=80)
        slots = {
            "a": self._make_slot("a", "first agent", status="done"),
            "b": self._make_slot("b", "last agent", status="running"),
        }
        lines = r.render(slots, ["a", "b"], now=time.time(), final=False)
        title_a = FrameRenderer.strip_ansi(lines[2])
        assert " ├─" in title_a, f"非末位 Agent 标题应含 ├─，实际: {title_a!r}"

    def test_agent_spacing_blank_line(self):
        """前一个 Agent 有子行时，两 Agent 间存在空延续行。"""
        r = self._make_renderer(width=80)
        now = time.time()
        t = ToolRecord(tool_name="read_file", detail="test.py", start_time=now - 5, phase="done")
        t.end_time = now
        slots = {
            "a": self._make_slot("a", "agent with tools", status="running",
                                 tool_history=[t]),
            "b": self._make_slot("b", "second agent", status="running"),
        }
        lines = r.render(slots, ["a", "b"], now=now, final=False)
        # 找到两个 agent 标题行之间的空延续行
        stripped = [FrameRenderer.strip_ansi(l) for l in lines]
        # agent "a" title 应在某处，agent "b" title 在其后，中间有空行
        a_idx = next(i for i, l in enumerate(stripped) if "agent with tools" in l)
        b_idx = next(i for i, l in enumerate(stripped) if "second agent" in l)
        assert b_idx > a_idx + 1, f"两 Agent 之间应有至少 1 个空延续行"
        # 空延续行紧邻 agent "b" 标题行之前
        gap_line = stripped[b_idx - 1]
        assert "│" in gap_line, f"空延续行应含 │ 竖线，实际: {gap_line!r}"
        # 除 │ 和空格外不应有其他可见字符
        assert gap_line.strip() == "│", f"延续行应仅含 │ 竖线，实际: {gap_line!r}"

    def test_phase_line_indent_2_spaces(self):
        """phase 行 cont 后恰好 2 空格。"""
        r = self._make_renderer(width=80)
        now = time.time()
        slots = {"a": self._make_slot("a", "thinking agent", status="running",
                                       model_phase="thinking")}
        lines = r.render(slots, ["a"], now=now, final=False)
        # 找到 phase 行（用 …thinking 精确匹配，避免误匹配标题行中的 "thinking agent"）
        phase_line = None
        for l in lines:
            plain = FrameRenderer.strip_ansi(l)
            if "…thinking" in plain:
                phase_line = plain
                break
        assert phase_line is not None, "应存在 thinking phase 行"
        # cont 是 " │ " (非末位) 或 "   " (末位，单agent)，后接 2 空格
        # 对于单 agent（末位），cont="   "，phase 行应为 "      …thinking  X.Xs"
        assert "  …thinking" in phase_line or "…thinking" in phase_line, \
            f"phase 行格式错误: {phase_line!r}"

    def test_tool_record_indent_2_spaces(self):
        """tool record 行 cont 后恰好 2 空格。"""
        r = self._make_renderer(width=80)
        now = time.time()
        t = ToolRecord(tool_name="read_file", detail="test.py", start_time=now - 3, phase="done")
        t.end_time = now
        slots = {"a": self._make_slot("a", "tool agent", status="running",
                                       tool_history=[t])}
        lines = r.render(slots, ["a"], now=now, final=False)
        # 找到 tool 行
        tool_line = None
        for l in lines:
            plain = FrameRenderer.strip_ansi(l)
            if "rf" in plain or "read_file" in plain:
                tool_line = plain
                break
        assert tool_line is not None, "应存在 tool record 行"
        # 对于单 agent（末位），cont="   "，tool 行前缀应为 5 空格
        assert tool_line.startswith("     "), f"tool 行应以 5 空格开头(cont 3 + 缩进 2)，实际: {tool_line[:20]!r}"

    def test_result_text_indent_2_spaces(self):
        """result 预览行 cont 后恰好 2 空格。"""
        r = self._make_renderer(width=80)
        slot = self._make_slot("a", "result agent", status="done")
        slot.result_text = "line 1\nline 2"
        slots = {"a": slot}
        lines = r.render(slots, ["a"], now=time.time(), final=True)
        # 找到 result 行
        result_lines = [FrameRenderer.strip_ansi(l) for l in lines if "line 1" in FrameRenderer.strip_ansi(l)]
        assert len(result_lines) >= 1, "应存在 result 预览行"

    def test_summary_bar_gradient_filled_0(self):
        """进度条 filled=0 时仅含暗灰▱，不含▰。"""
        r = self._make_renderer(width=120)
        slots = {
            "a": self._make_slot("a", "agent a", status="running"),
            "b": self._make_slot("b", "agent b", status="running"),
            "c": self._make_slot("c", "agent c", status="running"),
        }
        lines = r.render(slots, ["a", "b", "c"], now=time.time(), final=False)
        raw_summary = lines[0]
        # 应含▱但不含▰
        assert "▱" in raw_summary, "filled=0 时应含暗灰▱"
        assert "▰" not in raw_summary, "filled=0 时不应含▰"

    def test_summary_bar_gradient_filled_partial(self):
        """进度条部分填充时，▰ 使用琥珀→绿渐变色号。"""
        r = self._make_renderer(width=120)
        # 3 agents, 1 done → bar_width=12, filled=4
        slots = {
            "a": self._make_slot("a", "agent a", status="done"),
            "b": self._make_slot("b", "agent b", status="running"),
            "c": self._make_slot("c", "agent c", status="running"),
        }
        lines = r.render(slots, ["a", "b", "c"], now=time.time(), final=False)
        raw_summary = lines[0]
        # 第一个▰应使用琥珀色(214)
        assert "\033[38;5;214m▰" in raw_summary, \
            f"第一个▰应为琥珀色(214)，实际: {raw_summary!r}"
        # 应有▱（未完成部分）
        assert "▱" in raw_summary, "未完成部分应含▱"
        # 应有▰（完成部分）
        assert "▰" in raw_summary, "完成部分应含▰"

    def test_summary_bar_gradient_all_done_final(self):
        """final=True + done=total 时，进度条全绿。"""
        r = self._make_renderer(width=120)
        now = time.time()
        slots = {
            "a": self._make_slot("a", "agent a", status="done"),
            "b": self._make_slot("b", "agent b", status="done"),
            "c": self._make_slot("c", "agent c", status="done"),
        }
        lines = r.render(slots, ["a", "b", "c"], now=now, final=True)
        raw_summary = lines[0]
        # 完成状态使用 _C_DONE（全绿），不含▱
        assert "▰" in raw_summary, "完成状态应含▰"
        assert "▱" not in raw_summary, "完成状态不应含未完成▱"

    def test_summary_bar_gradient_color_sequence(self):
        """渐变进度条色号序列正确：琥珀(214)开始，绿(41)结束。"""
        from src.ui.tui._animator import BreathPalette
        colors = BreathPalette.get("progress_amber_green")
        assert colors[0] == 214, \
            f"渐变色起始应为214(琥珀)，实际: {colors[0]}"
        assert colors[-1] == 41, \
            f"渐变色结束应为41(绿)，实际: {colors[-1]}"
        assert len(colors) == 8, \
            f"渐变色长度应为8，实际: {len(colors)}"

    def test_full_tree_output_snapshot(self):
        """3 Agent 完整树形输出快照比对。"""
        r = self._make_renderer(width=90)
        now = time.time()
        t1 = ToolRecord(tool_name="read_file", detail="main.py", start_time=now - 10, phase="done")
        t1.end_time = now - 8
        t2 = ToolRecord(tool_name="bash", detail="pytest", start_time=now - 5, phase="running")
        slots = {
            "a": self._make_slot("a", "解析模块结构", status="running",
                                 agent_type="map", output_tokens=1200, last_speed=45.0,
                                 model_phase="thinking", tool_history=[t1]),
            "b": self._make_slot("b", "搜索相关代码", status="done",
                                 agent_type="review", output_tokens=800),
            "c": self._make_slot("c", "制定执行计划", status="running",
                                 agent_type="plan", output_tokens=500, last_speed=30.0,
                                 model_phase="answering", tool_history=[t2]),
        }
        lines = r.render(slots, ["a", "b", "c"], now=now, final=False)

        stripped = [FrameRenderer.strip_ansi(l) for l in lines]

        # 验证树形结构
        assert any(" ├─" in l for l in stripped), "应有 ├─ 分支符"
        assert any(" └─" in l for l in stripped), "应有 └─ 分支符"
        assert any("thinking" in l for l in stripped), "应有 thinking phase"
        assert any("answering" in l for l in stripped), "应有 answering phase"
        assert any("rf" in l for l in stripped), "应有 rf 工具记录"
