"""_mermaid_extra — Mermaid ER/状态/思维导图/时间线/旅程图渲染 Mixin。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._mermaid_helpers import (
    _STYLE_BOX, _STYLE_NODE, _STYLE_EDGE_LABEL, _STYLE_HEADER,
    _STYLE_ARROW, _STYLE_ACTOR, _STYLE_SUBGRAPH,
    _is_comment_line,
)


class MermaidExtraMixin:
    """MermaidRenderer 额外图表类型渲染 Mixin。

    包含：ER 图、Gitgraph、思维导图、时间线、旅程图。
    """

    # ═══════════════════════════════════════════════════════════
    # ER Diagram
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _parse_er_rel(s):
        colon_idx = s.find(':')
        if colon_idx == -1:
            return None
        left_part = s[:colon_idx].strip()
        label = s[colon_idx + 1:].strip().strip('"').strip("'").strip()
        parts = left_part.split()
        if len(parts) >= 3:
            e1 = parts[0]
            rel_sym = parts[1]
            e2 = parts[-1]
            return e1, rel_sym, e2, label
        return None

    def _render_er(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        relationships: list[tuple[str, str, str, str]] = []

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_er_rel(s)
            if m:
                e1, rel, e2, label = m
                relationships.append((e1, rel, e2, label))

        if not relationships:
            return Text("  📊 er: no relationships", style=Style(dim=True, italic=True))

        for i, (e1, rel_sym, e2, label) in enumerate(relationships):
            w1 = max(len(e1) + 4, 10)
            w2 = max(len(e2) + 4, 10)
            result.append(
                f"  ┌{'─' * (w1 - 2)}┐     ┌{'─' * (w2 - 2)}┐",
                style=_STYLE_BOX,
            )
            result.append("\n")
            rel_ch = "─" * 5
            result.append(
                f"  │ {e1:^{w1 - 4}s} │{rel_ch}│ {e2:^{w2 - 4}s} │",
                style=_STYLE_NODE,
            )
            result.append("\n")
            result.append(
                f"  └{'─' * (w1 - 2)}┘     └{'─' * (w2 - 2)}┘",
                style=_STYLE_BOX,
            )
            result.append("\n")
            result.append(f"       {label}", style=_STYLE_EDGE_LABEL)
            result.append("\n\n")

        return result

    # ═══════════════════════════════════════════════════════════
    # Gitgraph
    # ═══════════════════════════════════════════════════════════

    def _render_gitgraph(self, lines: list[str]) -> Text:
        try:
            result = Text()
            result.append("\n")
            branches: list[str] = ["main"]
            branch_col: dict[str, int] = {"main": 0}
            current = "main"
            steps: list[tuple] = []

            for line in lines[1:]:
                s = line.strip()
                if not s or _is_comment_line(s):
                    continue
                if s == "commit":
                    steps.append(("commit", current))
                elif s.startswith("branch "):
                    name = s[7:].strip()
                    if name not in branch_col:
                        branch_col[name] = len(branches)
                        branches.append(name)
                    steps.append(("branch", name))
                elif s.startswith("checkout "):
                    current = s[9:].strip()
                elif s.startswith("merge "):
                    name = s[6:].strip()
                    steps.append(("merge", name, current))

            if not steps:
                return Text("  📊 gitgraph: no commits", style=Style(dim=True, italic=True))

            col_w = max(12, max(len(b) + 2 for b in branches))
            result.append("  ")
            for b in branches:
                result.append(f"{b:<{col_w}}", style=_STYLE_ACTOR)
            result.append("\n")
            result.append("  ")
            for _ in branches:
                result.append("─" * col_w, style=_STYLE_BOX)
            result.append("\n")

            active: set[str] = {"main"}

            for step in steps:
                row = Text("  ")
                if step[0] == "commit":
                    _, cur = step
                    for b in branches:
                        if b == cur:
                            row.append(f"{'●':^{col_w}}", style=_STYLE_NODE)
                        elif b in active:
                            row.append(f"{'│':^{col_w}}", style=_STYLE_BOX)
                        else:
                            row.append(f"{' ' * col_w}")
                elif step[0] == "branch":
                    _, name = step
                    active.add(name)
                    for b in branches:
                        if b in active:
                            row.append(f"{'│':^{col_w}}", style=_STYLE_BOX)
                        else:
                            row.append(f"{' ' * col_w}")
                elif step[0] == "merge":
                    _, src_name, dst_name = step
                    src_idx = branch_col.get(src_name, 0)
                    dst_idx = branch_col.get(dst_name, 0)
                    left_idx = min(src_idx, dst_idx)
                    right_idx = max(src_idx, dst_idx)

                    total_width = col_w * len(branches)
                    chars = [" "] * total_width

                    for i, b in enumerate(branches):
                        offset = i * col_w
                        if b in active:
                            mid = offset + col_w // 2
                            if i == dst_idx or i == src_idx:
                                chars[mid] = "●"
                            else:
                                chars[mid] = "│"

                    if left_idx != right_idx:
                        left_center = left_idx * col_w + col_w // 2
                        right_center = right_idx * col_w + col_w // 2
                        for p in range(left_center + 1, right_center):
                            chars[p] = "─"
                        if dst_idx < src_idx:
                            if left_center + 1 < right_center:
                                chars[left_center + 1] = "◀"
                        else:
                            if right_center - 1 > left_center:
                                chars[right_center - 1] = "▶"

                    chars_str = "".join(chars)
                    for i, b in enumerate(branches):
                        segment = chars_str[i * col_w:(i + 1) * col_w]
                        if i == dst_idx or i == src_idx:
                            row.append(f"{segment:<{col_w}}", style=_STYLE_NODE)
                        else:
                            row.append(f"{segment:<{col_w}}", style=_STYLE_BOX)

                result.append(row)
                result.append("\n")

            return result
        except Exception:
            return Text("  📊 gitgraph: (parse error)", style=Style(dim=True, italic=True))

    # ═══════════════════════════════════════════════════════════
    # Mindmap
    # ═══════════════════════════════════════════════════════════

    def _render_mindmap(self, lines: list[str]) -> Text:
        try:
            result = Text()
            result.append("\n")
            items: list[tuple[int, str, str]] = []

            leading_counts: list[int] = []
            for raw in lines[1:]:
                s = raw.rstrip()
                if not s or _is_comment_line(s):
                    continue
                stripped = s.lstrip()
                leading = len(s) - len(stripped)
                if leading > 0:
                    leading_counts.append(leading)
            indent_size = min(leading_counts) if leading_counts else 2

            for raw in lines[1:]:
                s = raw.rstrip()
                if not s or _is_comment_line(s):
                    continue
                stripped = s.lstrip()
                leading = len(s) - len(stripped)
                level = leading // indent_size if indent_size else 0
                text = stripped

                shape = "default"
                display = text
                if len(text) >= 4 and text[:2] == '((' and text[-2:] == '))':
                    shape = "double_circle"
                    display = text[2:-2]
                elif len(text) >= 2 and text[0] == '[' and text[-1] == ']':
                    shape = "square"
                    display = text[1:-1]
                elif len(text) >= 2 and text[0] == '(' and text[-1] == ')':
                    shape = "round"
                    display = text[1:-1]

                items.append((level, display, shape))

            if not items:
                return Text("  📊 mindmap: empty", style=Style(dim=True, italic=True))

            root_emoji = "🌳 "

            def _has_sibling(idx: int) -> bool:
                cur_level = items[idx][0]
                for j in range(idx + 1, len(items)):
                    if items[j][0] < cur_level:
                        return False
                    if items[j][0] == cur_level:
                        return True
                return False

            root_level = items[0][0]
            result.append(f"  {root_emoji}", style=_STYLE_HEADER)
            result.append(f"{items[0][1]}", style=_STYLE_HEADER)
            result.append("\n")

            level_has_more: dict[int, bool] = {}

            for idx, (level, display, shape) in enumerate(items[1:], start=1):
                pref = ""
                for l in range(1, level - root_level):
                    if level_has_more.get(l, False):
                        pref += "│  "
                    else:
                        pref += "   "
                has_more = _has_sibling(idx)
                level_has_more[level - root_level] = has_more
                connector = "├─ " if has_more else "└─ "
                pref += connector
                if shape == "double_circle":
                    st = _STYLE_NODE
                    display_text = f"◎ {display}"
                elif shape == "square":
                    st = _STYLE_NODE
                    display_text = f"▢ {display}"
                elif shape == "round":
                    st = _STYLE_NODE
                    display_text = f"◯ {display}"
                else:
                    st = _STYLE_NODE
                    display_text = display

                result.append(f"  {pref}", style=_STYLE_BOX)
                result.append(f"{display_text}", style=st)
                result.append("\n")

            return result
        except Exception:
            return Text("  📊 mindmap: (parse error)", style=Style(dim=True, italic=True))

    # ═══════════════════════════════════════════════════════════
    # Timeline
    # ═══════════════════════════════════════════════════════════

    def _render_timeline(self, lines: list[str]) -> Text:
        try:
            result = Text()
            result.append("\n")
            title = ""
            entries: list[tuple[str, list[str]]] = []

            for line in lines[1:]:
                s = line.strip()
                if not s or _is_comment_line(s):
                    continue
                if s.lower().startswith("title "):
                    title = s[6:].strip()
                    continue
                parts = [p.strip() for p in s.split(":")]
                if len(parts) >= 1:
                    time_point = parts[0]
                    events = parts[1:]
                    if time_point:
                        entries.append((time_point, events))

            if not entries and not title:
                return Text("  📊 timeline: empty", style=Style(dim=True, italic=True))

            if title:
                result.append("  📅 ", style=_STYLE_HEADER)
                result.append(f"{title}", style=_STYLE_HEADER)
                result.append("\n\n")

            for idx, (time_point, events) in enumerate(entries):
                result.append(f"  {time_point}", style=_STYLE_HEADER)
                result.append("\n")
                if events:
                    for event in events:
                        if event:
                            result.append("    ●── ", style=_STYLE_ARROW)
                            result.append(f"{event}", style=_STYLE_NODE)
                            result.append("\n")
                if idx < len(entries) - 1:
                    result.append("    │", style=_STYLE_BOX)
                    result.append("\n")
                    result.append("    │", style=_STYLE_BOX)
                    result.append("\n")

            return result
        except Exception:
            return Text("  📊 timeline: (parse error)", style=Style(dim=True, italic=True))

    # ═══════════════════════════════════════════════════════════
    # Journey
    # ═══════════════════════════════════════════════════════════

    def _render_journey(self, lines: list[str]) -> Text:
        try:
            result = Text()
            result.append("\n")
            title = ""
            sections: list[tuple[str, list[tuple[str, int, str]]]] = []
            cur_sec_name = ""
            cur_tasks: list[tuple[str, int, str]] = []
            role_emoji = {"用户": "👤", "系统": "🤖"}

            for line in lines[1:]:
                s = line.strip()
                if not s or _is_comment_line(s):
                    continue
                if s.lower().startswith("title "):
                    title = s[6:].strip()
                    continue
                if s.lower().startswith("section "):
                    if cur_tasks:
                        sections.append((cur_sec_name, cur_tasks))
                    cur_sec_name = s[8:].strip()
                    cur_tasks = []
                    continue
                parts = [p.strip() for p in s.split(":")]
                if len(parts) >= 2:
                    task_name = parts[0]
                    try:
                        score = int(parts[1])
                    except ValueError:
                        score = 3
                    role = parts[2] if len(parts) >= 3 else "用户"
                    if task_name:
                        cur_tasks.append((task_name, max(1, min(5, score)), role))

            if cur_tasks:
                sections.append((cur_sec_name, cur_tasks))

            if not sections and not title:
                return Text("  📊 journey: empty", style=Style(dim=True, italic=True))

            bar_max = 15

            if title:
                result.append("  🧭 ", style=_STYLE_HEADER)
                result.append(f"{title}", style=_STYLE_HEADER)
                result.append("\n\n")

            for sec_idx, (sec_name, tasks) in enumerate(sections):
                if sec_name:
                    result.append("  📁 ", style=_STYLE_SUBGRAPH)
                    result.append(f"{sec_name}", style=_STYLE_SUBGRAPH)
                    result.append("\n")
                for task_name, score, role in tasks:
                    bar_len = max(1, round(score / 5 * bar_max))
                    bar = "█" * bar_len
                    emoji = role_emoji.get(role, "🔘")
                    result.append(f"    {task_name:<10} ", style=_STYLE_NODE)
                    result.append(f"{bar:<{bar_max}}", style=_STYLE_ARROW)
                    result.append(f"  {score}  ", style=_STYLE_EDGE_LABEL)
                    result.append(f"{emoji}{role}", style=_STYLE_EDGE_LABEL)
                    result.append("\n")
                if sec_idx < len(sections) - 1:
                    result.append("\n")

            return result
        except Exception:
            return Text("  📊 journey: (parse error)", style=Style(dim=True, italic=True))
