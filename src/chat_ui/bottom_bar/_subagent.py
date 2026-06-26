"""SubAgent 槽位渲染模块。

在 bottom bar 固定区域渲染 subagent 状态面板。
使用绝对行号定位（_blessed_move_clear），消除与 chat 内容增长的耦合。

职责：
  - render_subagent_slots —— 渲染 subagent 槽位行到终端固定区域
  - _SUBAGENT_TYPE_ABBR —— agent_type → 缩写映射常量
"""

from __future__ import annotations

import time

from ...ui.ansi import truncate_ansi_visual
from ..infrastructure.styled import StyledText
from ._scroll_region import blessed_move_clear as _blessed_move_clear

# ── SubAgent 类型缩写映射 ──
_SUBAGENT_TYPE_ABBR: dict[str, str] = {
    "plan_execute": "exec",
    "map": "map",
    "review": "review",
    "plan": "plan",
    "read_memory": "mem",
    "write_memory": "wmem",
}


def render_subagent_slots(out, slots: dict, row_start: int, term_width: int) -> int:
    """渲染 subagent 槽位行到终端固定区域。

    使用绝对行号定位（_blessed_move_clear(row)）替代相对定位，
    消除与 chat 内容增长的耦合。

    Args:
        out: sys.__stdout__ 文件对象（与 bottom bar 一致的写入目标）
        slots: dict label → slot dict（与 TuiState.subagent_slots 结构一致）
        row_start: 起始行号（1-based）
        term_width: 终端宽度

    Returns:
        渲染的总行数
    """
    if not slots:
        return 0

    _now = time.time()
    abbrs = _SUBAGENT_TYPE_ABBR
    tw = term_width or 80

    row = row_start
    new_line_count = 0

    for label, slot in slots.items():
        # 提取字段
        desc = slot.get("description", label)
        agent_type = slot.get("agent_type", "plan_execute")
        status = slot.get("status", "running")
        output_tokens = slot.get("output_tokens", 0) + slot.get(
            "live_output_tokens", 0)
        start_time = slot.get("start_time", 0)
        end_time = slot.get("end_time", 0)

        # elapsed: running 用实时时钟，done/fail 用记录的 end_time
        if status == "running" and start_time > 0:
            elapsed = _now - start_time
        elif end_time > 0:
            elapsed = end_time - start_time
        else:
            elapsed = 0.0

        type_tag = abbrs.get(agent_type, agent_type[:4])

        # ── 预计算共享字段 ──
        token_str = f"{output_tokens}" if output_tokens else ""
        elapsed_str = f"{elapsed:.1f}s" if elapsed > 0 else ""

        # ── 终端宽度感知截断 ──
        prefix_w = 7 + len(type_tag)  # "  X [tag] "
        suffix_w = 0
        if status != "fail" and token_str:
            suffix_w += 4 + len(token_str) + 4  # "  · {n} out"
        if elapsed_str:
            suffix_w += 4 + len(elapsed_str)  # "  · {N.Ns}"
        available = max(tw - prefix_w - suffix_w - 1, 10)
        desc = truncate_ansi_visual(desc, max_visual=available)

        if status == "running":
            icon = "\u23fa"  # ⏺
            line_parts = [
                (f"  {icon} ", "cyan"),
                (f"[{type_tag}] ", "dim"),
                (f"{desc}", ""),
            ]
            if token_str:
                line_parts.append(("  · ", "dim"))
                line_parts.append((f"{token_str} out", "dim"))
            if elapsed_str:
                line_parts.append(("  · ", "dim"))
                line_parts.append((f"{elapsed_str}", "dim"))
            rendered = StyledText.assemble(*line_parts)
        elif status in ("done", "completed"):
            icon = "\u2713"  # ✓
            line_parts = [
                (f"  {icon} ", "green"),
                (f"[{type_tag}] ", "dim"),
                (f"{desc}", ""),
            ]
            if token_str:
                line_parts.append(("  · ", "dim"))
                line_parts.append((f"{token_str} out", "dim"))
            if elapsed_str:
                line_parts.append(("  · ", "dim"))
                line_parts.append((f"{elapsed_str}", "dim"))
            rendered = StyledText.assemble(*line_parts)
        else:  # fail
            icon = "\u2717"  # ✗
            line_parts = [
                (f"  {icon} ", "red"),
                (f"[{type_tag}] ", "dim"),
                (f"{desc}", ""),
            ]
            if elapsed_str:
                line_parts.append(("  · ", "dim"))
                line_parts.append((f"{elapsed_str}", "dim"))
            rendered = StyledText.assemble(*line_parts)

        # 使用绝对行号定位写入（move_clear 已清行，无需 \033[K）
        out.write(_blessed_move_clear(row) + f"\r{rendered}")
        row += 1
        new_line_count += 1

        # ── 模型阶段状态行（思考中/回答中/接收工具参数中 + 耗时）──
        model_phase = slot.get("model_phase", "")
        model_phase_start = slot.get("model_phase_start", 0.0)
        if model_phase:
            if model_phase_start > 0:
                phase_elapsed = _now - model_phase_start
            else:
                phase_elapsed = 0.0

            phase_elapsed_str = f"{phase_elapsed:.1f}s" if phase_elapsed > 0 else ""

            # 根据 phase 确定中文标签和颜色
            if model_phase == "thinking":
                phase_label = "思考中"
                phase_color = "yellow"
            elif model_phase == "answering":
                phase_label = "回答中"
                phase_color = "cyan"
            elif model_phase == "parsing":
                phase_label = "接收工具参数中"
                phase_color = "yellow"
            else:
                phase_label = model_phase
                phase_color = ""

            # 终端宽度截断
            phase_prefix_w = 6  # "    ⟳ "
            phase_suffix_w = 4 + len(phase_elapsed_str) if phase_elapsed_str else 0
            phase_available = max(tw - phase_prefix_w - phase_suffix_w - 1, 10)
            display_label = truncate_ansi_visual(phase_label, max_visual=phase_available)

            phase_parts = [
                (f"    \u27f3 ", f"dim {phase_color}" if phase_color else "dim"),
                (f"{display_label}", "dim"),
            ]
            if phase_elapsed_str:
                phase_parts.append(("  \u00b7 ", "dim"))
                phase_parts.append((phase_elapsed_str, "dim"))

            phase_line = StyledText.assemble(*phase_parts)
            out.write(_blessed_move_clear(row) + f"\r{phase_line}")
            row += 1
            new_line_count += 1

        # ── 工具调用历史（最近 3 条，倒序）──
        tool_history = slot.get("tool_history", [])
        if tool_history:
            recent_tools = list(reversed(tool_history[-3:]))
            for rec in recent_tools:
                t_name = rec.get("tool_name") or "?"
                t_detail = rec.get("detail", "")
                t_phase = rec.get("phase", "running")
                t_start = rec.get("start_time", 0)
                t_end = rec.get("end_time", 0)

                # elapsed
                if t_phase in ("running", "parsing") and t_start > 0:
                    t_elapsed = _now - t_start
                elif t_end > 0:
                    t_elapsed = t_end - t_start
                else:
                    t_elapsed = 0.0

                tool_desc = f"{t_name} {t_detail}" if t_detail else t_name

                t_elapsed_str = f"{t_elapsed:.1f}s" if t_elapsed > 0 else ""

                # 终端宽度截断
                t_prefix_w = 6  # "    X "
                t_suffix_w = 4 + len(t_elapsed_str) if t_elapsed_str else 0
                t_available = max(tw - t_prefix_w - t_suffix_w - 1, 10)
                tool_desc = truncate_ansi_visual(tool_desc, max_visual=t_available)

                if t_phase in ("done",):
                    t_icon = "\u2713"  # ✓
                    t_icon_color = "dim green"
                elif t_phase in ("fail",):
                    t_icon = "\u2717"  # ✗
                    t_icon_color = "dim red"
                else:  # running / parsing
                    t_icon = "\u27f3"  # ⟳
                    t_icon_color = "dim yellow"

                t_parts = [
                    (f"    {t_icon} ", t_icon_color),
                    (f"{tool_desc}", "dim"),
                ]
                if t_elapsed_str:
                    t_parts.append(("  \u00b7 ", "dim"))
                    t_parts.append((t_elapsed_str, "dim"))

                t_line = StyledText.assemble(*t_parts)
                out.write(_blessed_move_clear(row) + f"\r{t_line}")
                row += 1
                new_line_count += 1

    return new_line_count
