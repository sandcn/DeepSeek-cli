"""_mermaid_render — Mermaid 图表 → AnsiLine 终端渲染（零 Rich 依赖）。

原 ``ansi/mermaid.py`` 仅以等宽纯文本框展示 Mermaid 源码（首版退化）。本模块
提供真正的 ASCII/Unicode 图形渲染，覆盖 11 类图：

  流程图 / 时序图 / 类图 / 状态图 / 甘特图 / 饼图 / ER 图 / Git 提交图 /
  思维导图 / 时间线 / 用户旅程图

与 Rich 路径（``renderer.mermaid_renderer``）**共享字符级解析**（``_mermaid_parse``），
布局语义一致；输出 ``AnsiLine`` 并按显示宽度对齐（CJK/emoji 安全）。

性能：渲染结果按源码缓存在 ``ansi/mermaid.py`` 层上（流式预览逐帧重渲时
源码未变即零成本）。
"""

from __future__ import annotations

from collections import deque

from .style import Style
from .helpers import AnsiLine
from src.renderer._utils import cjk_display_width
from src.renderer._mermaid_parse import (
    _extract_word_ids,
    _is_comment_line,
    _parse_node_shape,
    _is_subgraph_start,
    _is_subgraph_end,
    _extract_subgraph_title,
    _starts_with_ignore_case,
)


# ── 样式常量（ANSI；语义与 Rich 路径一致） ────────────────
_ST_BOX = Style(fg=241, dim=True)
_ST_NODE = Style(fg=231, bold=True)
_ST_EDGE_LABEL = Style(fg=245, italic=True, dim=True)
_ST_HEADER = Style(fg=51, bold=True)
_ST_ARROW = Style(fg=245, dim=True)
_ST_ACTOR = Style(fg=41, bold=True)
_ST_NOTE = Style(fg=220, italic=True, dim=True)
_ST_SUBGRAPH = Style(fg=201, bold=True)
_ST_FIELD = Style(fg=240, dim=True)
_ST_METHOD = Style(fg=33)
_ST_RELATION = Style(fg=51, dim=True)


def _pad(text: str, width: int, align: str = "left") -> str:
    """按显示宽度填充文本（CJK/emoji 安全）。"""
    w = cjk_display_width(text)
    gap = width - w
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def _dw(text: str) -> int:
    return cjk_display_width(text)


class _Builder:
    """带样式的多行构建器（``append`` 文本可含换行）。"""

    __slots__ = ("lines",)

    def __init__(self) -> None:
        self.lines: list[AnsiLine] = [AnsiLine()]

    def append(self, text, style: Style | None = None) -> "_Builder":
        if text is None:
            return self
        s = str(text)
        if not s:
            return self
        parts = s.split("\n")
        for idx, p in enumerate(parts):
            if idx:
                self.lines.append(AnsiLine())
            if p:
                self.lines[-1].append(p, style)
        return self

    def __call__(self, text, style=None) -> "_Builder":
        return self.append(text, style)

    def done(self) -> list[AnsiLine]:
        lines = self.lines
        while len(lines) > 1 and not lines[-1].runs:
            lines.pop()
        return lines


def _make_box(text: str, shape: str = "square") -> list[str]:
    """节点框（显示宽度对齐）。"""
    display = text if text else " "
    inner = max(_dw(display), 2)
    top = "┌" + "─" * (inner + 2) + "┐"
    mid = "│ " + _pad(display, inner) + " │"
    bot = "└" + "─" * (inner + 2) + "┘"
    if shape == "round":
        top = "╭" + "─" * (inner + 2) + "╮"
        bot = "╰" + "─" * (inner + 2) + "╯"
        return [top, mid, bot]
    if shape == "diamond":
        pad = " " * ((inner + 2) // 2)
        return [" " + pad + "◇" + pad + " ",
                " ╱" + " " * (inner + 2) + "╲ ",
                mid,
                " ╲" + " " * (inner + 2) + "╱ "]
    if shape == "cylinder":
        return [top, mid, mid, bot]
    return [top, mid, bot]


class _MermaidRenderer:
    """Mermaid 源码 → AnsiLine 列表。"""

    # ── 分派 ──────────────────────────────────────────

    def render(self, source: str) -> list[AnsiLine]:
        if not source or not source.strip():
            return [AnsiLine.of("📊 (空图表)", _ST_EDGE_LABEL)]
        lines = [ln.rstrip() for ln in source.split("\n") if ln.strip()]
        if not lines:
            return [AnsiLine.of("📊 (空图表)", _ST_EDGE_LABEL)]
        first = lines[0].strip()
        low = first.lower()
        try:
            if low.startswith("graph ") or low.startswith("flowchart "):
                return self._render_flowchart(lines)
            if low.startswith("sequencediagram"):
                return self._render_sequence(lines)
            if low.startswith("classdiagram"):
                return self._render_class(lines)
            if low.startswith("statediagram"):
                return self._render_state(lines)
            if low.startswith("gantt"):
                return self._render_gantt(lines)
            if low.startswith("pie"):
                return self._render_pie(lines)
            if low.startswith("erdiagram"):
                return self._render_er(lines)
            if low.startswith("gitgraph"):
                return self._render_gitgraph(lines)
            if low.startswith("mindmap"):
                return self._render_mindmap(lines)
            if low.startswith("timeline"):
                return self._render_timeline(lines)
            if low.startswith("journey"):
                return self._render_journey(lines)
        except Exception:
            return [AnsiLine.of("📊 Mermaid 渲染失败，显示源码", _ST_EDGE_LABEL)] + [
                AnsiLine.of("  " + ln, _ST_EDGE_LABEL) for ln in lines
            ]
        return self._render_fallback(lines)

    def _render_fallback(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        first = lines[0].strip() if lines else ""
        type_name = first.split()[0] if first else "diagram"
        b.append("  📊 " + type_name, _ST_HEADER)
        b.append("\n")
        for line in lines:
            s = line.strip()
            if s:
                b.append("  │ " + s, _ST_EDGE_LABEL)
                b.append("\n")
        return b.done()

    @staticmethod
    def _expand_semicolons(lines: list[str]) -> list[str]:
        """把 Mermaid 语句分隔符 ``;`` 拆成独立行（支持 ``graph TD; A-->B`` 单行写法）。

        仅拆分顶层（不在 ``[]``/``()``/``{}``/``""`` 内）的分号，避免破坏节点
        标签与边标签中的分号。
        """
        out: list[str] = []
        for idx, line in enumerate(lines):
            if ";" not in line:
                out.append(line)
                continue
            parts: list[str] = []
            cur: list[str] = []
            depth = 0
            quote = ""
            for ch in line:
                if quote:
                    cur.append(ch)
                    if ch == quote:
                        quote = ""
                    continue
                if ch in "\"'":
                    quote = ch
                    cur.append(ch)
                elif ch in "[({":
                    depth += 1
                    cur.append(ch)
                elif ch in "])}":
                    depth = max(0, depth - 1)
                    cur.append(ch)
                elif ch == ";" and depth == 0:
                    parts.append("".join(cur))
                    cur = []
                else:
                    cur.append(ch)
            parts.append("".join(cur))
            for part in parts:
                stripped = part.strip()
                if stripped:
                    out.append(stripped)
        # 若首行被拆分后为空（不应发生），回退原样
        return out if out else list(lines)

    # ═══════════════════════════════════════════════════
    # 流程图
    # ═══════════════════════════════════════════════════

    def _render_flowchart(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        lines = self._expand_semicolons(lines)
        first = lines[0].strip()
        is_lr = "LR" in first.upper() or "RL" in first.upper()

        nodes: dict[str, str] = {}
        shapes: dict[str, str] = {}
        subgraphs: list[tuple[str, set[str]]] = []
        edges: list[tuple[str, str, str, str]] = []

        cur_sg_name: str | None = None
        cur_sg_nodes: set[str] = set()

        def _ensure(nid: str, text: str | None = None, shape: str = "square"):
            if nid not in nodes:
                nodes[nid] = text or nid
                shapes[nid] = shape
            elif text is not None and nodes[nid] == nid:
                nodes[nid] = text
                shapes[nid] = shape

        for raw_line in lines[1:]:
            s = raw_line.strip()
            if not s or _is_comment_line(s) or _is_subgraph_start(s) or _is_subgraph_end(s):
                continue
            for nid, display, shape in _parse_node_shape(s):
                _ensure(nid, display, shape)

        for raw_line in lines[1:]:
            s = raw_line.strip()
            if not s or _is_comment_line(s):
                continue
            if _is_subgraph_start(s):
                cur_sg_name = _extract_subgraph_title(s)
                cur_sg_nodes = set()
                continue
            if _is_subgraph_end(s):
                if cur_sg_name is not None:
                    subgraphs.append((cur_sg_name, cur_sg_nodes))
                cur_sg_name = None
                continue
            bare = self._strip_shape_markers(s)
            bare = " ".join(bare.split()).strip()
            e_src, e_label, e_dst = self._parse_edge_with_label(bare)
            if not e_src:
                e_src, e_label, e_dst = self._parse_edge_arrow(bare)
            if not e_src:
                e_src, e_label, e_dst = self._parse_edge_line(bare)
            if e_src and e_dst:
                _ensure(e_src)
                _ensure(e_dst)
                style = "arrow"
                if "==" in s:
                    style = "thick"
                elif ".-" in s or "-." in s or ".." in s:
                    style = "dotted"
                edges.append((e_src, e_dst, e_label, style))
            all_ids = _extract_word_ids(s)
            if cur_sg_name is not None:
                for nid in all_ids:
                    if nid in nodes:
                        cur_sg_nodes.add(nid)
        if cur_sg_name is not None:
            subgraphs.append((cur_sg_name, cur_sg_nodes))

        if not nodes:
            return [AnsiLine.of("  📊 flowchart: 无节点", _ST_EDGE_LABEL)]

        in_deg: dict[str, int] = {n: 0 for n in nodes}
        adj: dict[str, list[str]] = {n: [] for n in nodes}
        for src, dst, _, _ in edges:
            if src in adj and dst in adj:
                adj[src].append(dst)
                in_deg[dst] = in_deg.get(dst, 0) + 1
        q = deque([n for n in nodes if in_deg.get(n, 0) == 0])
        order: list[str] = []
        while q:
            n = q.popleft()
            order.append(n)
            for nb in adj.get(n, []):
                in_deg[nb] -= 1
                if in_deg[nb] == 0:
                    q.append(nb)
        for n in nodes:
            if n not in order:
                order.append(n)

        for sg_title, sg_nodes_set in subgraphs:
            if sg_nodes_set:
                b.append("  📁 " + sg_title, _ST_SUBGRAPH)
                b.append("\n")

        self._render_flow_order(b, order, nodes, shapes, edges, is_lr)
        return b.done()

    @staticmethod
    def _strip_shape_markers(s: str) -> str:
        result: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            if i + 1 < n and s[i:i + 2] == "[(":
                j = i + 2
                while j < n and s[j] != ")":
                    j += 1
                if j < n and j + 1 < n and s[j + 1] == "]":
                    i = j + 2
                    continue
            if s[i] == "{":
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if s[j] == "{":
                        depth += 1
                    elif s[j] == "}":
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            if s[i] == "(":
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if s[j] == "(":
                        depth += 1
                    elif s[j] == ")":
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            if s[i] == "[" and (i + 1 >= n or s[i + 1] != "("):
                j = i + 1
                while j < n and s[j] != "]":
                    j += 1
                if j < n:
                    i = j + 1
                    continue
            result.append(s[i])
            i += 1
        return "".join(result)

    @staticmethod
    def _parse_edge_with_label(text: str) -> tuple[str | None, str, str | None]:
        pipe_start = text.find("|")
        if pipe_start == -1:
            return None, "", None
        pipe_end = text.find("|", pipe_start + 1)
        if pipe_end == -1:
            return None, "", None
        label = text[pipe_start + 1:pipe_end]
        left_ids = _extract_word_ids(text[:pipe_start])
        right_ids = _extract_word_ids(text[pipe_end + 1:])
        return (left_ids[-1] if left_ids else None, label,
                right_ids[0] if right_ids else None)

    @staticmethod
    def _parse_edge_arrow(text: str) -> tuple[str | None, str, str | None]:
        for arrow in ("==>", "-->", "->"):
            idx = text.find(arrow)
            if idx != -1:
                left_ids = _extract_word_ids(text[:idx])
                right_ids = _extract_word_ids(text[idx + len(arrow):])
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, "", dst
        return None, "", None

    @staticmethod
    def _parse_edge_line(text: str) -> tuple[str | None, str, str | None]:
        for sep in ("===", "---"):
            idx = text.find(sep)
            if idx != -1:
                left_ids = _extract_word_ids(text[:idx])
                right_ids = _extract_word_ids(text[idx + len(sep):])
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, "", dst
        return None, "", None

    @staticmethod
    def _find_label(edges, src, dst) -> tuple[str, str]:
        for s, d, label, style in edges:
            if s == src and d == dst:
                return label, style
        return "", "arrow"

    def _render_flow_order(self, b, order, nodes, shapes, edges, is_lr: bool) -> None:
        for i, nid in enumerate(order):
            text = nodes.get(nid, nid)
            shape = shapes.get(nid, "square")
            for line in _make_box(text, shape):
                b.append("  " + line)
                b.append("\n")
            if i < len(order) - 1:
                nxt = order[i + 1]
                label, style = self._find_label(edges, nid, nxt)
                ch = "═" if style == "thick" else ("┅" if style == "dotted" else "─")
                if is_lr:
                    b.append("    " + ch * 4 + "▶", _ST_ARROW)
                else:
                    b.append("    " + ch * 3 + "▼", _ST_ARROW)
                b.append("\n")
                if label:
                    b.append("    " + label, _ST_EDGE_LABEL)
                    b.append("\n")

    # ═══════════════════════════════════════════════════
    # 时序图
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_seq_msg(s: str):
        arrow_at = -1
        arrow_str = ""
        for a in ("--x", "->>", "-->", "->", "-x"):
            idx = s.find(a)
            if idx != -1 and (arrow_at == -1 or idx < arrow_at):
                arrow_at = idx
                arrow_str = a
        if arrow_at == -1:
            return None
        src_part = s[:arrow_at].strip()
        rest = s[arrow_at + len(arrow_str):].strip()
        dst_label = rest.split(":", 1)
        dst_part = dst_label[0].strip()
        label = dst_label[1].strip() if len(dst_label) > 1 else ""
        src_ids = _extract_word_ids(src_part)
        dst_ids = _extract_word_ids(dst_part)
        src = src_ids[-1] if src_ids else None
        dst = dst_ids[0] if dst_ids else None
        if src and dst:
            return src, arrow_str, dst, label
        return None

    @staticmethod
    def _parse_note_over(s: str):
        if not _starts_with_ignore_case(s.strip(), "note over"):
            return None
        rest = s.strip()[9:].strip()
        parts = rest.split(":", 1)
        names_str = parts[0].strip() if parts else ""
        note_text = parts[1].strip() if len(parts) > 1 else ""
        names = [x.strip() for x in names_str.split(",") if x.strip()]
        return names, note_text

    @staticmethod
    def _parse_note_side(s: str):
        low = s.strip().lower()
        if low.startswith("note right of "):
            side, plen = "right", len("note right of ")
        elif low.startswith("note left of "):
            side, plen = "left", len("note left of ")
        else:
            return None
        rest = s.strip()[plen:]
        parts = rest.split(":", 1)
        name = parts[0].strip() if parts else ""
        note_text = parts[1].strip() if len(parts) > 1 else ""
        return side, name, note_text

    @staticmethod
    def _parse_participant(s: str):
        low = s.strip().lower()
        if low.startswith("participant "):
            rest = s.strip()[12:].strip()
        elif low.startswith("actor "):
            rest = s.strip()[6:].strip()
        else:
            return None
        as_idx = rest.lower().rfind(" as ")
        if as_idx != -1:
            return rest[:as_idx].strip(), rest[as_idx + 4:].strip()
        return rest, None

    def _render_sequence(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        parts: list[str] = []
        msgs: list[tuple] = []
        notes: list[tuple] = []

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_participant(s)
            if m:
                name = (m[1] or m[0]).strip()
                if name not in parts:
                    parts.append(name)
                continue
            m = self._parse_note_over(s)
            if m:
                names, note_text = m
                if not names:
                    continue
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
                if side == "left" and idx > 0:
                    notes.append((idx - 1, idx, note_text))
                elif side == "right" and idx + 1 < len(parts):
                    notes.append((idx, idx + 1, note_text))
                else:
                    notes.append((idx, idx, note_text))
                continue
            m = self._parse_seq_msg(s)
            if m:
                src, arrow, dst, label = m
                for p in (src, dst):
                    if p not in parts:
                        parts.append(p)
                si, di = parts.index(src), parts.index(dst)
                is_dot = arrow.startswith("--")
                atype = "loss" if "x" in arrow.lower() else ("arrow" if ">" in arrow else "line")
                msgs.append((si, di, atype, is_dot, label))
                continue

        if not parts:
            return [AnsiLine.of("  📊 sequence: 无参与者", _ST_EDGE_LABEL)]

        cw = 14
        gap = 2

        def _life(active=None) -> None:
            active = active or set()
            b.append("  ")
            for i in range(len(parts)):
                b.append(" " * gap)
                b.append(_pad("●" if i in active else "│", cw - 1), _ST_BOX)
            b.append("\n")

        b.append("  ")
        for p in parts:
            b.append(" " * gap)
            b.append(_pad(p, cw), _ST_ACTOR)
        b.append("\n")
        b.append("  ")
        for _ in parts:
            b.append(" " * gap + "─" * cw, _ST_BOX)
        b.append("\n")
        _life()

        for si, di, atype, is_dot, label in msgs:
            left, right = min(si, di), max(si, di)
            ch = "·" if is_dot else "─"
            head_ch = "✖" if atype == "loss" else "▶"
            b.append("  ")
            for i in range(len(parts)):
                pfx = " " * gap
                if i == si and i == di:
                    b.append(pfx + "┌" + "─" * (cw - 3) + "┐", _ST_BOX)
                elif i == si and si < di:
                    b.append(pfx + _pad("│", cw - 1), _ST_BOX)
                elif i == di and si < di:
                    b.append(pfx + ch * (cw - 2) + head_ch, _ST_ARROW)
                elif i == si and si > di:
                    b.append(pfx + head_ch + ch * (cw - 2), _ST_ARROW)
                else:
                    b.append(pfx + _pad("│", cw - 1), _ST_BOX)
            b.append("\n")
            if label:
                b.append("  ")
                placed = False
                for i in range(len(parts)):
                    pfx = " " * gap
                    if left < i < right:
                        b.append(pfx + _pad(label, cw), _ST_EDGE_LABEL)
                        placed = True
                    else:
                        b.append(pfx + " " * cw)
                if not placed:
                    b.append(label, _ST_EDGE_LABEL)
                b.append("\n")
            _life()

        for nl, nr, text in notes:
            b.append("  ")
            for i in range(len(parts)):
                pfx = " " * gap
                if i == nl == nr:
                    b.append(pfx + "┌" + _pad(text, cw - 2) + "┐", _ST_NOTE)
                elif i == nl:
                    b.append(pfx + "┌" + "─" * (cw - 3) + "┐", _ST_NOTE)
                elif i == nr and nr > nl:
                    b.append(pfx + "│" + _pad(text, cw - 2) + "│", _ST_NOTE)
                elif nl < i < nr:
                    b.append(pfx + "│" + " " * (cw - 2) + "│", _ST_NOTE)
                else:
                    b.append(pfx + " " * cw)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 类图
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_class_decl(s: str):
        if _starts_with_ignore_case(s.strip(), "class "):
            rest = s.strip()[6:].strip()
            name = rest.split()[0] if rest else ""
            return name or None
        return None

    @staticmethod
    def _parse_class_rel(s: str):
        for sym in ("<|--", "*--", "o--", "-->", "--|", "..>", "..|>"):
            idx = s.find(sym)
            if idx != -1:
                left_ids = _extract_word_ids(s[:idx].strip())
                right_ids = _extract_word_ids(s[idx + len(sym):].strip())
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, sym, dst
        return None

    def _render_class(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        classes: dict[str, list[str]] = {}
        relations: list[tuple] = []
        current: str | None = None

        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_class_decl(s)
            if m:
                current = m
                classes.setdefault(m, [])
                continue
            m = self._parse_class_rel(s)
            if m:
                src, rel, dst = m
                relations.append((src, rel, dst))
                for c in (src, dst):
                    classes.setdefault(c, [])
                continue
            if ":" in s:
                cname, _, field = s.partition(":")
                cname, field = cname.strip(), field.strip()
                if cname in classes:
                    classes[cname].append(field)
                    current = cname
                elif current and field:
                    classes.setdefault(current, []).append(s)
                continue
            if current and s and s not in ("{", "}"):
                classes.setdefault(current, []).append(s)

        if not classes:
            return [AnsiLine.of("  📊 class diagram: 无类", _ST_EDGE_LABEL)]

        for cname, members in classes.items():
            inner = max(_dw(cname), 12)
            for m in members:
                inner = max(inner, _dw(m))
            inner = min(inner, 36)
            b.append("  ┌" + "─" * (inner + 2) + "┐", _ST_BOX)
            b.append("\n")
            b.append("  │ " + _pad(cname, inner) + " │", _ST_NODE)
            b.append("\n")
            if members:
                b.append("  ├" + "─" * (inner + 2) + "┤", _ST_BOX)
                b.append("\n")
                for member in members:
                    st = _ST_METHOD if ("(" in member and ")" in member) else _ST_FIELD
                    b.append("  │ " + _pad(member, inner) + " │", st)
                    b.append("\n")
            b.append("  └" + "─" * (inner + 2) + "┘", _ST_BOX)
            b.append("\n\n")

        sym = {"<|--": "◁─", "*--": "◆─", "o--": "○─",
               "-->": "─▶", "--|": "─▷", "..>": "·▶", "..|>": "·▷"}
        for src, rel, dst in relations:
            b.append(f"  {src} {sym.get(rel, rel)} {dst}", _ST_RELATION)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 状态图
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_state_edge(s: str):
        idx = s.find("-->")
        if idx == -1:
            return None
        left = s[:idx].strip()
        right = s[idx + 3:].strip()
        if left in ("[*]", "*"):
            src = "*"
        else:
            ids = _extract_word_ids(left)
            src = ids[-1] if ids else left
        label = ""
        c = right.find(":")
        if c != -1:
            label = right[c + 1:].strip()
            right = right[:c].strip()
        if right in ("[*]", "*"):
            dst = "*"
        else:
            ids = _extract_word_ids(right)
            dst = ids[0] if ids else right
        return src, dst, label

    def _render_state(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        states: set[str] = set()
        trans: list[tuple] = []
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
            return [AnsiLine.of("  📊 state: 无状态", _ST_EDGE_LABEL)]
        if any(s == "*" for s, _, _ in trans):
            b.append("  ● (初始)", _ST_EDGE_LABEL)
            b.append("\n\n")
        for st in sorted(states):
            inner = max(_dw(st), 4)
            b.append("  ┌" + "─" * (inner + 2) + "┐", _ST_BOX)
            b.append("\n")
            b.append("  │" + _pad(st, inner + 2, "center") + "│", _ST_NODE)
            b.append("\n")
            b.append("  └" + "─" * (inner + 2) + "┘", _ST_BOX)
            b.append("\n")
            for src, dst, label in trans:
                if src == st:
                    b.append("    ──▶ ", _ST_ARROW)
                    if dst == "*":
                        b.append("● (终止)", _ST_EDGE_LABEL)
                    else:
                        b.append(dst, _ST_NODE)
                    if label:
                        b.append(f"  [{label}]", _ST_EDGE_LABEL)
                    b.append("\n")
            b.append("\n")
        if any(d == "*" for _, d, _ in trans):
            b.append("  ● (终止)", _ST_EDGE_LABEL)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 甘特图
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _extract_duration(detail: str):
        for p in reversed(detail.split(",")):
            p = p.strip()
            num = ""
            for ch in p:
                if ch.isdigit():
                    num += ch
                elif num:
                    break
            if num:
                return int(num)
        return None

    def _render_gantt(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        title = ""
        sections: list[tuple] = []
        cur_name, cur_tasks = "", []
        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            low = s.lower()
            if low.startswith("title "):
                title = s[6:].strip()
                continue
            if low.startswith("dateformat"):
                continue
            if low.startswith("section "):
                if cur_tasks:
                    sections.append((cur_name, cur_tasks))
                cur_name, cur_tasks = s[8:].strip(), []
                continue
            parts = s.split(":")
            if len(parts) >= 2:
                name = parts[0].strip()
                duration = self._extract_duration(parts[1].strip())
                if duration is not None and name:
                    cur_tasks.append((name, duration))
        if cur_tasks:
            sections.append((cur_name, cur_tasks))
        if not sections:
            return [AnsiLine.of("  📊 gantt: 无任务", _ST_EDGE_LABEL)]

        all_durations = [d for _, tasks in sections for _, d in tasks]
        max_dur = max(all_durations) if all_durations else 1
        bar_max = 20
        if title:
            b.append("  📅 " + title, _ST_HEADER)
            b.append("\n")
            b.append("  " + "─" * min(len(title) + 4, 30), _ST_EDGE_LABEL)
            b.append("\n")
        for sec_idx, (sec_name, tasks) in enumerate(sections):
            if sec_name:
                b.append("  📁 " + sec_name, _ST_SUBGRAPH)
                b.append("\n")
            for name, duration in tasks:
                bar_len = max(1, round(duration / max_dur * bar_max))
                b.append("    " + _pad(name, 12) + " " + _pad("█" * bar_len, bar_max)
                         + f"  {duration}d", _ST_NODE)
                b.append("\n")
            if sec_idx < len(sections) - 1:
                b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 饼图
    # ═══════════════════════════════════════════════════

    def _render_pie(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        title = ""
        data: list[tuple] = []
        for line in lines:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            low = s.lower()
            if low.startswith("pie title "):
                title = s[10:].strip()
                continue
            if low.startswith("pie"):
                continue
            if ":" in s:
                label_raw, _, value_raw = s.partition(":")
                label = label_raw.strip().strip('"').strip("'").strip()
                try:
                    data.append((label, float(value_raw.strip())))
                except ValueError:
                    pass
        if not data:
            return [AnsiLine.of("  🥧 pie: 无数据", _ST_EDGE_LABEL)]
        total = sum(v for _, v in data)
        if total == 0:
            return [AnsiLine.of("  🥧 (零数据)", _ST_EDGE_LABEL)]
        if title:
            b.append("  🥧 " + title, _ST_HEADER)
            b.append("\n")
            b.append("  " + "─" * 20, _ST_EDGE_LABEL)
            b.append("\n")
        bar_max = 20
        for label, value in data:
            pct = value / total * 100
            bar_len = max(1, round(pct / 100 * bar_max))
            b.append("  " + _pad(label, 8) + " " + _pad("█" * bar_len, bar_max)
                     + f"  {pct:.1f}%", _ST_NODE)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # ER 图
    # ═══════════════════════════════════════════════════

    @staticmethod
    def _parse_er_rel(s: str):
        c = s.find(":")
        if c == -1:
            return None
        left = s[:c].strip()
        label = s[c + 1:].strip().strip('"').strip("'").strip()
        parts = left.split()
        if len(parts) >= 3:
            return parts[0], parts[1], parts[-1], label
        return None

    def _render_er(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        rels: list[tuple] = []
        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            m = self._parse_er_rel(s)
            if m:
                rels.append(m)
        if not rels:
            return [AnsiLine.of("  📊 er: 无关系", _ST_EDGE_LABEL)]
        for e1, rel_sym, e2, label in rels:
            w1 = max(_dw(e1), 8)
            w2 = max(_dw(e2), 8)
            rel_ch = (rel_sym or "").replace("-", "─")[:5].ljust(5, "─")
            b.append("  ┌" + "─" * (w1 + 2) + "┐     ┌" + "─" * (w2 + 2) + "┐", _ST_BOX)
            b.append("\n")
            b.append("  │ " + _pad(e1, w1, "center") + " │" + rel_ch + "│ "
                     + _pad(e2, w2, "center") + " │", _ST_NODE)
            b.append("\n")
            b.append("  └" + "─" * (w1 + 2) + "┘     └" + "─" * (w2 + 2) + "┘", _ST_BOX)
            b.append("\n")
            edge = f"{rel_sym}  {label}".rstrip() if label else (rel_sym or "")
            b.append("       " + edge, _ST_EDGE_LABEL)
            b.append("\n\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # Git 提交图
    # ═══════════════════════════════════════════════════

    def _render_gitgraph(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
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
                steps.append(("merge", s[6:].strip(), current))
        if not steps:
            return [AnsiLine.of("  📊 gitgraph: 无提交", _ST_EDGE_LABEL)]

        col_w = max(12, max(_dw(x) + 2 for x in branches))
        b.append("  ")
        for x in branches:
            b.append(_pad(x, col_w), _ST_ACTOR)
        b.append("\n")
        b.append("  ")
        for _ in branches:
            b.append("─" * col_w, _ST_BOX)
        b.append("\n")

        active: set[str] = {"main"}
        for step in steps:
            b.append("  ")
            if step[0] == "commit":
                _, cur = step
                for x in branches:
                    if x == cur:
                        b.append(_pad("●", col_w, "center"), _ST_NODE)
                    elif x in active:
                        b.append(_pad("│", col_w, "center"), _ST_BOX)
                    else:
                        b.append(" " * col_w)
            elif step[0] == "branch":
                _, name = step
                active.add(name)
                for x in branches:
                    if x in active:
                        b.append(_pad("│", col_w, "center"), _ST_BOX)
                    else:
                        b.append(" " * col_w)
            elif step[0] == "merge":
                _, src_name, dst_name = step
                src_idx = branch_col.get(src_name, 0)
                dst_idx = branch_col.get(dst_name, 0)
                left_idx, right_idx = min(src_idx, dst_idx), max(src_idx, dst_idx)
                chars = [" "] * (col_w * len(branches))
                for i, x in enumerate(branches):
                    if x in active:
                        mid = i * col_w + col_w // 2
                        chars[mid] = "●" if i in (dst_idx, src_idx) else "│"
                if left_idx != right_idx:
                    lc = left_idx * col_w + col_w // 2
                    rc = right_idx * col_w + col_w // 2
                    for p in range(lc + 1, rc):
                        chars[p] = "─"
                    if dst_idx < src_idx:
                        if lc + 1 < rc:
                            chars[lc + 1] = "◀"
                    elif rc - 1 > lc:
                        chars[rc - 1] = "▶"
                joined = "".join(chars)
                for i in range(len(branches)):
                    seg = joined[i * col_w:(i + 1) * col_w]
                    st = _ST_NODE if i in (dst_idx, src_idx) else _ST_BOX
                    b.append(_pad(seg, col_w), st)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 思维导图
    # ═══════════════════════════════════════════════════

    def _render_mindmap(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        items: list[tuple] = []
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
            if len(text) >= 4 and text[:2] == "((" and text[-2:] == "))":
                shape, display = "double_circle", text[2:-2]
            elif len(text) >= 2 and text[0] == "[" and text[-1] == "]":
                shape, display = "square", text[1:-1]
            elif len(text) >= 2 and text[0] == "(" and text[-1] == ")":
                shape, display = "round", text[1:-1]
            items.append((level, display, shape))
        if not items:
            return [AnsiLine.of("  📊 mindmap: 空", _ST_EDGE_LABEL)]

        def _has_sibling(idx: int) -> bool:
            cur_level = items[idx][0]
            for j in range(idx + 1, len(items)):
                if items[j][0] < cur_level:
                    return False
                if items[j][0] == cur_level:
                    return True
            return False

        root_level = items[0][0]
        b.append("  🌳 " + items[0][1], _ST_HEADER)
        b.append("\n")
        level_has_more: dict[int, bool] = {}
        for idx, (level, display, shape) in enumerate(items[1:], start=1):
            pref = ""
            for lvl in range(1, level - root_level):
                pref += "│  " if level_has_more.get(lvl, False) else "   "
            has_more = _has_sibling(idx)
            level_has_more[level - root_level] = has_more
            pref += "├─ " if has_more else "└─ "
            if shape == "double_circle":
                display_text = "◎ " + display
            elif shape == "square":
                display_text = "▢ " + display
            elif shape == "round":
                display_text = "◯ " + display
            else:
                display_text = display
            b.append("  " + pref, _ST_BOX)
            b.append(display_text, _ST_NODE)
            b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 时间线
    # ═══════════════════════════════════════════════════

    def _render_timeline(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        title = ""
        entries: list[tuple] = []
        for line in lines[1:]:
            s = line.strip()
            if not s or _is_comment_line(s):
                continue
            if s.lower().startswith("title "):
                title = s[6:].strip()
                continue
            parts = [p.strip() for p in s.split(":")]
            if parts and parts[0]:
                entries.append((parts[0], parts[1:]))
        if not entries and not title:
            return [AnsiLine.of("  📊 timeline: 空", _ST_EDGE_LABEL)]
        if title:
            b.append("  📅 " + title, _ST_HEADER)
            b.append("\n\n")
        for idx, (tp, events) in enumerate(entries):
            b.append("  " + tp, _ST_HEADER)
            b.append("\n")
            for event in events:
                if event:
                    b.append("    ●── ", _ST_ARROW)
                    b.append(event, _ST_NODE)
                    b.append("\n")
            if idx < len(entries) - 1:
                b.append("    │", _ST_BOX)
                b.append("\n")
                b.append("    │", _ST_BOX)
                b.append("\n")
        return b.done()

    # ═══════════════════════════════════════════════════
    # 用户旅程图
    # ═══════════════════════════════════════════════════

    def _render_journey(self, lines: list[str]) -> list[AnsiLine]:
        b = _Builder()
        title = ""
        sections: list[tuple] = []
        cur_name, cur_tasks = "", []
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
                    sections.append((cur_name, cur_tasks))
                cur_name, cur_tasks = s[8:].strip(), []
                continue
            parts = [p.strip() for p in s.split(":")]
            if len(parts) >= 2:
                name = parts[0]
                try:
                    score = int(parts[1])
                except ValueError:
                    score = 3
                role = parts[2] if len(parts) >= 3 else "用户"
                if name:
                    cur_tasks.append((name, max(1, min(5, score)), role))
        if cur_tasks:
            sections.append((cur_name, cur_tasks))
        if not sections and not title:
            return [AnsiLine.of("  📊 journey: 空", _ST_EDGE_LABEL)]
        bar_max = 15
        if title:
            b.append("  🧭 " + title, _ST_HEADER)
            b.append("\n\n")
        for sec_idx, (sec_name, tasks) in enumerate(sections):
            if sec_name:
                b.append("  📁 " + sec_name, _ST_SUBGRAPH)
                b.append("\n")
            for name, score, role in tasks:
                bar_len = max(1, round(score / 5 * bar_max))
                b.append("    " + _pad(name, 10) + " ", _ST_NODE)
                b.append(_pad("█" * bar_len, bar_max), _ST_ARROW)
                b.append(f"  {score}  ", _ST_EDGE_LABEL)
                b.append(role_emoji.get(role, "🔘") + role, _ST_EDGE_LABEL)
                b.append("\n")
            if sec_idx < len(sections) - 1:
                b.append("\n")
        return b.done()


_RENDERER = _MermaidRenderer()


def render_mermaid(source: str) -> list[AnsiLine]:
    """Mermaid 源码 → AnsiLine 列表（异常时降级为源码展示）。"""
    return _RENDERER.render(source)


__all__ = ["render_mermaid", "_MermaidRenderer"]
