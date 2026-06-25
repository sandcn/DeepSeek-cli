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

from ...core.constants import RESET as _C_RESET
from ..parallel._config import (
    SUMMARY_SEPARATOR as _DEFAULT_SUMMARY_SEPARATOR,
    SUMMARY_ICON_RUNNING as _DEFAULT_SUMMARY_ICON_RUNNING,
    SUMMARY_ICON_DONE as _DEFAULT_SUMMARY_ICON_DONE,
    SPINNER_FRAMES as _DEFAULT_SPINNER_FRAMES,
)
from ..parallel._text_formatter import TextFormatter as _DefaultTextFormatter
from ..parallel._tool_icons import (
    TOOL_ICONS as _DEFAULT_TOOL_ICONS,
    AGENT_TYPE_ABBREV,
    AGENT_TYPE_COLORS,
    get_tool_color,
)
from ..state.agent_state import AgentSlot, ToolRecord
from ...tools.registry import get_tool_display_name

# ── 常量 ────────────────────────────────────────────────

_ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_TRUNC_MARGIN = 2
_TRUNC_ELLIPSIS_SPACE = 3
_TRUNC_MIN_WIDTH = 10

# ── 256色 ANSI 主题色（subagent 面板专用） ──────────────
# 颜色编号选自 xterm-256color 调色板，所有终端均兼容
_C_RUNNING   = "\033[38;5;214m"  # 琥珀色 — 运行中
_C_DONE      = "\033[38;5;40m"   # 亮绿 — 完成
_C_FAIL      = "\033[38;5;196m"  # 红 — 失败
_C_ANSWERING = "\033[38;5;75m"   # 浅蓝 — 回答中
_C_PARSING   = "\033[38;5;178m"  # 金色 — 解析工具调用
_C_BATCH     = "\033[38;5;140m"  # 淡紫 — 批量工具调用
_C_DIMMER    = "\033[38;5;240m"  # 暗灰 — 辅助信息
_C_DIMMEST   = "\033[38;5;238m"  # 更深灰 — 分隔线/边框
_C_SUMMARY_DIM = "\033[38;5;245m"  # 中灰 — 摘要行次要信息
_C_SPINNER   = "\033[38;5;221m"  # 金色 — spinner 动画

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
        self._spinner_frames = spinner_frames or _DEFAULT_SPINNER_FRAMES

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
        from ..ansi import strip_ansi as _strip_ansi
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
        for idx, label in enumerate(order):
            slot = slots_snapshot.get(label)
            if slot is None:
                continue
            lines.extend(self._build_agent_lines(slot, now, final))

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
        """构建摘要行 — Claude Code 极简格式。

        从 render() 中提取，负责速度计算和摘要文本组装。
        """
        if done_count == total_agents:
            speed_value = 0.0
        elif has_running:
            speed_value = latest_speed
        else:
            speed_value = 0.0
        speed_str = self._text_formatter.format_compact_speed(speed_value)

        if done_count < total_agents:
            # ── 运行中 ──
            icon = f"{_C_RUNNING}{self._summary_icon_running}{_C_RESET}"
            summary = (
                f"{icon} {_C_SUMMARY_DIM}{total_agents} agents{_C_RESET}"
                f" {self._summary_separator} {_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f" {self._summary_separator} {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f" {self._summary_separator} {_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f" {self._summary_separator} {_C_RUNNING}{done_count}/{total_agents} done{_C_RESET}"
            )
        else:
            # ── 完成 ──
            icon = f"{_C_DONE}{self._summary_icon_done}{_C_RESET}"
            summary = (
                f"{icon} {_C_DONE}{total_agents} agents{_C_RESET}"
                f" {self._summary_separator} {_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f" {self._summary_separator} {_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f" {self._summary_separator} {_C_DONE}{done_count}/{total_agents} done{_C_RESET}"
            )
        return self.truncate_to_width(summary)

    def _build_phase_line(
        self,
        slot: AgentSlot,
        now: float,
        final: bool,
    ) -> List[str]:
        """构建阶段指示行 — 彩色分类。返回行列表（可能为空）。"""
        phase_lines: list[str] = []
        if slot.status == "running" and slot.model_phase and not final:
            phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
            phase_time = f"{phase_elapsed:.1f}s"
            if slot.model_phase == "thinking":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}    …thinking  {phase_time}{_C_RESET}"))
            elif slot.model_phase == "answering":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}    {_C_ANSWERING}…answering{_C_DIMMER}  {phase_time}{_C_RESET}"))
            elif slot.model_phase == "parsing":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}    {_C_PARSING}…parsing{_C_DIMMER}  {slot.model_info}{_C_RESET}"))
            elif slot.model_phase == "batch":
                phase_lines.append(self.truncate_to_width(
                    f"{_C_DIMMER}    {_C_BATCH}…batch{_C_DIMMER}  {slot.model_info}  {phase_time}{_C_RESET}"))
        return phase_lines

    def _build_agent_lines(
        self,
        slot: AgentSlot,
        now: float,
        final: bool = False,
    ) -> List[str]:
        """构建单个 Agent 的增强显示行。

        使用 256 色 + braille spinner + 彩色类型标签（Claude Code 极简风格）。
        """
        lines: list[str] = []

        elapsed = (slot.end_time or now) - slot.start_time
        elapsed_str = self._text_formatter.format_duration(elapsed)
        # 与 _compute_summary 一致：使用加法汇总 output + live_output
        display_out = slot.output_tokens + slot.live_output_tokens
        output_str = self._text_formatter.format_token_count(display_out)
        speed_value = slot.last_speed if slot.status == "running" else 0.0
        speed_str = self._text_formatter.format_compact_speed(speed_value)

        # ── 类型标签（256 色背景） ──
        abbr = AGENT_TYPE_ABBREV.get(slot.agent_type, "??")
        type_color = AGENT_TYPE_COLORS.get(slot.agent_type, _C_DIMMER)
        type_tag = f"{type_color}[{abbr}]{_C_RESET}"

        # ── 标题行 ──
        if slot.status == "done":
            icon = f"{_C_DONE}✔{_C_RESET}"
            suffix = f"  {_C_DIMMER}{output_str}{_C_RESET}  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"  {icon} {type_tag} {slot.description}{suffix}"
        elif slot.status == "fail":
            icon = f"{_C_FAIL}✖{_C_RESET}"
            suffix = f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            title = f"  {icon} {type_tag} {slot.description}{suffix}"
        else:
            # 运行中 — braille spinner 动画（10 帧循环）
            if not final:
                spinner_idx = self._frame % len(self._spinner_frames)
                spinner_char = self._spinner_frames[spinner_idx]
                dot = f"{_C_SPINNER}{spinner_char}{_C_RESET}"
            else:
                dot = f"{_C_DIMMER}●{_C_RESET}"
            suffix = (
                f"  {_C_DIMMER}{output_str}{_C_RESET}"
                f"  {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
            )
            title = f"  {dot} {type_tag} {slot.description}{suffix}"
        lines.append(self.truncate_to_width(title))

        # ── 阶段指示（彩色） ──
        lines.extend(self._build_phase_line(slot, now, final))

        # ── 工具历史（倒序：最近工具在最上面，最多显示最近 max_history 条） ──
        if final or slot.status not in ("done", "fail"):
            history = slot.tool_history[-self.max_history:]
            for rec in reversed(history):
                lines.append(self._format_tool_record(rec, now))

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
                        f"{_C_DIMMER}    {line}{_C_RESET}"))

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

    def _format_tool_record(self, rec: ToolRecord, now: float) -> str:
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
        prefix = "    "

        if rec.phase == "parsing":
            line = f"{prefix}{_C_PARSING}{tool_abbr}{detail_display}"
        elif rec.phase == "running":
            line = f"{prefix}{_C_RUNNING}{tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"
        elif rec.phase == "done":
            line = f"{prefix}{_C_DONE}{tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"
        else:
            line = f"{prefix}{_C_FAIL}{tool_abbr}{detail_display}  {_C_DIMMER}{time_str}{_C_RESET}"

        return self.truncate_to_width(line)
