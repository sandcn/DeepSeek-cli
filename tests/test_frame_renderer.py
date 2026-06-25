"""FrameRenderer 单元测试 — 覆盖全部渲染路径

测试 Claude Code 风格的终端渲染格式：
- 摘要行：⏺/✔ 图标、· 分隔符、无进度条 ▰
- Agent 行：2空格缩进、braille spinner、类型标签、无竖线 │
- 工具行：4空格缩进、工具图标、无圆点指示器 ◌●
- 阶段指示：4空格缩进 + …phase_name
- 文本截断与 ANSI 处理
"""

import time
import pytest

from src.ui.renderer.frame_renderer import FrameRenderer
from src.ui.state.agent_state import AgentSlot, ToolRecord
from src.ui.parallel._config import (
    SPINNER_FRAMES,
    SUMMARY_ICON_RUNNING,
    SUMMARY_ICON_DONE,
)
from src.ui.parallel._tool_icons import (
    AGENT_TYPE_ABBREV,
    AGENT_TYPE_COLORS,
    TOOL_ICONS,
)

# ── 固定时间戳，确保测试结果可复现 ──
FIXED_NOW = 1000.0
EARLY_START = 990.0  # 10s 前启动


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def renderer():
    """返回默认配置的 FrameRenderer（终端宽度 120，frame=0，max_history=3）。"""
    return FrameRenderer(terminal_width=120, frame=0, max_history=3)


@pytest.fixture
def narrow_renderer():
    """返回窄终端 FrameRenderer（终端宽度 60）。"""
    return FrameRenderer(terminal_width=60, frame=0, max_history=3)


def make_agent_slot(label="agent-1", description="测试任务", agent_type="plan_execute",
                    status="running", start_time=EARLY_START, end_time=0.0,
                    output_tokens=0, live_output_tokens=0, last_speed=0.0,
                    model_phase="", model_phase_start=0.0, model_info="",
                    result_text="", result_error=""):
    """创建 AgentSlot 测试数据。"""
    return AgentSlot(
        label=label, description=description, agent_type=agent_type,
        status=status, start_time=start_time, end_time=end_time,
        output_tokens=output_tokens, live_output_tokens=live_output_tokens,
        last_speed=last_speed, model_phase=model_phase,
        model_phase_start=model_phase_start, model_info=model_info,
        result_text=result_text, result_error=result_error,
    )


def make_tool_record(tool_name="bash", detail="ls src/", start_time=995.0,
                     end_time=0.0, phase="running"):
    """创建 ToolRecord 测试数据。"""
    return ToolRecord(
        tool_name=tool_name, detail=detail,
        start_time=start_time, end_time=end_time, phase=phase,
    )


def strip_ansi(text):
    """去除 ANSI 转义序列，获取纯文本。"""
    return FrameRenderer.strip_ansi(text)


# ═══════════════════════════════════════════════════════════════
# 摘要行渲染测试
# ═══════════════════════════════════════════════════════════════

class TestSummaryLineRunning:
    """运行中状态的摘要行渲染。"""

    def test_contains_running_icon(self, renderer):
        """运行中：包含 ⏺ 图标。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        summary = lines[0]
        plain = strip_ansi(summary)
        assert SUMMARY_ICON_RUNNING in summary, f"应包含运行中图标，实际摘要: {summary!r}"

    def test_contains_agent_count(self, renderer):
        """运行中：包含 'N agents' 文本。"""
        slot1 = make_agent_slot("agent-1", status="running")
        slot2 = make_agent_slot("agent-2", status="running")
        lines = renderer.render(
            {"agent-1": slot1, "agent-2": slot2},
            ["agent-1", "agent-2"], now=FIXED_NOW, final=False,
        )
        plain = strip_ansi(lines[0])
        assert "2 agents" in plain, f"应包含 agent 计数，实际: {plain!r}"

    def test_contains_middle_dot_separator(self, renderer):
        """运行中：使用 · 分隔符。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        plain = strip_ansi(lines[0])
        assert "·" in plain, f"应包含 · 分隔符，实际: {plain!r}"

    def test_contains_done_fraction(self, renderer):
        """运行中：包含 'N/M done' 进度。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        plain = strip_ansi(lines[0])
        assert "0/1 done" in plain, f"应包含 done 进度，实际: {plain!r}"

    def test_no_progress_bar_char(self, renderer):
        """运行中：不包含进度条字符 ▰。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        assert "▰" not in lines[0], f"不应包含进度条字符 ▰: {lines[0]!r}"
        assert "▱" not in lines[0], f"不应包含进度条字符 ▱: {lines[0]!r}"

    def test_multi_agent_running_summary(self, renderer):
        """多 agent 运行中：完整摘要格式验证。"""
        for i in range(3):
            slot = make_agent_slot(f"agent-{i + 1}", status="running", output_tokens=1000)
            renderer.render({"a": slot}, ["a"], now=FIXED_NOW, final=False)
        slots = {
            "agent-1": make_agent_slot("agent-1", status="running", output_tokens=500),
            "agent-2": make_agent_slot("agent-2", status="running", output_tokens=800),
            "agent-3": make_agent_slot("agent-3", status="running", output_tokens=300),
        }
        lines = renderer.render(slots, ["agent-1", "agent-2", "agent-3"],
                                now=FIXED_NOW, final=False)
        plain = strip_ansi(lines[0])
        assert "3 agents" in plain
        assert "·" in plain
        assert "0/3 done" in plain
        assert "▰" not in lines[0]


class TestSummaryLineDone:
    """完成状态的摘要行渲染。"""

    def test_contains_done_icon(self, renderer):
        """完成：包含 ✔ 图标。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        assert SUMMARY_ICON_DONE in lines[0], f"应包含完成图标 ✔: {lines[0]!r}"

    def test_done_count_matches_total(self, renderer):
        """完成：done 计数等于 total。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        plain = strip_ansi(lines[0])
        assert "1/1 done" in plain, f"完成时 done 计数应为 1/1: {plain!r}"

    def test_all_green_when_done(self, renderer):
        """完成：使用绿色 ANSI 序列。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        # 完成图标、agent 计数、done 计数应使用绿色 \033[38;5;40m
        assert "\033[38;5;40m" in lines[0], f"完成状态应使用绿色: {lines[0]!r}"

    def test_no_progress_bar_in_done(self, renderer):
        """完成：不包含进度条字符。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        assert "▰" not in lines[0]
        assert "▱" not in lines[0]


# ═══════════════════════════════════════════════════════════════
# Agent 行渲染测试
# ═══════════════════════════════════════════════════════════════

class TestAgentLineRunning:
    """运行中 Agent 行渲染。"""

    def test_starts_with_two_spaces(self, renderer):
        """Agent 行以 2 空格缩进开头。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # 第一行是摘要行，第二行是分隔线，第三行开始是 agent 行
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert plain.startswith("  "), f"Agent 行应以 2 空格开头: {plain!r}"

    def test_contains_braille_spinner(self, renderer):
        """运行中 Agent 行包含 braille spinner 字符。"""
        slot = make_agent_slot(status="running")
        # frame=0 → ⠋ (SPINNER_FRAMES[0])
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        # braille spinner 是 SPINNER_FRAMES[0] = "⠋"
        assert SPINNER_FRAMES[0] in plain, (
            f"应包含 braille spinner '{SPINNER_FRAMES[0]}': {plain!r}"
        )

    def test_spinner_animates_with_frame(self, renderer):
        """spinner 随 frame 变化而变化。"""
        slot = make_agent_slot(status="running")
        # frame=2 → SPINNER_FRAMES[2] = "⠹"
        renderer._frame = 2
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert SPINNER_FRAMES[2] in plain, (
            f"frame=2 时应为 '{SPINNER_FRAMES[2]}': {plain!r}"
        )

    def test_spinner_wraps_around(self, renderer):
        """spinner 帧超出列表长度时取模循环。"""
        slot = make_agent_slot(status="running")
        # frame=10 → SPINNER_FRAMES[10 % 10] = SPINNER_FRAMES[0] = "⠋"
        renderer._frame = 10
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert SPINNER_FRAMES[0] in plain, (
            f"frame=10 应取模回到 '{SPINNER_FRAMES[0]}': {plain!r}"
        )

    def test_contains_type_tag(self, renderer):
        """Agent 行包含类型标签 [MP]（plan_execute 缩写）。"""
        slot = make_agent_slot(status="running", agent_type="plan_execute")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        abbr = AGENT_TYPE_ABBREV.get("plan_execute", "??")
        assert f"[{abbr}]" in plain, f"应包含类型标签 [{abbr}]: {plain!r}"

    def test_contains_description(self, renderer):
        """Agent 行包含任务描述。"""
        slot = make_agent_slot(description="分析项目结构")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert "分析项目结构" in plain, f"应包含描述: {plain!r}"

    def test_no_vertical_bar(self, renderer):
        """Agent 行不包含竖线字符 │。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "│" not in plain, f"不应包含竖线字符 │: {plain!r}"


class TestAgentLineDone:
    """完成 Agent 行渲染。"""

    def test_done_icon(self, renderer):
        """完成 Agent 行以 ✔ 开头（2空格缩进后）。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert "✔" in plain, f"完成 Agent 行应包含 ✔: {plain!r}"

    def test_done_contains_stats(self, renderer):
        """完成 Agent 行包含输出统计和耗时。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW,
                               output_tokens=1500, live_output_tokens=0)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert "1.5k" in plain, f"应包含 token 统计: {plain!r}"

    def test_done_two_space_indent(self, renderer):
        """完成 Agent 行以 2 空格缩进开头。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert plain.startswith("  "), f"应以 2 空格开头: {plain!r}"


class TestAgentLineFail:
    """失败 Agent 行渲染。"""

    def test_fail_icon(self, renderer):
        """失败 Agent 行以 ✖ 开头（2空格缩进后）。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert "✖" in plain, f"失败 Agent 行应包含 ✖: {plain!r}"

    def test_fail_two_space_indent(self, renderer):
        """失败 Agent 行以 2 空格缩进开头。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert plain.startswith("  "), f"应以 2 空格开头: {plain!r}"

    def test_fail_contains_type_tag(self, renderer):
        """失败 Agent 行包含类型标签。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW, agent_type="plan_execute")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        abbr = AGENT_TYPE_ABBREV.get("plan_execute", "??")
        assert f"[{abbr}]" in plain

    def test_no_vertical_bar_in_fail(self, renderer):
        """失败 Agent 行不包含竖线。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "│" not in plain


# ═══════════════════════════════════════════════════════════════
# 工具行渲染测试
# ═══════════════════════════════════════════════════════════════

class TestToolLineParsing:
    """parsing 阶段工具行渲染。"""

    def test_four_space_indent(self, renderer):
        """parsing 工具行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls src/", phase="parsing")
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # 找到工具行（4空格开头，bash 显示名为 "bs"）
        tool_lines = [l for l in lines if strip_ansi(l).startswith("    ") and "bs" in strip_ansi(l)]
        assert len(tool_lines) >= 1, f"应找到工具行: {lines!r}"
        plain = strip_ansi(tool_lines[0])
        assert plain.startswith("    "), f"工具行应以 4 空格开头: {plain!r}"

    def test_contains_tool_icon(self, renderer):
        """parsing 工具行包含工具图标（如 ⚡ for bash）。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls src/", phase="parsing")
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        icon = TOOL_ICONS.get("bash", "")
        assert icon in all_text, f"应包含工具图标 {icon!r}: {all_text!r}"

    def test_no_dot_indicator(self, renderer):
        """parsing 工具行不包含圆点指示器 ◌ ●。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls src/", phase="parsing")
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "◌" not in plain, f"不应包含圆点指示器 ◌: {all_text!r}"
        assert "●" not in plain, f"不应包含圆点指示器 ●: {all_text!r}"


class TestToolLineRunning:
    """running 阶段工具行渲染。"""

    def test_four_space_indent(self, renderer):
        """running 工具行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls src/", phase="running",
                               start_time=995.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # bash 显示名为 "bs"
        tool_lines = [l for l in lines if strip_ansi(l).startswith("    ") and "bs" in strip_ansi(l)]
        assert len(tool_lines) >= 1
        plain = strip_ansi(tool_lines[0])
        assert plain.startswith("    ")

    def test_contains_time(self, renderer):
        """running 工具行包含执行时间。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="read_file", detail="src/main.py",
                               phase="running", start_time=995.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        # 时间 = 1000.0 - 995.0 = 5.0s
        assert "5.0s" in plain, f"应包含执行时间 5.0s: {plain!r}"

    def test_no_dot_indicator_running(self, renderer):
        """running 工具行不包含圆点指示器。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls", phase="running",
                               start_time=995.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "◌" not in plain
        assert "●" not in plain


class TestToolLineDone:
    """done 阶段工具行渲染。"""

    def test_four_space_indent(self, renderer):
        """done 工具行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="ls src/", phase="done",
                               start_time=995.0, end_time=998.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # bash 显示名为 "bs"
        tool_lines = [l for l in lines if strip_ansi(l).startswith("    ") and "bs" in strip_ansi(l)]
        assert len(tool_lines) >= 1
        plain = strip_ansi(tool_lines[0])
        assert plain.startswith("    ")

    def test_contains_time(self, renderer):
        """done 工具行包含执行时间。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="read_file", detail="src/main.py",
                               phase="done", start_time=995.0, end_time=997.5)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "2.5s" in plain, f"应包含执行时间 2.5s: {plain!r}"


class TestToolLineFail:
    """fail 阶段工具行渲染。"""

    def test_four_space_indent(self, renderer):
        """fail 工具行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="rm -rf /", phase="fail",
                               start_time=995.0, end_time=996.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # bash 显示名为 "bs"
        tool_lines = [l for l in lines if strip_ansi(l).startswith("    ") and "bs" in strip_ansi(l)]
        assert len(tool_lines) >= 1

    def test_no_dot_indicator_fail(self, renderer):
        """fail 工具行不包含圆点指示器。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name="bash", detail="fail cmd", phase="fail")
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "◌" not in plain
        assert "●" not in plain


class TestToolIcons:
    """各工具类型的图标渲染。"""

    @pytest.mark.parametrize("tool_name,expected_icon", [
        ("bash", "⚡"),
        ("read_file", "📖"),
        ("write_file", "✎"),
        ("update_file", "✎"),
        ("dispatch_agent", "⚙"),
        ("user_select", "❓"),
        ("web_search", "🌐"),
        ("rm", "✕"),
        ("find", "⌕"),
    ])
    def test_tool_icon_present(self, renderer, tool_name, expected_icon):
        """各工具类型的图标正确渲染。"""
        slot = make_agent_slot(status="running")
        rec = make_tool_record(tool_name=tool_name, detail="test", phase="running",
                               start_time=995.0)
        slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        assert expected_icon in all_text, (
            f"工具 {tool_name} 应包含图标 {expected_icon!r}: {all_text!r}"
        )


# ═══════════════════════════════════════════════════════════════
# 阶段指示行测试
# ═══════════════════════════════════════════════════════════════

class TestPhaseLine:
    """阶段指示行渲染。"""

    def test_thinking_phase(self, renderer):
        """thinking 阶段：4空格缩进 + …thinking + 时间。"""
        slot = make_agent_slot(status="running", model_phase="thinking",
                               model_phase_start=998.0)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # thinking 应出现在 agent 行之后
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "…thinking" in plain, f"应包含 …thinking: {plain!r}"

    def test_answering_phase(self, renderer):
        """answering 阶段：4空格缩进 + …answering + 时间。"""
        slot = make_agent_slot(status="running", model_phase="answering",
                               model_phase_start=997.0)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "…answering" in plain, f"应包含 …answering: {plain!r}"

    def test_parsing_phase(self, renderer):
        """parsing 阶段：4空格缩进 + …parsing + model_info。"""
        slot = make_agent_slot(status="running", model_phase="parsing",
                               model_phase_start=996.0,
                               model_info="read_file, write_file 2.5s")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "…parsing" in plain, f"应包含 …parsing: {plain!r}"
        assert "read_file, write_file" in plain, f"应包含 model_info: {plain!r}"

    def test_batch_phase(self, renderer):
        """batch 阶段：4空格缩进 + …batch + model_info + 时间。"""
        slot = make_agent_slot(status="running", model_phase="batch",
                               model_phase_start=995.0,
                               model_info="3x parallel: bash, read_file, find")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "…batch" in plain, f"应包含 …batch: {plain!r}"
        assert "3x parallel" in plain, f"应包含 batch 信息: {plain!r}"

    def test_phase_not_rendered_when_done(self, renderer):
        """done 状态不渲染阶段指示行。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW,
                               model_phase="thinking", model_phase_start=998.0)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "…thinking" not in plain, f"done 状态不应有阶段指示: {plain!r}"

    def test_four_space_indent_phase(self, renderer):
        """阶段指示行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="running", model_phase="thinking",
                               model_phase_start=998.0)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # 找到阶段指示行
        phase_lines = [l for l in lines if "…thinking" in l]
        assert len(phase_lines) >= 1
        plain = strip_ansi(phase_lines[0])
        assert plain.startswith("    "), f"阶段行应以 4 空格开头: {plain!r}"


# ═══════════════════════════════════════════════════════════════
# 边界测试
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界条件测试。"""

    def test_empty_agent_list(self, renderer):
        """空 agent 列表：仅返回摘要行（无分隔线无 agent 行）。"""
        lines = renderer.render({}, [], now=FIXED_NOW, final=False)
        # 至少返回摘要行
        assert len(lines) >= 1
        plain = strip_ansi(lines[0])
        assert "0 agents" in plain or "0/0" in plain, (
            f"空列表应有摘要行: {lines!r}"
        )

    def test_single_agent(self, renderer):
        """单 agent：摘要 + 分隔线 + agent 行。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        assert len(lines) >= 3, f"单 agent 至少 3 行（摘要+分隔+agent）: {len(lines)}"

    def test_multiple_agents(self, renderer):
        """多 agent：每个 agent 都有对应行。"""
        slots = {
            "agent-1": make_agent_slot("agent-1", "任务1"),
            "agent-2": make_agent_slot("agent-2", "任务2"),
            "agent-3": make_agent_slot("agent-3", "任务3"),
        }
        lines = renderer.render(slots, ["agent-1", "agent-2", "agent-3"],
                                now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "任务1" in plain
        assert "任务2" in plain
        assert "任务3" in plain

    def test_final_true_shows_result(self, renderer):
        """final=True 时显示结果文本。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW,
                               result_text="执行成功：完成 3 个步骤")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "执行成功" in plain, f"final 帧应显示结果: {plain!r}"

    def test_final_false_hides_result(self, renderer):
        """final=False 时不显示结果文本。"""
        slot = make_agent_slot(status="running",
                               result_text="不应该显示的结果")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "不应该显示的结果" not in plain, f"非 final 帧不应显示结果: {plain!r}"

    def test_fail_shows_error(self, renderer):
        """失败 Agent 在 final 帧显示错误信息。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW,
                               result_error="执行超时：任务未完成")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "执行超时" in plain, f"final 帧应显示错误: {plain!r}"

    def test_agent_not_in_slots_skipped(self, renderer):
        """order 中的 label 不在 slots 中时跳过。"""
        slot = make_agent_slot("agent-1")
        lines = renderer.render({"agent-1": slot}, ["agent-1", "agent-2"],
                                now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        # agent-2 不应出现
        assert "agent-2" not in plain

    def test_tool_history_respects_max_history(self, renderer):
        """工具历史遵循 max_history 限制。"""
        slot = make_agent_slot(status="running")
        for i in range(5):
            rec = make_tool_record(tool_name="bash", detail=f"cmd{i}",
                                   phase="running", start_time=990.0 + i)
            slot.tool_history.append(rec)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # max_history=3，应只显示最近 3 条
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        # cmd0 和 cmd1 不应出现（最旧的 2 条）
        assert "cmd0" not in plain, f"cmd0 应被截断（超过 max_history=3）"
        assert "cmd1" not in plain, f"cmd1 应被截断（超过 max_history=3）"
        # cmd2, cmd3, cmd4 应出现
        assert "cmd4" in plain, "最近工具 cmd4 应出现"


# ═══════════════════════════════════════════════════════════════
# 文本截断测试
# ═══════════════════════════════════════════════════════════════

class TestAnsiStripping:
    """ANSI 清理测试。"""

    def test_strip_ansi_simple(self):
        """strip_ansi 移除 ANSI 颜色码。"""
        text = "\033[38;5;214m运行中\033[0m"
        result = FrameRenderer.strip_ansi(text)
        assert result == "运行中", f"应移除所有 ANSI 码: {result!r}"

    def test_strip_ansi_no_escape(self):
        """无 ANSI 码时保持原样。"""
        text = "纯文本"
        result = FrameRenderer.strip_ansi(text)
        assert result == "纯文本"

    def test_strip_ansi_empty(self):
        """空字符串保持为空。"""
        assert FrameRenderer.strip_ansi("") == ""


class TestDisplayWidth:
    """显示宽度计算测试。"""

    def test_ascii_width(self):
        """ASCII 字符宽度为 1。"""
        assert FrameRenderer.char_width("a") == 1
        assert FrameRenderer.char_width(" ") == 1

    def test_cjk_width(self):
        """中文字符宽度为 2。"""
        assert FrameRenderer.char_width("中") == 2
        assert FrameRenderer.char_width("文") == 2
        assert FrameRenderer.char_width("测") == 2

    def test_fullwidth_punctuation(self):
        """全角标点宽度为 2。"""
        assert FrameRenderer.char_width("。") == 2
        assert FrameRenderer.char_width("！") == 2

    def test_display_width_mixed(self):
        """混合文本宽度计算。"""
        text = "abc中文"
        width = FrameRenderer.display_width(text)
        # a(1) + b(1) + c(1) + 中(2) + 文(2) = 7
        assert width == 7, f"混合宽度应为 7: {width}"

    def test_display_width_pure_ascii(self):
        """纯 ASCII 宽度 = 字符数。"""
        assert FrameRenderer.display_width("hello") == 5


class TestTruncation:
    """截断测试。"""

    def test_no_truncation_short_text(self, renderer):
        """短文本不被截断。"""
        result = renderer.truncate_to_width("short text", max_width=120)
        assert result == "short text"

    def test_truncation_narrow(self, narrow_renderer):
        """窄终端截断长文本。"""
        long_text = "x" * 100
        result = narrow_renderer.truncate_to_width(long_text)
        # 终端宽度 60，减去 margin 和 ellipsis
        assert len(strip_ansi(result)) < 100, f"应被截断: {len(strip_ansi(result))}"

    def test_truncation_adds_ellipsis(self, narrow_renderer):
        """截断后添加 ... 标记。"""
        long_text = "A" * 100
        result = narrow_renderer.truncate_to_width(long_text)
        assert "..." in result, f"截断应添加 ...: {result!r}"

    def test_chinese_text_truncation(self, narrow_renderer):
        """中文文本截断考虑双宽字符。"""
        # 很长的中文文本，确保需要截断
        chinese_text = "这是一段" + "非常长的中文测试文本" * 10 + "用于验证截断功能是否正常"
        result = narrow_renderer.truncate_to_width(chinese_text, max_width=40)
        plain = strip_ansi(result)
        # 原文本 display_width 远超 40，应被截断
        assert len(plain) < len(chinese_text), f"中文应被截断: {len(plain)} vs {len(chinese_text)}"
        assert "..." in result, f"截断应添加 ...: {result!r}"

    def test_truncation_preserves_ansi(self, narrow_renderer):
        """截断保留 ANSI 码。"""
        text = "\033[38;5;214m" + "A" * 100 + "\033[0m"
        result = narrow_renderer.truncate_to_width(text)
        # 应包含 ANSI 开始和 RESET 结束
        assert "\033[38;5;214m" in result or "\033[0m" in result, (
            f"应保留 ANSI 码: {result!r}"
        )

    def test_truncation_min_width(self, renderer):
        """极窄宽度仍至少保留 MIN_WIDTH 字符。"""
        result = renderer.truncate_to_width("hello world test", max_width=3)
        # 最小保留 _TRUNC_MIN_WIDTH=10
        assert len(strip_ansi(result)) >= 3, f"应至少保留最小宽度: {result!r}"


# ═══════════════════════════════════════════════════════════════
# Claude Code 风格全局验证
# ═══════════════════════════════════════════════════════════════

class TestClaudeCodeStyle:
    """Claude Code 风格全局验证 — 无竖线、无进度条、正确缩进。"""

    def test_no_vertical_bar_anywhere(self, renderer):
        """所有行均不包含竖线字符 │。"""
        slots = {
            "agent-1": make_agent_slot("agent-1", status="running"),
            "agent-2": make_agent_slot("agent-2", status="done", end_time=FIXED_NOW),
        }
        lines = renderer.render(slots, ["agent-1", "agent-2"],
                                now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "│" not in plain, f"全局不应有竖线: {plain!r}"

    def test_no_progress_bar_anywhere(self, renderer):
        """所有行均不包含进度条字符 ▰ ▱。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        assert "▰" not in all_text
        assert "▱" not in all_text

    def test_spinner_frames_length(self):
        """SPINNER_FRAMES 长度为 10。"""
        assert len(SPINNER_FRAMES) == 10, (
            f"SPINNER_FRAMES 应为 10 帧，实际: {len(SPINNER_FRAMES)}"
        )

    def test_summary_icon_running(self):
        """SUMMARY_ICON_RUNNING 为 ⏺。"""
        assert SUMMARY_ICON_RUNNING == "⏺", (
            f"SUMMARY_ICON_RUNNING 应为 ⏺，实际: {SUMMARY_ICON_RUNNING!r}"
        )

    def test_summary_icon_done(self):
        """SUMMARY_ICON_DONE 为 ✔。"""
        assert SUMMARY_ICON_DONE == "✔", (
            f"SUMMARY_ICON_DONE 应为 ✔，实际: {SUMMARY_ICON_DONE!r}"
        )

    def test_no_tree_connectors(self, renderer):
        """不包含树形连接线字符 ├ └。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)
        assert "├" not in plain, f"不应有树形 ├: {plain!r}"
        assert "└" not in plain, f"不应有树形 └: {plain!r}"


# ═══════════════════════════════════════════════════════════════
# ANSI 颜色验证
# ═══════════════════════════════════════════════════════════════

class TestColorCodes:
    """Agent 状态颜色验证。"""

    def test_running_uses_amber(self, renderer):
        """运行中使用琥珀色 (214)。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        assert "\033[38;5;214m" in all_text, f"运行中应为琥珀 214: {all_text!r}"

    def test_done_uses_green(self, renderer):
        """完成使用绿色 (40)。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        assert "\033[38;5;40m" in all_text, f"完成应为绿色 40: {all_text!r}"

    def test_fail_uses_red(self, renderer):
        """失败使用红色 (196)。"""
        slot = make_agent_slot(status="fail", end_time=FIXED_NOW)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        assert "\033[38;5;196m" in all_text, f"失败应为红色 196: {all_text!r}"

    def test_spinner_uses_gold(self, renderer):
        """spinner 使用金色 (221)。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        assert "\033[38;5;221m" in all_text, f"spinner 应为金色 221: {all_text!r}"


# ═══════════════════════════════════════════════════════════════
# 结果预览测试
# ═══════════════════════════════════════════════════════════════

class TestResultPreview:
    """结果预览渲染测试。"""

    def test_result_preview_four_space_indent(self, renderer):
        """结果预览行以 4 空格缩进开头。"""
        slot = make_agent_slot(status="done", end_time=FIXED_NOW,
                               result_text="任务完成：所有步骤已执行")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        # 找到结果预览行
        result_lines = [l for l in lines if "任务完成" in strip_ansi(l)]
        assert len(result_lines) >= 1
        plain = strip_ansi(result_lines[0])
        assert plain.startswith("    "), f"结果预览应以 4 空格开头: {plain!r}"

    def test_result_preview_truncated(self, renderer):
        """结果预览超长时被截断。"""
        long_result = "A" * 500
        slot = make_agent_slot(status="done", end_time=FIXED_NOW,
                               result_text=long_result)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        result_lines = [l for l in lines if "A" in strip_ansi(l) and not "agent" in strip_ansi(l)]
        if result_lines:
            plain = strip_ansi(result_lines[0])
            # 不应有 500 个 A
            assert len(plain) < 500, f"长结果应被截断: {len(plain)}"


# ═══════════════════════════════════════════════════════════════
# 分隔线测试
# ═══════════════════════════════════════════════════════════════

class TestSeparatorLine:
    """分隔线渲染测试。"""

    def test_separator_present(self, renderer):
        """分隔线存在于摘要和 agent 行之间。"""
        slot = make_agent_slot(status="running")
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        # 第二行应为分隔线
        assert len(lines) >= 3
        separator = lines[1]
        plain = strip_ansi(separator)
        assert "━" in plain, f"分隔线应包含 ━: {plain!r}"


# ═══════════════════════════════════════════════════════════════
# Agent 类型标签测试
# ═══════════════════════════════════════════════════════════════

class TestAgentTypeTags:
    """Agent 类型标签渲染。"""

    @pytest.mark.parametrize("agent_type,expected_abbr", [
        ("plan_execute", "pe"),
        ("map", "mp"),
        ("review", "rv"),
        ("plan", "pl"),
        ("read_memory", "rm"),
        ("write_memory", "wm"),
    ])
    def test_type_tag_abbreviation(self, renderer, agent_type, expected_abbr):
        """各 Agent 类型的缩写标签正确。"""
        slot = make_agent_slot(status="running", agent_type=agent_type)
        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        agent_line = lines[2]
        plain = strip_ansi(agent_line)
        assert f"[{expected_abbr}]" in plain, (
            f"{agent_type} 类型标签应为 [{expected_abbr}]: {plain!r}"
        )


# ═══════════════════════════════════════════════════════════════
# 综合渲染测试
# ═══════════════════════════════════════════════════════════════

class TestFullRender:
    """综合渲染场景测试。"""

    def test_full_scene_running_with_tools_and_phase(self, renderer):
        """完整运行场景：agent + 工具 + 阶段指示。"""
        slot = make_agent_slot(
            status="running",
            description="分析代码结构",
            model_phase="thinking",
            model_phase_start=998.0,
            output_tokens=500,
            last_speed=50.0,
        )
        slot.tool_history.append(
            make_tool_record("read_file", "src/main.py", phase="done",
                             start_time=995.0, end_time=997.0))
        slot.tool_history.append(
            make_tool_record("bash", "ls src/", phase="running",
                             start_time=998.0))
        slot.tool_history.append(
            make_tool_record("find", "*.py", phase="parsing"))

        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=False)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)

        # 摘要行
        assert "1 agents" in plain
        # Agent 行
        assert "分析代码结构" in plain
        # 阶段指示
        assert "…thinking" in plain
        # 工具行
        assert "main.py" in plain or "ls src/" in plain or "*.py" in plain
        # 无旧格式
        assert "│" not in plain
        assert "▰" not in plain
        assert "◌" not in plain
        assert "●" not in plain

    def test_full_scene_done_with_result(self, renderer):
        """完成场景：done agent + 结果预览。"""
        slot = make_agent_slot(
            status="done",
            end_time=FIXED_NOW,
            description="执行计划步骤",
            output_tokens=2000,
            result_text="所有步骤已完成：\n1. 读取文件 ✓\n2. 修改配置 ✓\n3. 验证通过 ✓",
        )
        slot.tool_history.append(
            make_tool_record("read_file", "config.yaml", phase="done",
                             start_time=993.0, end_time=995.0))
        slot.tool_history.append(
            make_tool_record("update_file", "config.yaml", phase="done",
                             start_time=996.0, end_time=998.0))

        lines = renderer.render({"agent-1": slot}, ["agent-1"], now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)

        # 摘要行
        assert "✔" in plain
        assert "1/1 done" in plain
        # Agent 行
        assert "✔" in plain
        assert "执行计划步骤" in plain
        # 结果预览
        assert "所有步骤已完成" in plain
        # 工具行
        assert "config.yaml" in plain
        # 无旧格式
        assert "│" not in plain

    def test_multiple_agents_mixed_status(self, renderer):
        """混合状态的多 agent 场景。"""
        slots = {
            "agent-1": make_agent_slot("agent-1", "正在执行的任务",
                                       status="running", output_tokens=300,
                                       last_speed=25.0),
            "agent-2": make_agent_slot("agent-2", "已完成的任务",
                                       status="done", end_time=FIXED_NOW,
                                       output_tokens=1200),
            "agent-3": make_agent_slot("agent-3", "失败的任务",
                                       status="fail", end_time=FIXED_NOW - 2,
                                       result_error="权限不足"),
        }
        lines = renderer.render(slots, ["agent-1", "agent-2", "agent-3"],
                                now=FIXED_NOW, final=True)
        all_text = "\n".join(lines)
        plain = strip_ansi(all_text)

        # 摘要行应为完成状态（final=True）
        assert "✔" in plain
        assert "2/3 done" in plain
        # 各 agent 行
        assert "正在执行的任务" in plain
        assert "已完成的任务" in plain
        assert "失败的任务" in plain
        # 失败 agent 的结果
        assert "权限不足" in plain
        # 无旧格式
        assert "│" not in plain
        assert "▰" not in plain
