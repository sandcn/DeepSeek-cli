"""_mermaid_class_state_gantt_pie — Mermaid 类图+状态图+甘特+饼图渲染 Mixin。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._mermaid_helpers import (
    _STYLE_BOX, _STYLE_NODE, _STYLE_EDGE_LABEL, _STYLE_HEADER,
    _STYLE_ARROW, _STYLE_FIELD, _STYLE_METHOD, _STYLE_RELATION,
    _STYLE_SUBGRAPH,
    _extract_word_ids,
    _starts_with_ignore_case, _is_comment_line,
)


class MermaidClassStateGanttPieMixin:
    """MermaidRenderer 类图、状态图、甘特图、饼图渲染 Mixin。"""

    # ═══════════════════════════════════════════════════════════
    # Class Diagram
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse_class_decl(s):
        if _starts_with_ignore_case(s.strip(), 'class '):
            rest = s.strip()[6:].strip().split()[0] if s.strip()[6:].strip() else ""
            return rest if rest else None
        return None

    @staticmethod
    def _parse_class_rel(s):
        rel_symbols = ['<|--', '*--', 'o--', '-->', '--|', '..>', '..|>']
        for sym in rel_symbols:
            idx = s.find(sym)
            if idx != -1:
                left = s[:idx].strip()
                right = s[idx + len(sym):].strip()
                left_ids = _extract_word_ids(left)
                right_ids = _extract_word_ids(right)
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, sym, dst
        return None

    def _render_class(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        classes: dict[str, list[str]] = {}
        relations: list[tuple[str, str, str]] = []
        current_class: str | None = None

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_class_decl(s)
            if m:
                current_class = m
                if current_class not in classes:
                    classes[current_class] = []
                continue
            m = self._parse_class_rel(s)
            if m:
                src, rel, dst = m
                relations.append((src, rel, dst))
                for c in (src, dst):
                    if c not in classes:
                        classes[c] = []
                continue
            if ':' in s:
                parts = s.split(':', 1)
                cname = parts[0].strip()
                field = parts[1].strip()
                if cname in classes:
                    classes[cname].append(field)
                    current_class = cname
                elif current_class and field:
                    classes.setdefault(current_class, []).append(s)
                continue
            if current_class and s and not _starts_with_ignore_case(s, 'class') and not _is_comment_line(s):
                classes.setdefault(current_class, []).append(s)

        if not classes:
            return Text("  📊 class diagram: no classes", style=Style(dim=True, italic=True))

        for cname, members in classes.items():
            width = max(len(cname) + 4, 16)
            for m in members:
                width = max(width, len(m) + 4)
            width = min(width, 40)
            result.append(f"  ┌{'─' * (width - 2)}┐", style=_STYLE_BOX)
            result.append("\n")
            result.append(f"  │ {cname:<{width - 4}s} │ ", style=_STYLE_NODE)
            result.append("\n")
            if members:
                result.append(f"  ├{'─' * (width - 2)}┤", style=_STYLE_BOX)
                result.append("\n")
                for member in members:
                    st = _STYLE_METHOD if ('(' in member and ')' in member) else _STYLE_FIELD
                    result.append(f"  │ {member:<{width - 4}s} │ ", style=st)
                    result.append("\n")
            result.append(f"  └{'─' * (width - 2)}┘", style=_STYLE_BOX)
            result.append("\n\n")

        sym = {"<|--": "◁─", "*--": "◆─", "o--": "○─",
               "-->": "─▶", "--|": "─▷", "..>": "·▶", "..|>": "·▷"}
        for src, rel, dst in relations:
            result.append(f"  {src} {sym.get(rel, rel)} {dst}", style=_STYLE_RELATION)
            result.append("\n")
        return result

    # ═══════════════════════════════════════════════════════════
    # State Diagram
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse_state_edge(s):
        s = s.strip()
        arrow_idx = s.find('-->')
        if arrow_idx == -1:
            return None
        left = s[:arrow_idx].strip()
        right = s[arrow_idx + 3:].strip()
        if left == '[*]' or left == '*':
            src = '*'
        elif _extract_word_ids(left):
            src = _extract_word_ids(left)[-1]
        else:
            src = left
        label = ""
        colon_idx = right.find(':')
        if colon_idx != -1:
            label = right[colon_idx + 1:].strip()
            right = right[:colon_idx].strip()
        if right == '[*]' or right == '*':
            dst = '*'
        elif _extract_word_ids(right):
            dst = _extract_word_ids(right)[0]
        else:
            dst = right
        return src, dst, label

    def _render_state(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        states: set[str] = set()
        trans: list[tuple[str, str, str]] = []

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_state_edge(s)
            if m:
                s1, s2, label = m
                if s1 != "*":
                    states.add(s1)
                if s2 != "*":
                    states.add(s2)
                trans.append((s1, s2, label))

        if not states and not trans:
            return Text("  📊 state: no states", style=Style(dim=True, italic=True))

        if any(s == "*" for s, _, _ in trans):
            result.append("  ● (initial)", style=Style(dim=True))
            result.append("\n\n")

        for st in sorted(states):
            w = max(len(st) + 2, 6)
            result.append(f"  ┌{'─' * (w - 2)}┐", style=_STYLE_BOX)
            result.append("\n")
            result.append(f"  │{st:^{w - 2}s}│", style=_STYLE_NODE)
            result.append("\n")
            result.append(f"  └{'─' * (w - 2)}┘", style=_STYLE_BOX)
            result.append("\n")
            for src, dst, label in trans:
                if src == st:
                    result.append("    ──▶ ", style=_STYLE_ARROW)
                    if dst == "*":
                        result.append("● (final)", style=Style(dim=True))
                    else:
                        result.append(dst, style=_STYLE_NODE)
                    if label:
                        result.append(f"  [{label}]", style=_STYLE_EDGE_LABEL)
                    result.append("\n")
            result.append("\n")

        if any(d == "*" for _, d, _ in trans):
            result.append("  ● (final)", style=Style(dim=True))
            result.append("\n")

        return result

    # ═══════════════════════════════════════════════════════════
    # Gantt Chart
    # ═══════════════════════════════════════════════════════════

    def _render_gantt(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        title = ""
        sections: list[tuple[str, list[tuple[str, int]]]] = []
        cur_section = ("", [])

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            if s.lower().startswith("title "):
                title = s[6:].strip()
                continue
            if s.lower().startswith("dateformat"):
                continue
            if s.lower().startswith("section "):
                if cur_section[1]:
                    sections.append(cur_section)
                cur_section = (s[8:].strip(), [])
                continue
            parts = s.split(':')
            if len(parts) >= 2:
                task_name = parts[0].strip()
                detail_parts = parts[1].strip()
                duration = self._extract_duration(detail_parts)
                if duration is not None and task_name:
                    cur_section[1].append((task_name, duration))

        if cur_section[1]:
            sections.append(cur_section)

        if not sections:
            return Text("  📊 gantt: no tasks", style=Style(dim=True, italic=True))

        all_durations = [d for _, tasks in sections for _, d in tasks]
        max_dur = max(all_durations) if all_durations else 1
        bar_max = 20

        if title:
            result.append(f"  📅 {title}", style=_STYLE_HEADER)
            result.append("\n")
            result.append(f"  {'─' * min(len(title) + 4, 30)}", style=Style(dim=True))
            result.append("\n")

        for sec_idx, (sec_name, tasks) in enumerate(sections):
            if sec_name:
                result.append(f"  📁 {sec_name}", style=_STYLE_SUBGRAPH)
                result.append("\n")
            for task_name, duration in tasks:
                bar_len = max(1, round(duration / max_dur * bar_max))
                bar = "█" * bar_len
                result.append(
                    f"    {task_name:<12} {bar:<{bar_max}}  {duration}d",
                    style=_STYLE_NODE,
                )
                result.append("\n")
            if sec_idx < len(sections) - 1:
                result.append("\n")

        return result

    @staticmethod
    def _extract_duration(detail_str):
        parts = detail_str.split(',')
        for p in reversed(parts):
            p = p.strip()
            num_str = ""
            for ch in p:
                if ch.isdigit():
                    num_str += ch
                elif num_str:
                    break
            if num_str:
                return int(num_str)
        return None

    # ═══════════════════════════════════════════════════════════
    # Pie Chart
    # ═══════════════════════════════════════════════════════════

    def _render_pie(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        title = ""
        data: list[tuple[str, float]] = []

        for line in lines:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            if s.lower().startswith("pie title "):
                title = s[10:].strip()
                continue
            elif s.lower().startswith("pie"):
                continue
            colon_idx = s.find(':')
            if colon_idx != -1:
                label_raw = s[:colon_idx].strip()
                value_raw = s[colon_idx + 1:].strip()
                label = label_raw.strip('"').strip("'").strip()
                try:
                    value = float(value_raw)
                    data.append((label, value))
                except ValueError:
                    pass

        if not data:
            return Text("  🥧 pie: no data", style=Style(dim=True, italic=True))

        total = sum(v for _, v in data)
        if total == 0:
            return Text("  🥧 (zero data)", style=Style(dim=True, italic=True))

        if title:
            result.append(f"  🥧 {title}", style=_STYLE_HEADER)
            result.append("\n")
            result.append(f"  {'─' * 20}", style=Style(dim=True))
            result.append("\n")

        bar_max = 20
        for label, value in data:
            pct = value / total * 100
            bar_len = max(1, round(pct / 100 * bar_max))
            bar = "█" * bar_len
            result.append(
                f"  {label:<8} {bar:<{bar_max}}  {pct:.1f}%",
                style=_STYLE_NODE,
            )
            result.append("\n")

        return result
