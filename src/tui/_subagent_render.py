"""SubAgent 面板帧渲染（Layer 0 约束：仅依赖 _const/_config/_tool_icons/_format/app.* 叶模块与同级状态模块）。

方向C 步骤7：从 ``_subagent_panel.SubAgentPanelController`` 上帝类拆出的
帧渲染域——``render_frame`` / ``build_agent_lines`` / ``format_tool_record``
与动效辅助（``_fade_type_ansi`` / ``_get_tool_color``）。

对齐 Claude Code：子代理活动渲染为**逐 agent 卡片**（``┌─ ● ⚡ map 地图扫描 ─┐``
顶边框 + ``│`` 主体行 + ``└─ ✔ 完成 ─┘`` 底边框），不再输出汇总行/树形分支/
方括号类型标签。卡片内容宽度自适应（``wcswidth_simple`` 测量）。

设计模式: 模板方法（Template Method）— 帧渲染骨架由渲染模块统一提供，
控制器（外观）委托本模块渲染，状态建模在 ``_subagent_state``。

输入约定：
  - ``render_frame(store, max_history)`` 以 ``StateStore`` 为输入，
    内部获取/释放 ``store._state_lock``（RLock 可重入）；
  - 渲染函数不修改状态（只读快照），全部输出为 ANSI 行（List[str]），
    作为「控制器→模型→组件」互换契约（模型 ``subagent_lines`` 存 ANSI 行）。

依赖约束（P3-11 更新允许清单）：仅依赖 _const/_config/_tool_icons/events/
_format/app._theme/app._fx/_screen 与**同级状态模块 _subagent_state** 与标准库
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
from src.tui._screen import wcswidth_simple
from src.tui.app import _fx
from src.tui._format import format_duration, format_tokens, format_speed

from src.tui._subagent_state import _AgentSlot, _ToolRecord

#: 卡片边框色（对齐工具卡 palette.border fg=23 暗青）
_C_BORDER = "\033[38;5;23m"

_SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_INDENT = "  "

_ANSI_STRIP_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


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
    """agent 类型名 FadeIn 渐显（BEAUTY-1）。

    时间基：elapsed>=duration 时返回原色（动画结束不触发重绘）；
    elapsed 期间从 ``_FADE_START_COLOR`` 渐变到原色号。
    """
    code = _ansi_256_code(agent_type_ansi)
    if code is None:
        return agent_type_ansi
    faded = _fx.fade_color(elapsed, _FADE_DURATION, _FADE_START_COLOR, code)
    return _color_256_ansi(faded)


def _display_width(text: str) -> int:
    """ANSI 字符串显示宽度（剥离转义后按 wcswidth_simple 测量）。"""
    return wcswidth_simple(_ANSI_STRIP_RE.sub("", text))


def _pad_ansi(text: str, width: int) -> str:
    """右侧补空格至目标显示宽度（内容不超则原样）。"""
    pad = width - _display_width(text)
    return text + " " * pad if pad > 0 else text


def _get_tool_color(tool_name: str) -> str:
    """查询工具类别配色（共享单一真源映射，_tool_icons.TOOL_CATEGORY_MAP/COLORS）。

    函数签名保留（方向F 步骤12 收敛后查询共享映射，线程安全只读）。
    """
    cat = TOOL_CATEGORY_MAP.get(tool_name, "")
    # P3-17：默认兜底色引用 _C_SUMMARY_DIM（_const 模块级导入），
    # 消除硬编码 "\033[38;5;245m"（值一致，语义命名）
    return TOOL_CATEGORY_COLORS.get(cat, _C_SUMMARY_DIM)


# ═══════════════════════════════════════════════════════════
# 帧渲染（单卡合并，对齐 Claude Code 子代理 Task 卡 + 终端行数保护）
# ═══════════════════════════════════════════════════════════

def render_frame(store, max_history: int = 3,
                 agents: dict | None = None,
                 order: list | None = None,
                 max_lines: int | None = None) -> List[str]:
    """渲染面板帧（所有 Agent 合并为一个卡片，含终端行数保护）。

    Args:
        store: ``StateStore`` 状态存储（内部取锁读取快照）。
        max_history: 工具历史展示条数上限（来自控制器构造参数）。
        agents: 可选状态字典覆盖（兼容控制器测试直接替换
            ``ctrl._agents`` 引用的场景；None 时用 ``store._agents``）。
        order: 可选顺序列表覆盖（同上；None 时用 ``store._order``）。
        max_lines: 卡片最大总行数（终端行数保护）；None 时按终端高度推算。

    Returns:
        单卡片行列表；无 agent 时返回空列表。
    """
    # 控制器（外观）可整体替换 _agents/_order 引用（既有测试模式）——
    # 渲染以调用方传入的当前引用为准，锁仍取自 store（RLock 可重入）。
    agents = store._agents if agents is None else agents
    order = store._order if order is None else order
    with store._state_lock:
        if not agents:
            return []
        now = time.time()
        rows: list[tuple[str, str, List[str]]] = []
        for label in order:
            slot = agents.get(label)
            if slot is None:
                continue
            lines = build_agent_lines(slot, now, is_last=True, max_history=max_history)
            if not lines:
                continue
            rows.append((slot.status, lines[0], lines[1:]))
        if not rows:
            return []
        return _build_group_card(rows, now, max_lines)


def _terminal_max_lines() -> int:
    """按终端高度计算卡片最大行数（预留顶部标题栏 + 状态栏 + 输入区 + 边距）。

    终端尺寸查询失败回退 12（行数保护兜底）。
    """
    try:
        from src.tui._screen import _get_terminal_size
        _, h = _get_terminal_size()
        return max(6, h - 6)
    except Exception:
        return 12


def _build_group_card(rows: list[tuple[str, str, List[str]]],
                      now: float,
                      max_lines: int | None = None) -> List[str]:
    """构建子代理组卡片（所有 Agent 合并为一个卡，内容宽度自适应）。

    对齐 Claude Code：``┌─ ● ⚡ 子代理 · N ─┐`` 顶边框 + ``│`` 各 agent 行
    （running 优先并展开阶段/工具子行，done/fail 为单行）+ ``└─ ✔ 完成 ─┘``
    底边框（全部结束）。**行数保护**：卡片总行数 ≤ max_lines（终端高度推算），
    超限截断并追加 ``… +K 行省略`` 提示——防卡片撑爆终端可视区。
    """
    if max_lines is None:
        max_lines = _terminal_max_lines()
    n = len(rows)
    any_running = any(st == "running" for st, _, _ in rows)
    # 标题：●/✔ ⚡ 子代理 · N（⚡ 为 subagent 图标，对齐 Claude Code Task 卡）
    status_icon = f"{_C_RUNNING}\u25cf{_C_RESET}" if any_running else f"{_C_DONE}\u2714{_C_RESET}"
    title = f"{status_icon} {_C_RUNNING}\u26a1{_C_RESET} 子代理 \u00b7 {n}"
    # 主体行：running 优先（标题 + 缩进子行），done/fail 单行（后置）
    body: List[str] = []
    for status, t, sublines in rows:
        if status == "running":
            body.append(t)
            for s in sublines:
                body.append(f"{_C_DIMMER}\u2502{_C_RESET} {s}")
    for status, t, sublines in rows:
        if status != "running":
            body.append(t)
    # 行数保护：卡片总行数（顶 + 主体 + 底）≤ max_lines
    closed = not any_running
    budget = max_lines - (2 if closed else 1)
    if len(body) > budget:
        kept = max(1, budget - 1)  # 预留省略提示行
        dropped = len(body) - kept
        body = body[:kept] + [f"{_C_DIMMER}\u2026 +{dropped} 行省略{_C_RESET}"]
    # 组装卡片（内容宽度自适应）
    widths = [_display_width(title)] + [_display_width(l) for l in body]
    if closed:
        status_text = f"{_C_DONE}\u2714 完成{_C_RESET}"
        widths.append(_display_width(status_text))
    card_w = max(widths) + 6
    out: List[str] = []
    head = f"{_C_BORDER}\u250c\u2500 {_C_RESET}" + title
    head += f"{_C_BORDER}\u2500{_C_RESET}" * max(2, card_w - 4 - _display_width(title))
    head += f"{_C_BORDER}\u2510{_C_RESET}"
    out.append(head)
    for l in body:
        out.append(f"{_C_BORDER}\u2502 {_C_RESET}{_pad_ansi(l, card_w - 4)} "
                   f"{_C_BORDER}\u2502{_C_RESET}")
    if closed:
        tail = f"{_C_BORDER}\u2514\u2500 {_C_RESET}" + status_text
        tail += f"{_C_BORDER}\u2500{_C_RESET}" * max(2, card_w - 4 - _display_width(status_text))
        tail += f"{_C_BORDER}\u2518{_C_RESET}"
        out.append(tail)
    return out


def build_agent_lines(slot: _AgentSlot, now: float, is_last: bool,
                      max_history: int = 3) -> List[str]:
    """构建单个 Agent 的内容行（标题 + 阶段指示 + 工具历史，无树形分支）。

    首行为卡片顶边框标题（状态图标 + 类型名 + 描述 + 统计）；其余为卡片主体
    （阶段指示 + 工具记录）。``is_last`` 保留兼容参数（无分支后不再使用）。
    """
    lines: List[str] = []
    elapsed = (slot.end_time or now) - slot.start_time
    elapsed_str = format_duration(elapsed)
    disp_out = slot.output_tokens + slot.live_output_tokens
    output_str = format_tokens(disp_out)
    speed_str = format_speed(slot.last_speed) if slot.status == "running" else ""

    # ── 类型名（BEAUTY-1：FadeIn 渐显，时间基；无 `[xx]` 方括号标签） ──
    from ._tool_icons import AGENT_TYPE_COLORS
    agent_type_ansi = AGENT_TYPE_COLORS.get(slot.agent_type, _C_DIMMER)
    type_name = slot.agent_type or "??"
    fade_elapsed = time.monotonic() - slot.appear_time
    type_tag = f"{_fade_type_ansi(agent_type_ansi, fade_elapsed)}{type_name}{_C_RESET}"

    # ── 状态图标 + 标题行 ──
    # P3-?：description 经 _single_line 转义（可能含 \n → 强制单行显示）
    description = _single_line(slot.description)
    if slot.status == "done":
        icon = f"{_C_DONE}\u2714{_C_RESET}"
        suffix = f"  {_C_DIMMER}{output_str}{_C_RESET}  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        title = f"{icon} {type_tag} {description}{suffix}"
    elif slot.status == "fail":
        icon = f"{_C_FAIL}\u2716{_C_RESET}"
        suffix = f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        title = f"{icon} {type_tag} {description}{suffix}"
    else:
        # BEAUTY-3：spinner 时间基推进（非帧计数；_frame 字段保留兼容）
        spinner = _SPINNER_FRAMES[_fx.spinner_frame(_SPINNER_HZ, _SPINNER_FRAMES)]
        dot = f"{_C_RUNNING}{spinner}{_C_RESET}"
        suffix = (
            f"  {_C_DIMMER}{output_str}{_C_RESET}"
            f"  {_C_SUMMARY_DIM}{speed_str}{_C_RESET}"
            f"  {_C_DIMMER}{elapsed_str}{_C_RESET}"
        )
        title = f"{dot} {type_tag} {description}{suffix}"
    lines.append(title)

    # ── 阶段指示 ──
    if slot.status == "running" and slot.model_phase:
        phase_elapsed = now - slot.model_phase_start if slot.model_phase_start else 0
        phase_time = f"{phase_elapsed:.1f}s"
        if slot.model_phase == "thinking":
            lines.append(f"{_C_DIMMER}\u2026thinking{_C_RESET}  {phase_time}")
        elif slot.model_phase == "answering":
            lines.append(f"{_C_ANSWERING}\u2026answering{_C_RESET}  {_C_DIMMER}{phase_time}{_C_RESET}")
        elif slot.model_phase == "parsing":
            extra = _single_line(slot.parse_info or slot.model_info)
            lines.append(f"{_C_PARSING}\u2026parsing{_C_RESET}  {_C_DIMMER}{extra}{_C_RESET}")
        elif slot.model_phase == "batch":
            lines.append(f"{_C_BATCH}\u2026batch{_C_RESET}  {_C_DIMMER}{_single_line(slot.model_info)}  {phase_time}{_C_RESET}")

    # ── 工具历史（仅 running 时展开；done/fail 折叠为单行） ──
    if slot.status not in ("done", "fail"):
        history = slot.tool_history[-max_history:]
        for rec in reversed(history):
            lines.append(format_tool_record(rec, now, ""))
    return lines


def format_tool_record(rec: _ToolRecord, now: float, cont: str = "") -> str:
    """构建工具历史单行（无树形分支；``cont`` 保留兼容参数不再使用）。"""
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

    if rec.phase == "parsing":
        line = f"{_C_PARSING}\u25cc{_C_RESET} {tool_abbr}{detail_disp}"
    elif rec.phase == "running":
        # P2-14：硬编码 "\033[38;5;214m" → _C_RUNNING（_const 模块级导入，值一致）
        pulse_color = _C_RUNNING
        line = f"{pulse_color}\u25cf{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    elif rec.phase == "done":
        line = f"{_C_DONE}\u2714{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    else:  # fail
        line = f"{_C_FAIL}\u2716{_C_RESET} {tool_abbr}{detail_disp}  {_C_DIMMER}{time_str}{_C_RESET}"
    return line


__all__ = [
    "_SPINNER_FRAMES",
    "_get_tool_color",
    "render_frame",
    "build_agent_lines",
    "format_tool_record",
]
