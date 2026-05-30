"""_mermaid_sequence — Mermaid 时序图渲染 Mixin。"""

from __future__ import annotations

from rich.text import Text
from rich.style import Style

from ._mermaid_helpers import (
    _STYLE_BOX, _STYLE_NODE, _STYLE_EDGE_LABEL, _STYLE_ARROW,
    _STYLE_ACTOR, _STYLE_NOTE,
    _is_word_char, _extract_word_ids,
    _starts_with_ignore_case, _is_comment_line,
)


class MermaidSequenceMixin:
    """MermaidRenderer 时序图渲染 Mixin。"""

    @staticmethod
    def _parse_seq_msg(s):
        arrow_candidates = ['--x', '->>', '-->', '->', '-x']
        arrow_at = -1
        arrow_str = ""
        for a in arrow_candidates:
            idx = s.find(a)
            if idx != -1:
                if arrow_at == -1 or idx < arrow_at:
                    arrow_at = idx
                    arrow_str = a
        if arrow_at == -1:
            return None

        src_part = s[:arrow_at].strip()
        rest = s[arrow_at + len(arrow_str):].strip()

        dst_and_label = rest.split(':', 1)
        dst_part = dst_and_label[0].strip()
        label = dst_and_label[1].strip() if len(dst_and_label) > 1 else ""

        src_ids = _extract_word_ids(src_part)
        src = src_ids[-1] if src_ids else None
        dst_ids = _extract_word_ids(dst_part)
        dst = dst_ids[0] if dst_ids else None

        if src and dst:
            return src, arrow_str, dst, label
        return None

    @staticmethod
    def _parse_note_over(s):
        if not _starts_with_ignore_case(s.strip(), 'note over'):
            return None
        rest = s.strip()[9:].strip()
        parts = rest.split(':', 1)
        names_str = parts[0].strip() if parts else ""
        note_text = parts[1].strip() if len(parts) > 1 else ""
        names = [x.strip() for x in names_str.split(',') if x.strip()]
        return names, note_text

    @staticmethod
    def _parse_note_side(s):
        s_lower = s.strip().lower()
        side = None
        if s_lower.startswith('note right of '):
            side = 'right'
        elif s_lower.startswith('note left of '):
            side = 'left'
        if not side:
            return None
        prefix_len = len('note right of ') if side == 'right' else len('note left of ')
        rest = s.strip()[prefix_len:]
        parts = rest.split(':', 1)
        name = parts[0].strip() if parts else ""
        note_text = parts[1].strip() if len(parts) > 1 else ""
        return side, name, note_text

    @staticmethod
    def _parse_participant(s):
        s_lower = s.strip().lower()
        keyword = None
        rest = ""
        if s_lower.startswith('participant '):
            keyword = 'participant'
            rest = s.strip()[12:].strip()
        elif s_lower.startswith('actor '):
            keyword = 'actor'
            rest = s.strip()[6:].strip()
        if not keyword:
            return None
        as_idx = rest.lower().rfind(' as ')
        if as_idx != -1:
            name = rest[:as_idx].strip()
            alias = rest[as_idx + 4:].strip()
        else:
            name = rest
            alias = None
        return keyword, name, alias

    def _render_sequence(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
        parts: list[str] = []
        msgs: list[tuple[int, int, str, bool, str]] = []
        notes: list[tuple[int, int, str]] = []

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue

            m = self._parse_participant(s)
            if m:
                name = (m[2] or m[1]).strip()
                if name not in parts:
                    parts.append(name)
                continue

            m = self._parse_note_over(s)
            if m:
                names, note_text = m
                for n in names:
                    if n not in parts:
                        parts.append(n)
                nl = parts.index(names[0]) if names[0] in parts else 0
                nr = parts.index(names[-1]) if names[-1] in parts else 0
                notes.append((nl, nr, note_text))
                continue

            m = self._parse_note_side(s)
            if m:
                side, name, note_text = m
                if name not in parts:
                    parts.append(name)
                idx = parts.index(name)
                notes.append((idx, idx, note_text))
                continue

            m = self._parse_seq_msg(s)
            if m:
                src, arrow, dst, label = m
                for p in (src, dst):
                    if p not in parts:
                        parts.append(p)
                si, di = parts.index(src), parts.index(dst)
                is_dot = arrow.startswith('--')
                is_loss = 'x' in arrow.lower()
                atype = "loss" if is_loss else "arrow" if ">" in arrow else "line"
                msgs.append((si, di, atype, is_dot, label))
                continue

        if not parts:
            return Text("  📊 sequence: no participants", style=Style(dim=True, italic=True))

        cw = 14
        gap = 2

        def _render_life(active: set[int] | None = None) -> Text:
            t = Text("  ")
            active = active or set()
            for i in range(len(parts)):
                t.append(" " * gap)
                ch = "●" if i in active else "│"
                t.append(f"{ch:<{cw - 1}s}", style=_STYLE_BOX)
            return t

        result.append("  ")
        for p in parts:
            result.append(f"{' ' * gap}{p:<{cw}}", style=_STYLE_ACTOR)
        result.append("\n")
        result.append("  ")
        for _ in parts:
            result.append(f"{' ' * gap}{'─' * cw}", style=_STYLE_BOX)
        result.append("\n")

        result.append(_render_life())
        result.append("\n")

        for si, di, atype, is_dot, label in msgs:
            left, right = min(si, di), max(si, di)
            ch = "·" if is_dot else "─"
            head = "✖" if atype == "loss" else "▶"

            row = Text("  ")
            for i in range(len(parts)):
                pfx = " " * gap
                if i == si and i == di:
                    row.append(f"{pfx}┌{'─' * (cw - 3)}┐", style=_STYLE_BOX)
                elif i == si and si < di:
                    row.append(f"{pfx}{'│':<{cw - 1}s}", style=_STYLE_BOX)
                elif i == di and si < di:
                    row.append(f"{pfx}{ch * (cw - 2)}{head}", style=_STYLE_ARROW)
                elif i == si and si > di:
                    row.append(f"{pfx}{head}{ch * (cw - 2)}", style=_STYLE_ARROW)
                elif i == di and si > di:
                    row.append(f"{pfx}{'│':<{cw - 1}s}", style=_STYLE_BOX)
                elif left < i < right:
                    row.append(f"{pfx}{'│':<{cw - 1}s}", style=_STYLE_BOX)
                else:
                    row.append(f"{pfx}{'│':<{cw - 1}s}", style=_STYLE_BOX)
            result.append(row)
            result.append("\n")

            if label:
                row2 = Text("  ")
                for i in range(len(parts)):
                    pfx = " " * gap
                    if left < i < right:
                        row2.append(f"{pfx}{label:<{cw}}", style=_STYLE_EDGE_LABEL)
                    else:
                        row2.append(f"{pfx}{' ' * cw}")
                result.append(row2)
                result.append("\n")

            result.append(_render_life())
            result.append("\n")

        for nl, nr, text in notes:
            row = Text("  ")
            for i in range(len(parts)):
                pfx = " " * gap
                if i == nl == nr:
                    row.append(f"{pfx}┌{text:<{cw - 2}s}┐", style=_STYLE_NOTE)
                elif i == nl:
                    row.append(f"{pfx}┌{'─' * (cw - 3)}┐", style=_STYLE_NOTE)
                elif i == nr and nr > nl:
                    row.append(f"{pfx}│{text:<{cw - 2}s}│", style=_STYLE_NOTE)
                elif nl < i < nr:
                    row.append(f"{pfx}│{' ' * (cw - 2)}│", style=_STYLE_NOTE)
                else:
                    row.append(f"{pfx}{' ' * cw}")
            result.append(row)
            result.append("\n")

        return result
