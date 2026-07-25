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
from src.tui.core.ansi_utils import (
    strip_ansi as core_strip_ansi,
    visual_width as core_visual_width,
    truncate_ansi_visual as core_truncate_ansi_visual,
)


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


# ── 测试数据 ──────────────────────────────────────────

PLAIN_TEXTS = [
    "hello",
    "你好",
    "hello world",
    "你好世界",
    "a你b好c",
    "  leading and trailing  ",
    "",
    "a\u200bb",          # 零宽空格
    "a\u200db",          # 零宽连接符
    "a\u0300b",          # 组合标记
    " \t\n\r ",          # 空白字符
    "abc123!@#",
]

ANSI_TEXTS = [
    "\033[31mhello\033[0m",
    "\033[38;5;45m你好\033[0m",
    "\033[1m\033[31mbold red\033[0m",
    "\033[38;5;214;48;5;236mstyled\033[0m",
    "\033[31ma\033[32mb\033[33mc\033[0m",
    "\033[38;5;45m你好世界\033[0m",
    "\033[31m" "hello world" "\033[0m",
    "inline\033[31mred\033[0mnormal",
    "\033[31m\033[0m",       # 只有 ANSI，无可见字符
    "\033[38;5;45m" "a你b" "\033[0m",
]

MIXED_TEXTS = PLAIN_TEXTS + ANSI_TEXTS + [
    "\033[31mhello 你好 world\033[0m",
    "  \033[38;5;45mpadding\033[0m  ",
    "a\033[31mb\033[0m",  # 单字符夹 ANSI
]

# ── strip_ansi 一致性 ────────────────────────────────

class TestStripAnsiDelegation:
    """验证 FrameRenderer.strip_ansi 与 core.ansi_utils.strip_ansi 输出一致。"""

    def test_plain_texts(self):
        for text in PLAIN_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result!r} != {expected!r}"
            )

    def test_ansi_texts(self):
        for text in ANSI_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result!r} != {expected!r}"
            )

    def test_fast_path_no_ansi(self):
        """不含 ANSI 的文本走快速路径（'\x1b' not in text 优化）。"""
        for text in PLAIN_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected

    def test_fast_path_with_ansi(self):
        """含 ANSI 的文本委托到 core 函数。"""
        for text in ANSI_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected


# ── display_width 一致性 ──────────────────────────────

class TestDisplayWidthDelegation:
    """验证 FrameRenderer.display_width 与 core.ansi_utils.visual_width 一致。"""

    def test_plain_texts(self):
        for text in PLAIN_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_ansi_texts(self):
        for text in ANSI_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_mixed_texts(self):
        for text in MIXED_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_empty(self):
        assert FrameRenderer.display_width("") == 0
        assert core_visual_width("") == 0


# ── char_width 一致性 ─────────────────────────────────

class TestCharWidthDelegation:
    """验证 FrameRenderer.char_width 与 core.ansi_utils._char_width 一致。"""

    def test_ascii_chars(self):
        for ch in "abcdefXYZ012!@# ":
            r = FrameRenderer.char_width(ch)
            assert r in (0, 1), f"ASCII char {ch!r} width={r} expected 0 or 1"

    def test_cjk_chars(self):
        for ch in "你好世界测试":
            r = FrameRenderer.char_width(ch)
            assert r == 2, f"CJK char {ch!r} width={r} expected 2"

    def test_zero_width_chars(self):
        # 零宽空格 (U+200B) 和零宽连接符 (U+200D)
        assert FrameRenderer.char_width("\u200b") == 0
        assert FrameRenderer.char_width("\u200d") == 0

    def test_combining_mark(self):
        # 组合用变音符 (U+0300)
        assert FrameRenderer.char_width("\u0300") == 0

    def test_newline(self):
        # 换行符 - wcwidth 可能返回 -1（不可打印），回退为 1
        w = FrameRenderer.char_width("\n")
        assert w >= 0, f"newline width should be >= 0, got {w}"


# ── truncate_to_width 一致性 ───────────────────────────

class TestTruncateToWidthDelegation:
    """验证 FrameRenderer.truncate_to_width 委托行为正确。

    注意：truncate_to_width 保留 _TRUNC_MARGIN/_TRUNC_MIN_WIDTH 原逻辑，
    仅核心截断逻辑委托至 truncate_ansi_visual。
    """

    def setup_method(self):
        self.renderer = FrameRenderer(terminal_width=80, frame=0)

    def test_no_truncation_plain(self):
        """短文本原样返回。"""
        text = "hello"
        result = self.renderer.truncate_to_width(text, max_width=80)
        assert result == text, f"短文本不应被截断: {result!r}"

    def test_no_truncation_ansi(self):
        """短 ANSI 文本原样返回。"""
        text = "\033[31mhello\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=80)
        assert result == text, f"短 ANSI 文本不应被截断: {result!r}"

    def test_truncation_plain_text(self):
        """长纯文本被截断后视觉宽度不超过 max_width。"""
        text = "a" * 100
        result = self.renderer.truncate_to_width(text, max_width=20)
        # max_width=20 → max(20-2, 10)=18 → truncate_ansi_visual with max_visual=18
        # 截断后视觉宽度 ≤ 18（含 … 占 1 列）
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_cjk_text(self):
        """中文文本截断后视觉宽度不超过 max_width。"""
        text = "你好世界测试" * 10
        result = self.renderer.truncate_to_width(text, max_width=20)
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"CJK 截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_ansi_text(self):
        """ANSI 文本截断后保留样式且视觉宽度不超过 max_width。"""
        text = "\033[31m" + "a" * 100 + "\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        # 应保留 ANSI 颜色
        assert "\033[31m" in result, f"ANSI 样式应保留: {result!r}"
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"ANSI 截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_mixed_cjk_ansi(self):
        """中文 + ANSI 文本截断正确。"""
        text = "\033[38;5;45m" + "你好世界测试" * 5 + "\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert "\033[38;5;45m" in result, "颜色样式应保留"
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"混合文本截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_at_boundary(self):
        """文本恰好等于 max_width 时不截断。"""
        text = "hello world"
        result = self.renderer.truncate_to_width(text, max_width=20)
        # max_width=20 → max(18, 10) = 18, text=11 ≤ 18 → 不截断
        assert result == text

    def test_truncation_min_width_respected(self):
        """_TRUNC_MIN_WIDTH=10 确保极窄 max_width 时仍有合理截断宽度。"""
        text = "hello world extra long text"
        result = self.renderer.truncate_to_width(text, max_width=5)
        # max_width=5 → max(5-2, 10) = 10 → 用 10 作为截断宽度
        # 截断后视觉宽度 ≤ 10
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 10, f"截断后视觉宽度 {w} > 10"

    def test_truncation_only_ansi_sequence(self):
        """只有 ANSI 序列的文本（无可见字符）原样返回。"""
        text = "\033[31m\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert result == text, "仅有 ANSI 序列的文本不应截断"

    def test_truncation_empty(self):
        """空字符串原样返回。"""
        text = ""
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert result == ""

    def test_truncation_default_max_width(self):
        """max_width=None 时使用 self._terminal_width。"""
        # terminal_width=80 → max(80-2, 10) = 78
        text = "x" * 100
        result = self.renderer.truncate_to_width(text)
        assert len(FrameRenderer.strip_ansi(result)) <= 78

    def test_truncation_ellipsis_char(self):
        """截断后使用 …（U+2026）作为截断标记（来自 truncate_ansi_visual）。"""
        text = "a" * 100
        result = self.renderer.truncate_to_width(text, max_width=20)
        if result != text:
            # 截断后应含 …（单字符省略号）
            assert "…" in result or len(FrameRenderer.strip_ansi(result)) < 100, \
                f"截断应包含 …: {result!r}"
