"""SubAgent 面板帧渲染（Layer 0 约束：仅依赖 _const/_config/_tool_icons/_format/app.* 叶模块与同级状态模块）。

方向C 步骤7：从 ``_subagent_panel.SubAgentPanelController`` 上帝类拆出的
帧渲染域——``render_frame`` / ``build_agent_lines`` / ``format_tool_record``
与动效辅助（``_fade_type_ansi`` / ``_breathe_*`` / ``_get_tool_color``）。

设计模式: 模板方法（Template Method）— 帧渲染骨架由渲染模块统一提供，
控制器（外观）委托本模块渲染，状态建模在 ``_subagent_state``。

输入约定：
  - ``render_frame(store, max_history)`` 以 ``StateStore`` 为输入，
    内部获取/释放 ``store._state_lock``（RLock 可重入）；
  - 渲染函数不修改状态（只读快照），全部输出为 ANSI 行（List[str]），
    作为「控制器→模型→组件」互换契约（模型 ``subagent_lines`` 存 ANSI 行）。

依赖约束（P3-11 更新允许清单）：仅依赖 _const/_config/_tool_icons/events/
_format/app._theme/app._fx 与**同级状态模块 _subagent_state** 与标准库
（无父包依赖、无事件订阅）；``_tool_icons`` / ``src.tools.registry``
保持函数内惰性导入（避免模块加载环）。
"""

from __future__ import annotations

import re
import time
from typing import List

from src.tui._const import (
    _C_ANSWERING,
    _C_BATCH,
    _C_BRANCH,
    _C_DIMMER,
    _C_DIMMEST,
    _C_DONE,
    _C_FAIL,
    _C_PARSING,
    _C_RESET,
    _C_RUNNING,
    _C_SUMMARY_DIM,
)
from src.tui._config import TuiConfig
from src.tui._tool_icons import TOOL_CATEGORY_COLORS, TOOL_CATEGORY_MAP
from src.tui.app import _fx
from src.tui.app._theme import time_glow
from src.tui._format import format_duration, format_tokens, format_speed

from src.tui._subagent_state import _AgentSlot, _ToolRecord

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_INDENT = "  "


def _single_line(text: str) -> str:
    """确保单行显示：将换行/回车转义为字面量（subagent 行契约=单行）。

    每个 ``subagent_lines`` 条目应为一条终端行；来源字段（description /
    parse_info / model_info / tool detail）可能含 ``\n``/``\r``，直接插入
    会使终端按换行渲染成两行。与 ``format_tool_record`` 既有转义一致，
    转义为可见字面量 ``\\n``/``\\r``。
    """
    return text.replace("\r", "\\r").replace("\n", "\\n") if text else ""

# ── 动效时间基配置 ──
_CFG = TuiConfig.defaults()
_FADE_DURATION: float = _CFG.fade_duration_sec       # FadeIn 渐显总时长（0.6s）
_FADE_START_COLOR: int = _CFG.fade_start_color       # FadeIn 起始暗色（238）
_SPINNER_HZ: float = _CFG.spinner_tick_hz            # spinner 时间基推进频率（10Hz）


def _color_256_ansi(code: int) -> str:
    """256 色号 → ANSI 前景序列。"""
    return f"\033[38;5;{code}m"


def _ansi_256_code(ansi: str) -> int | None:
    """从 ANSI 序列提取 256 色号（如 ``"\\033[38;5;214m"`` → 214）；无法解析返回 None。"""
    m = re.search(r"38;5;(\d+)", ansi)
    return int(m.group(1)) if m else None


def _fade_type_ansi(agent_type_ansi: str, elapsed: float) -> str:
    """agent 类型标签 FadeIn 渐显（BEAUTY-1）。

    时间基：elapsed>=duration 时返回原色（动画结束不触发重绘）；
    elapsed 期间从 ``_FADE_START_COLOR`` 渐变到原色号。
    """
    code = _ansi_256_code(agent_type_ansi)
    if code is None:
        return agent_type_ansi
    faded = _fx.fade_color(elapsed, _FADE_DURATION, _FADE_START_COLOR, code)
    return _color_256_ansi(faded)


def _breathe_running_ansi(active: bool) -> str:
    """running 摘要段呼吸色（BEAUTY-2）：仅 running 时 time_glow(214,220,12s) 呼吸。

    空闲（无 running）时返回静态 ``_C_RUNNING``（零开销，不触发重绘）。
    """
    if not active:
        return _C_RUNNING
    return _color_256_ansi(time_glow(214, 220, 12.0))


def _breathe_sep_ansi(active: bool) -> str:
    """分隔线微呼吸（BEAUTY-2）：仅 running 时在 [237, 245] 微呼吸。

    空闲时返回静态 ``_C_DIMMEST``。
    """
    if not active:
        return _C_DIMMEST
    return _color_256_ansi(time_glow(237, 245, 12.0))


def _get_tool_color(tool_name: str) -> str:
    """查询工具类别配色（共享单一真源映射，_tool_icons.TOOL_CATEGORY_MAP/COLORS）。

    函数签名保留（方向F 步骤12 收敛后查询共享映射，线程安全只读）。
    """
    cat = TOOL_CATEGORY_MAP.get(tool_name, "")
    # P3-17：默认兜底色引用 _C_SUMMARY_DIM（_const 模块级导入），
    # 消除硬编码 "\033[38;5;245m"（值一致，语义命名）
    return TOOL_CATEGORY_COLORS.get(cat, _C_SUMMARY_DIM)


# ═══════════════════════════════════════════════════════════
# 帧渲染（与旧 FrameRenderer 等效的输出格式）
# ═══════════════════════════════════════════════════════════

def render_frame(store, max_history: int = 3,
                 agents: dict | None = None,
                 order: list | None = None) -> List[str]:
    """渲染面板帧（摘要行 + 分隔线 + 各 Agent 行）。

    Args:
        store: ``StateStore`` 状态存储（内部取锁读取快照）。
        max_history: 工具历史展示条数上限（来自控制器构造参数）。
        agents: 可选状态字典覆盖（兼容控制器测试直接替换
            ``ctrl._agents`` 引用的场景；None 时用 ``store._agents``）。
        order: 可选顺序列表覆盖（同上；None 时用 ``store._order``）。

    Returns:
        面板 ANSI 行列表；无 agent 时返回空列表。
    """
    # 控制器（外观）可整体替换 _agents/_order 引用（既有测试模式）——
    # 渲染以调用方传入的当前引用为准，锁仍取自 store（RLock 可重入）。
    agents = store._agents if agents is None else agents
    order = store._order if order is None else order
    with store._state_lock:
        if not agents:
            return []
        now = time.time()
        lines: List[str] = []

        # ── 摘要行 ──
        total = len(order)
        done_count = 0
        total_output = 0
        earliest_start: float | None = None
        latest_speed = 0.0
        has_running = False

        for label in order:
            slot = agents.get(label)
            if slot is None:
                continue
            disp_out = slot.output_tokens + slot.live_output_tokens
            total_output += disp_out
            if slot.status == "running":
                has_running = True
                if slot.last_speed > 0:
                    latest_speed += slot.last_speed
            if slot.status in ("done", "fail"):
                done_count += 1
            if earliest_start is None or slot.start_time < earliest_start:
                earliest_start = slot.start_time

        elapsed = (now - earliest_start) if earliest_start else 0
        elapsed_str = format_duration(elapsed)
        output_str = format_tokens(total_output)
        speed_str = format_speed(latest_speed) if has_running else "-"

        sep = f" {_C_DIMMER}\u00b7{_C_RESET} "
        # 简易进度条
        bar_width = min(12, total * 4)
        if done_count < total:
            done_blocks = int(bar_width * done_count / total) if total else 0
            # BEAUTY-2：running 时进度条/图标经 time_glow 呼吸（空闲静态）
            running_ansi = _breathe_running_ansi(has_running)
            bar = (
                running_ansi + "\u2588" * done_blocks
                + _C_DIMMEST + "\u2591" * (bar_width - done_blocks)
                + _C_RESET
            )
            icon = f"{running_ansi}\u25cf{_C_RESET}"
            summary = (
                f"{icon} {_C_SUMMARY_DIM}{total} agents{_C_RESET}"
                f" {bar}"
                f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f"{sep}{_C_RUNNING}{done_count}/{total} done{_C_RESET}"
            )
        else:
            bar = _C_DONE + "\u2588" * bar_width + _C_RESET
            icon = f"{_C_DONE}\u2714{_C_RESET}"
            summary = (
                f"{icon} {_C_DONE}{total} agents{_C_RESET}"
                f" {bar}"
                f"{sep}{_C_SUMMARY_DIM}{output_str} out{_C_RESET}"
                f"{sep}{_C_SUMMARY_DIM}{elapsed_str}{_C_RESET}"
                f"{sep}{_C_DONE}{done_count}/{total} done{_C_RESET}"
            )
        lines.append(summary)

        # ── 分隔线（BEAUTY-2：running 时微呼吸，空闲静态） ──
        sep_ansi = _breathe_sep_ansi(has_running)
        lines.append(f"{sep_ansi} \u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501{_C_RESET}")

        # ── 各 Agent ──
        prev_has_sublines = False
        for idx, label in enumerate(order):
            slot = agents.get(label)
            if slot is None:
                continue
            is_last = (idx == len(order) - 1)

            # Agent 间空白延续行
            if idx > 0 and prev_has_sublines:
                lines.append(f"{_C_BRANCH} \u2502 {_C_RESET}")

            agent_lines = build_agent_lines(slot, now, is_last, max_history)
            lines.extend(agent_lines)
            prev_has_sublines = len(agent_lines) > 1

        # 清理尾部空行
        while lines and lines[-1] == "":
            lines.pop()
        return lines


def build_agent_lines(slot: _AgentSlot, now: float, is_last: bool,
                      max_history: int = 3) -> List[str]:
    """构建单个 Agent 的显示行（标题 + 阶段指示 + 工具历史）。"""
    lines: List[str] = []
    branch = " \u2514\u2500" if is_last else " \u251c\u2500"
    cont   = "   " if is_last else " \u2502 "

    elapsed = (slot.end_time or now) - slot.start_time
    elapsed_str = format_duration(elapsed)
    disp_out = slot.output_tokens + slot.live_output_tokens
    output_str = format_tokens(disp_out)
    speed_str = format_speed(slot.last_speed) if slot.status == "running" else ""

    # ── 类型标签（BEAUTY-1：新 agent 标题 FadeIn 渐显，时间基） ──
    from ._tool_icons import AGENT_TYPE_ABBREV, AGENT_TYPE_COLORS
    agent_type_ansi = AGENT_TYPE_COLORS.get(slot.agent_type, _C_DIMMER)
    abbr = AGENT_TYPE_ABBREV.get(slot.agent_type, "??")
    fade_elapsed = time.monotonic() - slot.appear_time
    type_tag = f"{_fade_type_ansi(agent_type_ansi, fade_elapsed)}[{abbr}]{_C_RESET}"

    # ── 状态图标 + 标题行 ──
    # P3-?：description 经 _single_line 转义（可能含 \n → 强制单行显示）
    description = _single_line(slot.description)
    if slot.status == "done":
        icon = f"{_C_DONE}\u2714{_C_RESET}"
        suffix = f"  {_C_DIMMER}{output_str}{_C_RESET}  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {description}{suffix}"
    elif slot.status == "fail":
        icon = f"{_C_FAIL}\u2716{_C_RESET}"
        suffix = f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        title = f"{_C_BRANCH}{branch}{_C_RESET} {icon} {type_tag} {description}{suffix}"
    else:
        # BEAUTY-3：spinner 时间基推进（非帧计数；_frame 字段保留兼容）
        spinner = _SPINNER_FRAMES[_fx.spinner_frame(_SPINNER_HZ, _SPINNER_FRAMES)]
        dot = f"{_C_RUNNING}{spinner}{_C_RESET}"
        suffix = (
            f"  {_C_DIMMER}{output_str}{_C_RESET}"
            f"  {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
            f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        )
        title = f"{_C_BRANCH}{branch}{_C_RESET} {dot} {type_tag} {description}{suffix}"
    lines.append(title)

    # ── 阶段指示 ──
    if slot.status == "running" and slot.model_phase:
        phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
        phase_time = f"{phase_elapsed:.1f}s"
        if slot.model_phase == "thinking":
            lines.append(
                f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}\u2026thinking  {phase_time}")
        elif slot.model_phase == "answering":
            lines.append(
                f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_ANSWERING}\u2026answering{_C_DIMMER}  {phase_time}{_C_RESET}")
        elif slot.model_phase == "parsing":
            extra = _single_line(slot.parse_info or slot.model_info)
            lines.append(
                f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_PARSING}\u2026parsing{_C_DIMMER}  {extra}{_C_RESET}")
        elif slot.model_phase == "batch":
            lines.append(
                f"{_C_DIMMER}{cont}{_C_RESET}{_INDENT}{_C_BATCH}\u2026batch{_C_DIMMER}  {_single_line(slot.model_info)}  {phase_time}{_C_RESET}")

    # ── 工具历史（仅 running 时展开；done/fail 折叠为单行） ──
    if slot.status not in ("done", "fail"):
        history = slot.tool_history[-max_history:]
        for rec in reversed(history):
            lines.append(format_tool_record(rec, now, cont))

    return lines


def format_tool_record(rec: _ToolRecord, now: float, cont: str) -> str:
    elapsed = (rec.end_time or now) - rec.start_time if rec.start_time else 0
    time_str = f"{elapsed:.1f}s"
    detail = _single_line(rec.detail)

    from ._tool_icons import TOOL_ICONS
    from src.tools.registry import get_tool_display_name
    tool_icon = TOOL_ICONS.get(rec.tool_name, "")
    display_name = get_tool_display_name(rec.tool_name)
    tool_color = _get_tool_color(rec.tool_name)
    tool_abbr = f"{tool_icon} {tool_color}{display_name}{_C_RESET}" if tool_icon else f"{tool_color}{display_name}{_C_RESET}"
    detail_disp = f" {_C_DIMMER}{detail}{_C_RESET}" if detail else ""
    prefix = f"{_C_BRANCH}{cont}{_C_RESET}{_INDENT}"

    if rec.phase == "parsing":
        line = f"{prefix}{_C_PARSING}\u25cc{_C_RESET} {tool_abbr}{detail_disp}"
    elif rec.phase == "running":
        # P2-14：硬编码 "\033[38;5;214m" → _C_RUNNING（_const 模块级导入，值一致）
        pulse_color = _C_RUNNING
        line = f"{prefix}{pulse_color}\u25cf{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    elif rec.phase == "done":
        line = f"{prefix}{_C_DONE}\u2714{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    else:  # fail
        line = f"{prefix}{_C_FAIL}\u2716{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    return line


__all__ = [
    "_SPINNER_FRAMES",
    "_get_tool_color",
    "render_frame",
    "build_agent_lines",
    "format_tool_record",
]
