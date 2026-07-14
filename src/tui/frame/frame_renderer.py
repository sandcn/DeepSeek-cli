"""
FrameRenderer — 纯函数渲染器

将 Agent 状态快照渲染为终端行列表。
不依赖任何 I/O，可独立测试。

职责：AgentSlot/ToolRecord → list[str]（终端行）
"""

from __future__ import annotations

import re
import unicodedata
from typing import Dict, List

from ...ui.colors import RESET as _C_RESET, gradient_range as _gradient_range
from ..parallel._config import (
    SUMMARY_SEPARATOR as _DEFAULT_SUMMARY_SEPARATOR,
    SUMMARY_ICON_RUNNING as _DEFAULT_SUMMARY_ICON_RUNNING,
    SUMMARY_ICON_DONE as _DEFAULT_SUMMARY_ICON_DONE,
    SPINNER_FRAMES as _DEFAULT_SPINNER_FRAMES,
    get_spinner_frames as _get_spinner_frames,
    DEFAULT_SPINNER_SPEED as _DEFAULT_SPINNER_SPEED,
)
from ..parallel._text_formatter import TextFormatter as _DefaultTextFormatter
from ..parallel._tool_icons import (
    TOOL_ICONS as _DEFAULT_TOOL_ICONS,
    AGENT_TYPE_ABBREV,
    AGENT_TYPE_COLORS,
    get_tool_color,
)
from ..state.agent_state import AgentSlot, ToolRecord
from ...core.constants import (
    RUNNING_256 as _C_RUNNING,
    DONE_256 as _C_DONE,
    FAIL_256 as _C_FAIL,
    ANSWERING_256 as _C_ANSWERING,
    PARSING_256 as _C_PARSING,
    BATCH_256 as _C_BATCH,
    DIMMER_256 as _C_DIMMER,
    DIMMEST_256 as _C_DIMMEST,
    SUMMARY_DIM_256 as _C_SUMMARY_DIM,
    BRANCH_256 as _C_BRANCH,
    SPINNER_COLOR_256 as _C_SPINNER,
)
from ...tools.registry import get_tool_display_name

from ..core.animator import AnimatorContext, BreathPalette
from ..core.effects import sine_color

# ── 常量 ────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_TRUNC_MARGIN = 2
_TRUNC_ELLIPSIS_SPACE = 3
_TRUNC_MIN_WIDTH = 10

# ── 进度条渐变 & 呼吸调色板（从 BreathPalette 注册表读取） ──
# 所有颜色数据已由 BreathPalette 管理，此处不再定义局部常量。

# ── 树形缩进常量 ────────────────────────────────────────
_INDENT = "  "  # 子行统一缩进（2 空格），用于 phase/tool/result 行

# ── 渲染器 ──────────────────────────────────────────────

class FrameRenderer:
    """渲染器：将 Agent 状态快照渲染为终端行列表。

    纯函数设计，不依赖 I/O 或可变实例状态，可独立测试。
    所有渲染配置通过构造函数注入。

    使用示例：
        renderer = FrameRenderer(terminal_width=120, frame=0, max_history=3)
        lines = renderer.render(slots_snapshot, order, now=time.time(), final=False)
    """

    def __init__(
        self,
        terminal_width: int,
        frame: int = 0,
        max_history: int = 3,
        *,
        summary_separator: str = _DEFAULT_SUMMARY_SEPARATOR,
        summary_icon_running: str = _DEFAULT_SUMMARY_ICON_RUNNING,
        summary_icon_done: str = _DEFAULT_SUMMARY_ICON_DONE,
        tool_icons: dict | None = None,
        text_formatter=None,
        spinner_name: str | None = None,
        spinner_speed: float | None = None,
        spinner_frames: list[str] | None = None,
    ):
        self._terminal_width = terminal_width
        self._frame = frame
        self.max_history = max_history
        self._summary_separator = summary_separator
        self._summary_icon_running = summary_icon_running
        self._summary_icon_done = summary_icon_done
        self._tool_icons = tool_icons or _DEFAULT_TOOL_ICONS
        self._text_formatter = text_formatter or _DefaultTextFormatter

        # Spinner 配置：spinner_name 优先，其次 spinner_frames，最后默认值
        if spinner_name is not None:
            frames, speed = _get_spinner_frames(spinner_name)
            self._spinner_frames = frames
            self._spinner_speed = speed
        else:
            self._spinner_frames = spinner_frames or _DEFAULT_SPINNER_FRAMES
            self._spinner_speed = spinner_speed or _DEFAULT_SPINNER_SPEED

    def sync_terminal_state(self, width: int, frame: int) -> None:
        """同步终端状态（由外部每帧渲染前调用）。

        Args:
            width: 终端宽度（列数）
            frame: 当前帧号（用于脉冲动画）
        """
        self._terminal_width = width
        self._frame = frame

    # ── 静态工具 ────────────────────────────────────────

    @staticmethod
    def strip_ansi(text: str) -> str:
        if '\x1b' not in text:
            return text
        from ...ui.ansi import strip_ansi as _strip_ansi
        return _strip_ansi(text)

    @staticmethod
    def char_width(ch: str) -> int:
        eaw = unicodedata.east_asian_width(ch)
        return 2 if eaw in ('W', 'F') else 1

    @staticmethod
    def display_width(text: str) -> int:
        return sum(FrameRenderer.char_width(ch) for ch in text)

    # ── 截断 ────────────────────────────────────────────

    def truncate_to_width(self, text: str, max_width: int | None = None) -> str:
        """将文本截断到指定宽度（含中文字符双宽处理）。"""
        if max_width is None:
            max_width = self._terminal_width
        max_width = max(max_width - _TRUNC_MARGIN, _TRUNC_MIN_WIDTH)

        plain = self.strip_ansi(text)
        if self.display_width(plain) <= max_width:
            return text

        visible_limit = max_width - _TRUNC_ELLIPSIS_SPACE
        if visible_limit < 1:
            visible_limit = max_width

        if '\x1b' not in text:
            truncated = text[:visible_limit]
        else:
            result: list[str] = []
            visible = 0
            pos = 0
            while pos < len(text):
                m = _ANSI_RE.match(text, pos)
                if m:
                    result.append(m.group())
                    pos = m.end()
                else:
                    w = self.char_width(text[pos])
                    if visible + w > visible_limit:
                        break
                    result.append(text[pos])
                    visible += w
                    pos += 1
            truncated = "".join(result) + _C_RESET

        if visible_limit < max_width:
            return truncated + "..."
        return truncated

    # ── 主渲染入口 ──────────────────────────────────────

    def render(
        self,
        slots_snapshot: Dict[str, AgentSlot],
        order: List[str],
        now: float,
        final: bool = False,
    ) -> List[str]:
        """渲染完整帧 → 行列表。

        Args:
            slots_snapshot: AgentStateStore.snapshot_all() 的返回值
            order: AgentStateStore.get_order() 的返回值
            now: 当前时间戳（time.time()），由调用方传入以确保可测试
            final: 是否结束帧（完成状态渲染）

        Returns:
            终端行列表（每行为已截断的字符串，含 ANSI 颜色码）
        """
        lines: list[str] = []

        # 计算汇总统计数据
        (
            total_agents, total_output, done_count,
            earliest_start, latest_speed, has_running,
        ) = self._compute_summary(slots_snapshot, order)

        overall_elapsed = (now - earliest_start) if earliest_start else 0
        elapsed_str = self._text_formatter.format_duration(overall_elapsed)
        output_str = self._text_formatter.format_token_count(total_output)

        lines.append(self._build_summary_line(
            total_agents, output_str, done_count,
            elapsed_str, latest_speed, has_running, final,
        ))

        # ── 分隔线 ──
        sep_line_width = min(self._terminal_width - 2, 40)
        # 双层分隔：粗线 + 细线，增加层次感
        sep_line = (
            _C_DIMMEST + " " + "━" * sep_line_width + _C_RESET
        )
        lines.append(self.truncate_to_width(sep_line))

        # ── 各 Agent 行 ──
        prev_agent_lines = 0
        for idx, label in enumerate(order):
            slot = slots_snapshot.get(label)
            if slot is None:
                continue
            is_last = (idx == len(order) - 1)
            # ★ Agent 间空白呼吸行（前一个 Agent 有子行时插入竖线延续）
            if idx > 0 and prev_agent_lines > 1:
                lines.append(f"{_C_BRANCH} │ {_C_RESET}")
            agent_lines = self._build_agent_lines(slot, now, final, is_last)
            lines.extend(agent_lines)
            prev_agent_lines = len(agent_lines)

        # 清理尾部空行
        while lines and lines[-1] == "":
            lines.pop()
        return lines

    # ── 内部辅助 ────────────────────────────────────────

    @staticmethod
    def _compute_summary(
        slots_snapshot: Dict[str, AgentSlot],
        order: List[str],
    ) -> tuple:
        """计算渲染摘要所需的统计数据。"""
        total_agents = len(order)
        total_output = 0
        done_count = 0
        earliest_start: float | None = None
        latest_speed = 0.0
        has_running = False

        for label in order:
            slot = slots_snapshot.get(label)
            if slot is None:
                continue
            display_output = slot.output_tokens + slot.live_output_tokens
            total_output += display_output
            if slot.status == "running":
                has_running = True
                if slot.last_speed > 0:
                    latest_speed += slot.last_speed  # 并行场景下总吞吐 = 各 agent 速度之和
            if slot.status in ("done", "fail"):
                done_count += 1
            if earliest_start is None or slot.start_time < earliest_start:
                earliest_start = slot.start_time

        return (total_agents, total_output, done_count,
                earliest_start, latest_speed, has_running)

    def _build_summary_line(
        self,
        total_agents: int,
        output_str: str,
        done_count: int,
        elapsed_str: str,
        latest_speed: float,
        has_running: bool,
        final: bool,
    ) -> str:
        """构建增强摘要行 — 彩色进度条 + 分层信息。

        从 render() 中提取，负责速度计算和摘要文本组装。
        """
        if done_count == total_agents:
            speed_value = 0.0
        elif has_running:
            speed_value = latest_speed
        else:
            speed_value = 0.0
        speed_str = self._text_formatter.format_compact_speed(speed_value)

        sep = f" {_C_DIMMER}·{_C_RESET} "

        if done_count < total_agents and not final:
            # ── 运行中：进度条 + 统计 ──
            bar_width = min(12, total_agents * 4)
            filled = int(bar_width * done_count / total_agents) if total_agents else 0
            if filled > 0:
                # 琥珀→绿渐变：每个 ▰ 使用不同色号，含呼吸颜色漂移
                _gradient = BreathPalette.get("progress_amber_green")
                _breath_offset = sine_color(self._frame, 0, len(_gradient) - 1, 8)
                _parts: list[str] = []
                for _i in range(filled):
                    _ci = (_i + _breath_offset) % len(_gradient)
                    _parts.append(f"\033[38;5;{_gradient[_ci]}m▰\033[0m")
                # 空位部分：深灰↔浅灰渐变，与填充色形成对比
                empty_count = bar_width - filled
                if empty_count > 0:
                    _empty_grad = _gradient_range(235, 245, max(empty_count, 2))
                    for _j in range(empty_count):
                        _ej = (_j + _breath_offset) % len(_empty_grad)
                        _parts.append(f"\033[38;5;{_empty_grad[_ej]}m▱\033[0m")
                bar = "".join(_parts) + _C_RESET
            else:
                # 全空：深灰→浅灰渐变呼吸
                _empty_grad = _gradient_range(235, 245, max(bar_width, 2))
                _breath_offset = sine_color(self._frame, 0, len(_empty_grad) - 1, 8)
                _empty_parts: list[str] = []
                for _j in range(bar_width):
                    _ej = (_j + _breath_offset) % len(_empty_grad)
                    _empty_parts.append(f"\033[38;5;{_empty_grad[_ej]}m▱\033[0m")
                bar = "".join(_empty_parts) + _C_RESET
            icon = f"{_C_RUNNING}{self._summary_icon_running}{_C_RESET}"
            summary = (
                f"{icon} {_C_SUMMARY_DIM}{total_agents} agents{_C_RESET}"
                f" {bar}"
                f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f"{sep}{_C_RUNNING}{done_count}/{total_agents} done{_C_RESET}"
            )
        else:
            # ── 完成：全绿进度条 ──
            bar_width = min(12, total_agents * 4)
            bar = _C_DONE + "▰" * bar_width + _C_RESET
            icon = f"{_C_DONE}{self._summary_icon_done}{_C_RESET}"
            summary = (
                f"{icon} {_C_DONE}{total_agents} agents{_C_RESET}"
                f" {bar}"
                f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f"{sep}{_C_DONE}{done_count}/{total_agents} done{_C_RESET}"
            )
        return self.truncate_to_width(summary)

    def _build_phase_line(
        self,
        slot: AgentSlot,
        now: float,
        cont: str,
        final: bool,
    ) -> List[str]:
        """构建阶段指示行 — 彩色分类。返回行列表（可能为空）。"""
        phase_lines: list[str] = []
        if slot.status == "running" and slot.model_phase and not final:
            phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
            phase_time = f"{phase_elapsed:.1f}s"
            if slot.model_phase == "thinking":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}{cont}{_INDENT}…thinking  {phase_time}{_C_RESET}"))
            elif slot.model_phase == "answering":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}{cont}{_INDENT}{_C_ANSWERING}…answering{_C_DIMMER}  {phase_time}{_C_RESET}"))
            elif slot.model_phase == "parsing":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}{cont}{_INDENT}{_C_PARSING}…parsing{_C_DIMMER}  {slot.model_info}{_C_RESET}"))
            elif slot.model_phase == "batch":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}{cont}{_INDENT}{_C_BATCH}…batch{_C_DIMMER}  {slot.model_info}  {phase_time}{_C_RESET}"))
        return phase_lines

    def _build_agent_lines(
        self,
        slot: AgentSlot,
        now: float,
        final: bool = False,
        is_last: bool = False,
    ) -> List[str]:
        """构建单个 Agent 的增强显示行。

        使用 256 色 + braille spinner + 树形连接线 + 彩色类型标签。
        """
        lines: list[str] = []
        # 树形连接线：末行用 └─，非末行用 ├─，延续用 │
        branch = " └─" if is_last else " ├─"
        cont   = "   " if is_last else " │ "

        elapsed = (slot.end_time or now) - slot.start_time
        elapsed_str = self._text_formatter.format_duration(elapsed)
        # 与 _compute_summary 一致：使用加法汇总 output + live_output
        display_out = slot.output_tokens + slot.live_output_tokens
        output_str = self._text_formatter.format_token_count(display_out)
        speed_value = slot.last_speed if slot.status == "running" else 0.0
        speed_str = self._text_formatter.format_compact_speed(speed_value)

        # ── 类型标签（256 色背景，运行中呼吸） ──
        abbr = AGENT_TYPE_ABBREV.get(slot.agent_type, "??")
        type_color = AGENT_TYPE_COLORS.get(slot.agent_type, _C_DIMMER)
        if slot.status == "running" and not final:
            # 运行中：微量色号偏移产生柔和呼吸（正弦波呼吸替代线性取模）
            breath_palette = BreathPalette.get("agent_breath")
            breath_idx = round(sine_color(self._frame, 0, len(breath_palette) - 1, 12)) if breath_palette else 0
            offset = BreathPalette.get("agent_breath")[breath_idx]
            # 从 ANSI 序列中提取色号并偏移
            # 格式 \033[38;5;Nm → 提取 N
            import re as _re_breath
            m = _re_breath.search(r'\033\[(\d+;)?38;5;(\d+)m', type_color)
            if m:
                base_color = int(m.group(2))
                new_color = max(0, min(255, base_color + offset))
                type_tag = f"\033[38;5;{new_color}m[{abbr}]{_C_RESET}"
            else:
                type_tag = f"{type_color}[{abbr}]{_C_RESET}"
        else:
            type_tag = f"{type_color}[{abbr}]{_C_RESET}"

        # ── 标题行 ──
        if slot.status == "done":
            icon = f"{_C_DONE}✔{_C_RESET}"
            suffix = f"  {_C_DIMMER}{output_str}{_C_RESET}  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {slot.description}{suffix}"
        elif slot.status == "fail":
            icon = f"{_C_FAIL}✖{_C_RESET}"
            suffix = f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {slot.description}{suffix}"
        else:
            # 运行中 — spinner 动画（按速度配置调整帧索引）
            if not final:
                speed_ratio = _DEFAULT_SPINNER_SPEED / self._spinner_speed
                spinner_idx = int(self._frame * speed_ratio) % len(self._spinner_frames)
                spinner_char = self._spinner_frames[spinner_idx]
                dot = f"{_C_SPINNER}{spinner_char}{_C_RESET}"
            else:
                dot = f"{_C_DIMMER}●{_C_RESET}"
            suffix = (
                f"  {_C_DIMMER}{output_str}{_C_RESET}"
                f"  {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            )
            title = f"{_C_BRANCH}{branch}{_C_RESET} {dot} {type_tag} {slot.description}{suffix}"
        lines.append(self.truncate_to_width(title))

        # ── 阶段指示（彩色） ──
        lines.extend(self._build_phase_line(slot, now, cont, final))

        # ── 工具历史（倒序：最近工具在最上面，最多显示最近 max_history 条） ──
        if final or slot.status not in ("done", "fail"):
            history = slot.tool_history[-self.max_history:]
            for rec in reversed(history):
                lines.append(self._format_tool_record(rec, now, cont))

        # ── 结果文本（仅 final 帧显示） ──
        if final:
            display_text = ""
            if slot.status == "fail" and slot.result_error:
                display_text = slot.result_error
            elif slot.status == "done" and slot.result_text:
                display_text = slot.result_text
            if display_text:
                result_preview = self._truncate_result(display_text)
                for line in result_preview:
                    lines.append(self.truncate_to_width(
                        f"{_C_DIMMER}{cont}{_INDENT}{line}{_C_RESET}"))

        return lines

    @staticmethod
    def _truncate_result(text: str, max_lines: int = 3, max_chars: int = 300) -> List[str]:
        """截断结果文本为有限行和字符数。"""
        text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
        if not text:
            return []
        lines = text.split('\n')
        result: List[str] = []
        total = 0
        for line in lines[:max_lines]:
            if total >= max_chars:
                break
            remaining = max_chars - total
            result.append(line[:remaining])
            total += len(line[:remaining])
        return result

    def _format_tool_record(self, rec: ToolRecord, now: float, cont: str = "   ") -> str:
        """格式化工具记录 — 彩色分类 + 图标。

        工具根据类别使用不同颜色（shell=绿/file_read=蓝/file_write=粉/search=金/agent=浅蓝/delete=橙红）。
        """
        elapsed = (rec.end_time or now) - rec.start_time if rec.start_time else 0
        time_str = f"{elapsed:.1f}s"

        detail = rec.detail
        if detail:
            detail = detail.replace('\r', '\\r').replace('\n', '\\n')

        tool_icon = self._tool_icons.get(rec.tool_name, "")
        display_name = get_tool_display_name(rec.tool_name)
        tool_color = get_tool_color(rec.tool_name)

        # 彩色工具名（图标 + 名称）
        if tool_icon:
            tool_abbr = f"{tool_icon} {tool_color}{display_name}{_C_RESET}"
        else:
            tool_abbr = f"{tool_color}{display_name}{_C_RESET}"

        detail_display = f" {_C_DIMMER}{detail}{_C_RESET}" if detail else ""
        prefix = f"{_C_BRANCH}{cont}{_C_RESET}{_INDENT}"

        if rec.phase == "parsing":
            line = f"{prefix}{_C_PARSING}◌{_C_RESET} {tool_abbr}{detail_display}"
        elif rec.phase == "running":
            # 运行中工具图标脉动（正弦波呼吸替代线性取模）
            tool_pulse = BreathPalette.get("tool_pulse")
            pulse_idx = round(sine_color(self._frame, 0, len(tool_pulse) - 1, 6)) if tool_pulse else 0
            pulse_color = BreathPalette.get("tool_pulse")[pulse_idx]
            line = f"{prefix}\033[38;5;{pulse_color}m●\033[0m {tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"
        elif rec.phase == "done":
            line = f"{prefix}{_C_DONE}✔{_C_RESET} {tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"
        else:
            line = f"{prefix}{_C_FAIL}✖{_C_RESET} {tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"

        return self.truncate_to_width(line)
