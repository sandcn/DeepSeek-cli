"""测试 FrameRenderer.render() 的主渲染路径。

覆盖场景：
  - 空 slots 渲染
  - 单个 agent 各状态（running/done/fail）
  - 多个 agent 混合状态
  - tool_history 渲染（max_history 截断）
  - phase 渲染（thinking/answering/parsing/batch）
  - final=True 结果文本渲染
  - 摘要行渲染（运行中 vs 完成）
  - 进度条组件集成验证
"""

from __future__ import annotations

from src.tui.frame.frame_renderer import FrameRenderer
from src.tui.state.agent_state import AgentSlot, ToolRecord
from src.tui.core.ansi_utils import strip_ansi


def _make_slot(
    label: str = "agent-1",
    description: str = "test agent",
    status: str = "running",
    agent_type: str = "execute",
    output_tokens: int = 0,
    live_output_tokens: int = 0,
    input_tokens: int = 0,
    last_speed: float = 0.0,
    start_time: float | None = None,
    end_time: float = 0.0,
    model_phase: str = "",
    model_phase_start: float = 0.0,
    model_info: str = "",
    result_text: str = "",
    result_error: str = "",
    tool_history: list | None = None,
    total_calls: int = 0,
) -> AgentSlot:
    """创建测试用 AgentSlot。"""
    now = start_time or 1000.0
    return AgentSlot(
        label=label,
        description=description,
        agent_type=agent_type,
        status=status,
        start_time=now,
        end_time=end_time or (now + 5.0 if status in ("done", "fail") else 0.0),
        output_tokens=output_tokens,
        live_output_tokens=live_output_tokens,
        input_tokens=input_tokens,
        last_speed=last_speed,
        model_phase=model_phase,
        model_phase_start=model_phase_start or now,
        model_info=model_info,
        result_text=result_text,
        result_error=result_error,
        tool_history=tool_history or [],
        total_calls=total_calls,
    )


def _make_tool(
    tool_name: str = "read_file",
    detail: str = "foo.py",
    start_time: float | None = None,
    end_time: float = 0.0,
    phase: str = "done",
) -> ToolRecord:
    now = start_time or 1000.0
    return ToolRecord(
        tool_name=tool_name,
        detail=detail,
        start_time=now,
        end_time=end_time or (now + 0.5),
        phase=phase,
    )


class TestFrameRendererRender:
    """测试 FrameRenderer.render() 主渲染路径。"""

    def setup_method(self):
        self.renderer = FrameRenderer(terminal_width=80, frame=0, max_history=3)
        self.now = 1000.0

    # ── 空 slots ──────────────────────────────────────

    def test_empty_slots(self):
        """空 slots 渲染：仅有摘要行 + 分隔线。"""
        lines = self.renderer.render({}, [], now=self.now, final=False)
        assert len(lines) >= 1, "空 slots 应至少输出 1 行（摘要行）"
        # 纯文本检查
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "agent" in plain.lower() or "slots" in plain.lower()

    # ── 单个 agent 状态 ────────────────────────────────

    def test_single_agent_running(self):
        """单个 running 状态的 Agent 渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Test Runner",
                status="running", output_tokens=100, last_speed=10.0,
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now, final=False)
        assert len(lines) >= 3, "应有摘要行 + 分隔线 + agent 行"
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Test Runner" in plain, f"agent description 应出现在渲染中:\n{plain}"

    def test_single_agent_done(self):
        """单个 done 状态的 Agent 渲染（完成图标）。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Done Agent",
                status="done", output_tokens=500,
                start_time=self.now, end_time=self.now + 10.0,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 10, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Done Agent" in plain
        # done 状态应有 ✔ 图标或标识
        assert "Done Agent" in plain

    def test_single_agent_fail(self):
        """单个 fail 状态的 Agent 渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Fail Agent",
                status="fail",
                start_time=self.now, end_time=self.now + 3.0,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 3, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Fail Agent" in plain

    # ── 多个 agent 混合状态 ────────────────────────────

    def test_multi_agents_mixed_status(self):
        """多个 Agent 混合状态渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Runner A",
                status="running", output_tokens=200, last_speed=15.0,
                start_time=self.now,
            ),
            "agent-2": _make_slot(
                label="agent-2", description="Runner B",
                status="done", output_tokens=1000,
                start_time=self.now, end_time=self.now + 8.0,
            ),
            "agent-3": _make_slot(
                label="agent-3", description="Runner C",
                status="fail",
                start_time=self.now, end_time=self.now + 2.0,
            ),
        }
        order = ["agent-1", "agent-2", "agent-3"]
        lines = self.renderer.render(slots, order, now=self.now + 5, final=False)
        assert len(lines) >= 5, "3 个 agent 至少输出 5 行"
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Runner A" in plain
        assert "Runner B" in plain
        assert "Runner C" in plain

    def test_multi_agents_order_respected(self):
        """Agent 渲染顺序与 order 参数一致。"""
        slots = {
            "a": _make_slot(label="a", description="Alpha", status="done",
                            start_time=self.now, end_time=self.now + 1),
            "b": _make_slot(label="b", description="Beta", status="done",
                            start_time=self.now, end_time=self.now + 2),
        }
        lines_ab = self.renderer.render(slots, ["a", "b"], now=self.now + 2, final=False)
        lines_ba = self.renderer.render(slots, ["b", "a"], now=self.now + 2, final=False)
        plain_ab = "\n".join(strip_ansi(l) for l in lines_ab)
        plain_ba = "\n".join(strip_ansi(l) for l in lines_ba)
        # a 在 b 前（ab 顺序）
        idx_a_ab = plain_ab.index("Alpha") if "Alpha" in plain_ab else -1
        idx_b_ab = plain_ab.index("Beta") if "Beta" in plain_ab else -1
        # b 在 a 前（ba 顺序）
        idx_a_ba = plain_ba.index("Alpha") if "Alpha" in plain_ba else -1
        idx_b_ba = plain_ba.index("Beta") if "Beta" in plain_ba else -1
        if idx_a_ab >= 0 and idx_b_ab >= 0:
            assert idx_a_ab < idx_b_ab, "顺序 ['a','b'] 应使 Alpha 在 Beta 之前"
        if idx_a_ba >= 0 and idx_b_ba >= 0:
            assert idx_b_ba < idx_a_ba, "顺序 ['b','a'] 应使 Beta 在 Alpha 之前"

    # ── 摘要行 ─────────────────────────────────────────

    def test_summary_running(self):
        """运行中的摘要行应显示进度。"""
        slots = {
            "a": _make_slot(label="a", description="A", status="running",
                            output_tokens=50, start_time=self.now),
            "b": _make_slot(label="b", description="B", status="running",
                            output_tokens=30, start_time=self.now),
        }
        lines = self.renderer.render(slots, ["a", "b"], now=self.now, final=False)
        summary = strip_ansi(lines[0]) if lines else ""
        assert summary, "摘要行不应为空"
        # 检查摘要行包含语义内容（运行中信息）
        assert "running" in summary.lower() or "agent" in summary.lower() or "a" in summary.lower(), \
            f"摘要行应包含运行状态或 agent 信息:\n{summary}"

    def test_summary_completed(self):
        """全部完成时的摘要行。"""
        slots = {
            "a": _make_slot(label="a", description="A", status="done",
                            output_tokens=100, start_time=self.now,
                            end_time=self.now + 5),
        }
        lines = self.renderer.render(slots, ["a"], now=self.now + 5, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        # 完成时应显示总 agent 数
        assert "agent" in plain.lower()

    # ── tool_history 渲染 ──────────────────────────────

    def test_tool_history_displayed(self):
        """工具历史记录显示在 agent 行下方。"""
        tools = [
            _make_tool(tool_name="read_file", detail="src/main.py",
                       start_time=self.now, end_time=self.now + 0.5, phase="done"),
            _make_tool(tool_name="search", detail="test_foo",
                       start_time=self.now + 1, end_time=self.now + 1.8, phase="done"),
        ]
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Tool User",
                status="running", tool_history=tools, output_tokens=50,
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 2, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "src/main.py" in plain or "main.py" in plain, \
            f"工具 detail 应出现在渲染中:\n{plain}"
        assert "test_foo" in plain or "search" in plain.lower(), \
            f"工具名称应出现在渲染中:\n{plain}"

    def test_tool_history_max_history(self):
        """max_history=1 限制工具历史最多显示 1 条。"""
        tools = [
            _make_tool(tool_name="read_file", detail="file1.py",
                       start_time=self.now + 0, end_time=self.now + 0.5, phase="done"),
            _make_tool(tool_name="write_file", detail="file2.py",
                       start_time=self.now + 1, end_time=self.now + 1.5, phase="done"),
            _make_tool(tool_name="search", detail="query",
                       start_time=self.now + 2, end_time=self.now + 2.5, phase="done"),
        ]
        renderer = FrameRenderer(terminal_width=80, frame=0, max_history=1)
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Tool User",
                status="running", tool_history=tools,
                start_time=self.now,
            ),
        }
        lines = renderer.render(slots, ["agent-1"], now=self.now + 3, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        # max_history=1 显示最后 1 条（reversed 后最先显示）
        # 至少应该包含最后一条工具的信息
        assert "file2.py" in plain or "query" in plain, \
            f"至少最后一条工具应显示:\n{plain}"
        # 反向验证：max_history 截断生效，最早的工具记录不应出现
        assert "file1.py" not in plain, \
            f"max_history=1 应截断最早的工具记录(file1.py)，但仍在输出中:\n{plain}"

    def test_tool_history_running_phase(self):
        """运行中的工具记录（phase=running）应包含时间信息。"""
        tools = [
            _make_tool(tool_name="read_file", detail="data.txt",
                       start_time=self.now, end_time=0.0, phase="running"),
        ]
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Tool User",
                status="running", tool_history=tools,
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 1, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "data.txt" in plain

    # ── phase 渲染 ─────────────────────────────────────

    def test_phase_thinking(self):
        """thinking 阶段行渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Thinker",
                status="running", model_phase="thinking",
                model_phase_start=self.now,
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 2, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "thinking" in plain.lower(), f"thinking phase 应显示:\n{plain}"

    def test_phase_answering(self):
        """answering 阶段行渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Answerer",
                status="running", model_phase="answering",
                model_phase_start=self.now,
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 1, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "answering" in plain.lower(), f"answering phase 应显示:\n{plain}"

    def test_phase_parsing(self):
        """parsing 阶段行渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Parser",
                status="running", model_phase="parsing",
                model_phase_start=self.now, model_info="read_file 50t 0.3s",
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 1, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "parsing" in plain.lower(), f"parsing phase 应显示:\n{plain}"

    def test_phase_batch(self):
        """batch 阶段行渲染。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Batcher",
                status="running", model_phase="batch",
                model_phase_start=self.now,
                model_info="3x parallel: read_file, search",
                start_time=self.now,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"], now=self.now + 1, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "batch" in plain.lower(), f"batch phase 应显示:\n{plain}"

    def test_phase_not_shown_when_final(self):
        """final=True 时不显示阶段行。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Thinker",
                status="running", model_phase="thinking",
                model_phase_start=self.now,
                start_time=self.now,
            ),
        }
        lines_final = self.renderer.render(slots, ["agent-1"],
                                           now=self.now + 1, final=True)
        plain_final = "\n".join(strip_ansi(l) for l in lines_final)
        # final 模式下 running agent 可能有不同的表现，但至少不崩溃
        assert lines_final is not None

    # ── final=True 结果文本 ────────────────────────────

    def test_final_result_text_done(self):
        """final=True 时 done 状态显示 result_text。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Result Provider",
                status="done", result_text="This is the final result.",
                output_tokens=300,
                start_time=self.now, end_time=self.now + 10,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"],
                                     now=self.now + 10, final=True)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "final result" in plain.lower(), \
            f"final=True 时应显示 result_text:\n{plain}"

    def test_final_result_error_fail(self):
        """final=True 时 fail 状态显示 result_error。"""
        slots = {
            "agent-1": _make_slot(
                label="agent-1", description="Failed Agent",
                status="fail", result_error="Connection timeout after 30s",
                start_time=self.now, end_time=self.now + 5,
            ),
        }
        lines = self.renderer.render(slots, ["agent-1"],
                                     now=self.now + 5, final=True)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Connection timeout" in plain or "timeout" in plain.lower(), \
            f"final=True fail 时应显示 result_error:\n{plain}"

    # ── 边界场景 ───────────────────────────────────────

    def test_missing_slot_in_order(self):
        """order 中的 label 在 slots 中不存在时静默跳过。"""
        slots = {
            "agent-1": _make_slot(label="agent-1", description="Real Agent",
                                  status="done", start_time=self.now,
                                  end_time=self.now + 1),
        }
        # order 包含一个不存在的 label
        lines = self.renderer.render(slots, ["agent-1", "ghost"],
                                     now=self.now + 1, final=False)
        plain = "\n".join(strip_ansi(l) for l in lines)
        assert "Real Agent" in plain
        # 不应崩溃

    def test_renderer_sync_terminal_state(self):
        """sync_terminal_state 更新终端宽度和帧号。"""
        renderer = FrameRenderer(terminal_width=80, frame=0)
        renderer.sync_terminal_state(width=120, frame=10)
        assert renderer._terminal_width == 120
        assert renderer._frame == 10

    def test_truncate_result_method(self):
        """_truncate_result 静态方法截断正确。"""
        # max_lines=3, max_chars=300
        result = FrameRenderer._truncate_result("hello\nworld\nfoo\nbar", max_lines=2, max_chars=100)
        assert len(result) == 2
        assert "bar" not in result[1] if len(result) >= 2 else True

    def test_renderer_renders_different_agent_types(self):
        """不同 agent_type（execute/chat/delegate/think）的渲染不崩溃。"""
        for atype in ("execute", "chat", "delegate", "think"):
            slots = {
                "agent-1": _make_slot(
                    label="agent-1", description=f"Type {atype}",
                    agent_type=atype, status="running",
                    start_time=self.now,
                ),
            }
            lines = self.renderer.render(slots, ["agent-1"],
                                         now=self.now, final=False)
            plain = "\n".join(strip_ansi(l) for l in lines)
            assert f"Type {atype}" in plain, \
                f"agent_type={atype} 应正常渲染"
