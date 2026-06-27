"""React Ink SubAgent 槽位渲染组件。"""
from __future__ import annotations
import time
from .base import TuiComponent
from ..bottom_bar._theme import _SUBAGENT_TYPE_ABBR
from ..infrastructure.styled import StyledText
from ...ui.ansi import truncate_ansi_visual


class SubagentSlotsComponent(TuiComponent):
    """SubAgent 槽位渲染 — 在底部栏固定区域渲染 SubAgent 状态面板。
    
    Props:
        slots: dict - label → slot dict
        term_width: int - 终端宽度
    """
    
    def __init__(self, **props):
        super().__init__(children=None)
        self._props = props
    
    def render(self) -> str:
        slots = self._props.get("slots", {})
        if not slots:
            return ""
        
        term_width = self._props.get("term_width", 80)
        _now = time.time()
        abbrs = _SUBAGENT_TYPE_ABBR
        tw = term_width or 80
        lines = []
        
        for label, slot in slots.items():
            desc = slot.get("description", label)
            agent_type = slot.get("agent_type", "plan_execute")
            status = slot.get("status", "running")
            output_tokens = slot.get("output_tokens", 0) + slot.get("live_output_tokens", 0)
            start_time = slot.get("start_time", 0)
            end_time = slot.get("end_time", 0)
            
            if status == "running" and start_time > 0:
                elapsed = _now - start_time
            elif end_time > 0:
                elapsed = end_time - start_time
            else:
                elapsed = 0.0
            
            type_tag = abbrs.get(agent_type, agent_type[:4])
            token_str = f"{output_tokens}" if output_tokens else ""
            elapsed_str = f"{elapsed:.1f}s" if elapsed > 0 else ""
            
            prefix_w = 7 + len(type_tag)
            suffix_w = 0
            if status != "fail" and token_str:
                suffix_w += 4 + len(token_str) + 4
            if elapsed_str:
                suffix_w += 4 + len(elapsed_str)
            available = max(tw - prefix_w - suffix_w - 1, 10)
            desc = truncate_ansi_visual(desc, max_visual=available)
            
            if status == "running":
                icon = "\u23fa"
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
                icon = "\u2713"
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
            else:
                icon = "\u2717"
                line_parts = [
                    (f"  {icon} ", "red"),
                    (f"[{type_tag}] ", "dim"),
                    (f"{desc}", ""),
                ]
                if elapsed_str:
                    line_parts.append(("  · ", "dim"))
                    line_parts.append((f"{elapsed_str}", "dim"))
                rendered = StyledText.assemble(*line_parts)
            
            lines.append(rendered)
            
            # 模型阶段状态行
            model_phase = slot.get("model_phase", "")
            model_phase_start = slot.get("model_phase_start", 0.0)
            if model_phase:
                if model_phase_start > 0:
                    phase_elapsed = _now - model_phase_start
                else:
                    phase_elapsed = 0.0
                phase_elapsed_str = f"{phase_elapsed:.1f}s" if phase_elapsed > 0 else ""
                
                if model_phase == "thinking":
                    phase_label, phase_color = "思考中", "yellow"
                elif model_phase == "answering":
                    phase_label, phase_color = "回答中", "cyan"
                elif model_phase == "parsing":
                    phase_label, phase_color = "接收工具参数中", "yellow"
                else:
                    phase_label, phase_color = model_phase, ""
                
                phase_prefix_w = 6
                phase_suffix_w = 4 + len(phase_elapsed_str) if phase_elapsed_str else 0
                phase_available = max(tw - phase_prefix_w - phase_suffix_w - 1, 10)
                display_label = truncate_ansi_visual(phase_label, max_visual=phase_available)
                
                phase_parts = [
                    (f"    \u27f3 ", f"dim {phase_color}" if phase_color else "dim"),
                    (f"{display_label}", "dim"),
                ]
                if phase_elapsed_str:
                    phase_parts.append(("  · ", "dim"))
                    phase_parts.append((phase_elapsed_str, "dim"))
                phase_line = StyledText.assemble(*phase_parts)
                lines.append(phase_line)
            
            # 工具调用历史
            tool_history = slot.get("tool_history", [])
            if tool_history:
                recent_tools = list(reversed(tool_history[-3:]))
                for rec in recent_tools:
                    t_name = rec.get("tool_name") or "?"
                    t_detail = rec.get("detail", "")
                    t_phase = rec.get("phase", "running")
                    t_start = rec.get("start_time", 0)
                    t_end = rec.get("end_time", 0)
                    
                    if t_phase in ("running", "parsing") and t_start > 0:
                        t_elapsed = _now - t_start
                    elif t_end > 0:
                        t_elapsed = t_end - t_start
                    else:
                        t_elapsed = 0.0
                    
                    tool_desc = f"{t_name} {t_detail}" if t_detail else t_name
                    t_elapsed_str = f"{t_elapsed:.1f}s" if t_elapsed > 0 else ""
                    
                    t_prefix_w = 6
                    t_suffix_w = 4 + len(t_elapsed_str) if t_elapsed_str else 0
                    t_available = max(tw - t_prefix_w - t_suffix_w - 1, 10)
                    tool_desc = truncate_ansi_visual(tool_desc, max_visual=t_available)
                    
                    if t_phase in ("done",):
                        t_icon, t_icon_color = "\u2713", "dim green"
                    elif t_phase in ("fail",):
                        t_icon, t_icon_color = "\u2717", "dim red"
                    else:
                        t_icon, t_icon_color = "\u27f3", "dim yellow"
                    
                    t_parts = [
                        (f"    {t_icon} ", t_icon_color),
                        (f"{tool_desc}", "dim"),
                    ]
                    if t_elapsed_str:
                        t_parts.append(("  · ", "dim"))
                        t_parts.append((t_elapsed_str, "dim"))
                    t_line = StyledText.assemble(*t_parts)
                    lines.append(t_line)
        
        return "\n".join(str(line) for line in lines)
