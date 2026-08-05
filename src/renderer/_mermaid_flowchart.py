"""_mermaid_flowchart — Mermaid 流程图渲染 Mixin。

包含 Flowchart/Graph 图表的解析与渲染方法。
"""

from __future__ import annotations

from collections import deque

from rich.text import Text
from rich.style import Style

from ._mermaid_helpers import (
    _STYLE_EDGE_LABEL, _STYLE_ARROW,
    _STYLE_SUBGRAPH,
    _extract_word_ids,
    _is_comment_line,
    _parse_node_shape, _is_subgraph_start, _is_subgraph_end,
    _extract_subgraph_title,
)


class MermaidFlowchartMixin:
    """MermaidRenderer 流程图渲染 Mixin。"""

    def _render_flowchart(self, lines: list[str]) -> Text:
        result = Text()
        result.append("\n")
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

        # ── 提取所有节点声明 ──
        for raw_line in lines[1:]:
            s = raw_line.strip()
            if not s or _is_comment_line(s) or _is_subgraph_start(s) or _is_subgraph_end(s):
                continue
            for nid, display, shape in _parse_node_shape(s):
                _ensure(nid, display, shape)

        # ── 提取边 ──
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
            bare = ' '.join(bare.split()).strip()

            label = ""
            edge_src = edge_dst = None

            edge_src, label, edge_dst = self._parse_edge_with_label(bare)
            if not edge_src:
                edge_src, edge_dst = self._parse_edge_arrow(bare)
            if not edge_src:
                edge_src, edge_dst = self._parse_edge_line(bare)

            if edge_src and edge_dst:
                _ensure(edge_src)
                _ensure(edge_dst)
                style = "arrow"
                if "==" in s:
                    style = "thick"
                elif ".-" in s or "-." in s or ".." in s:
                    style = "dotted"
                edges.append((edge_src, edge_dst, label, style))

            all_ids = _extract_word_ids(s)
            if cur_sg_name is not None:
                for nid in all_ids:
                    if nid in nodes:
                        cur_sg_nodes.add(nid)

        if cur_sg_name is not None:
            subgraphs.append((cur_sg_name, cur_sg_nodes))

        if not nodes:
            return Text("  📊 flowchart: no nodes", style=Style(dim=True, italic=True))

        # ── 拓扑排序（Kahn BFS 算法）──
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

        # ── 子图标题 ──
        for sg_title, sg_nodes_set in subgraphs:
            if sg_nodes_set:
                result.append(f"  📁 {sg_title}", style=_STYLE_SUBGRAPH)
                result.append("\n")

        if is_lr:
            self._render_lr(result, order, nodes, shapes, edges)
        else:
            self._render_td(result, order, nodes, shapes, edges)

        return result

    @staticmethod
    def _strip_shape_markers(s):
        """字符级剥离形状标记 [(...)] [...] {...} (...)。"""
        result = []
        i = 0
        n = len(s)
        while i < n:
            if i + 1 < n and s[i:i+2] == '[(':
                j = i + 2
                while j < n and s[j] != ')':
                    j += 1
                if j < n and j + 1 < n and s[j+1] == ']':
                    i = j + 2
                    continue
            if s[i] == '{':
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if s[j] == '{':
                        depth += 1
                    elif s[j] == '}':
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            if s[i] == '(':
                j = i + 1
                depth = 1
                while j < n and depth > 0:
                    if s[j] == '(':
                        depth += 1
                    elif s[j] == ')':
                        depth -= 1
                    j += 1
                if depth == 0:
                    i = j
                    continue
            if s[i] == '[' and (i + 1 >= n or s[i+1] != '('):
                j = i + 1
                while j < n and s[j] != ']':
                    j += 1
                if j < n:
                    i = j + 1
                    continue
            result.append(s[i])
            i += 1
        return ''.join(result)

    @staticmethod
    def _parse_edge_with_label(text):
        """解析 A -->|label| B 格式的边，返回 (src, label, dst)。"""
        pipe_start = text.find('|')
        if pipe_start == -1:
            return None, "", None
        pipe_end = text.find('|', pipe_start + 1)
        if pipe_end == -1:
            return None, "", None
        label = text[pipe_start + 1:pipe_end]
        left_part = text[:pipe_start]
        left_ids = _extract_word_ids(left_part)
        src = left_ids[-1] if left_ids else None
        right_part = text[pipe_end + 1:]
        right_ids = _extract_word_ids(right_part)
        dst = right_ids[0] if right_ids else None
        return src, label, dst

    @staticmethod
    def _parse_edge_arrow(text):
        """解析 A --> B / A ---> B / A ==> B / A -.-> B 格式，返回 (src, dst)。"""
        for arrow in ['==>', '-->', '->']:
            idx = text.find(arrow)
            if idx != -1:
                left = text[:idx]
                right = text[idx + len(arrow):]
                left_ids = _extract_word_ids(left)
                right_ids = _extract_word_ids(right)
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, dst
        return None, None

    @staticmethod
    def _parse_edge_line(text):
        """解析 A --- B / A === B 格式（无箭头），返回 (src, dst)。"""
        for sep in ['===', '---']:
            idx = text.find(sep)
            if idx != -1:
                left = text[:idx]
                right = text[idx + len(sep):]
                left_ids = _extract_word_ids(left)
                right_ids = _extract_word_ids(right)
                src = left_ids[-1] if left_ids else None
                dst = right_ids[0] if right_ids else None
                if src and dst:
                    return src, dst
        return None, None

    def _render_td(self, result, order, nodes, shapes, edges):
        """上→下布局。"""
        for i, nid in enumerate(order):
            text = nodes.get(nid, nid)
            shape = shapes.get(nid, "square")
            for line in self._make_box(text, shape):
                result.append(f"  {line}")
                result.append("\n")
            if i < len(order) - 1:
                nxt = order[i + 1]
                label, style = self._find_label(edges, nid, nxt)
                ch = "═" if style == "thick" else ("┅" if style == "dotted" else "─")
                result.append(f"    {ch * 3}▼", style=_STYLE_ARROW)
                result.append("\n")
                if label:
                    result.append(f"    {label}", style=_STYLE_EDGE_LABEL)
                    result.append("\n")

    def _render_lr(self, result, order, nodes, shapes, edges):
        """左→右布局。"""
        for i, nid in enumerate(order):
            text = nodes.get(nid, nid)
            shape = shapes.get(nid, "square")
            for line in self._make_box(text, shape):
                result.append(f"  {line}")
                result.append("\n")
            if i < len(order) - 1:
                nxt = order[i + 1]
                label, style = self._find_label(edges, nid, nxt)
                ch = "═" if style == "thick" else ("┅" if style == "dotted" else "─")
                result.append(f"    {ch * 4}▶", style=_STYLE_ARROW)
                result.append("\n")
                if label:
                    result.append(f"    {label}", style=_STYLE_EDGE_LABEL)
                    result.append("\n")

    @staticmethod
    def _make_box(text: str, shape: str = "square") -> list[str]:
        display = text if text else " "
        width = max(len(display) + 2, 4)
        if shape == "round":
            return [f" ╭{'─' * (width - 2)}╮ ",
                    f" │ {display:<{width - 4}s} │ ",
                    f" ╰{'─' * (width - 2)}╯ "]
        elif shape == "diamond":
            pad = " " * ((width - 2) // 2)
            return [f"  {pad}◇{pad}  ",
                    f" ╱{' ' * (width - 2)}╲ ",
                    f" │ {display:<{width - 4}s} │ ",
                    f" ╲{' ' * (width - 2)}╱ "]
        elif shape == "cylinder":
            top = f" ┌{'─' * (width - 2)}┐ "
            mid = f" │ {display:<{width - 4}s} │ "
            bot = f" └{'─' * (width - 2)}┘ "
            return [top, mid, mid, bot]
        return [f" ┌{'─' * (width - 2)}┐ ",
                f" │ {display:<{width - 4}s} │ ",
                f" └{'─' * (width - 2)}┘ "]

    @staticmethod
    def _find_label(edges, src, dst) -> tuple[str, str]:
        for s, d, label, style in edges:
            if s == src and d == dst:
                return label, style
        return "", "arrow"
