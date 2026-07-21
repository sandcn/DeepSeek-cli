"""ast.types — AST 节点类型体系：NodeType 枚举 + ASTNode 数据类 + SourceRange。

为流式 Markdown 渲染提供树形中间表示，替代扁平 Token 流。
每个 ASTNode 代表一个 Markdown 结构单元（块/行/内联），
children 体现嵌套关系，content 为叶子文本。
"""

from __future__ import annotations

from dataclasses import field
from src._compat import dataclass
from enum import Enum, auto
from typing import Optional


# ═══════════════════════════════════════════════════════════
# NodeType 枚举
# ═══════════════════════════════════════════════════════════

class NodeType(Enum):
    """AST 节点类型枚举。

    按 Markdown 块级结构组织：
      DOCUMENT       — 文档根节点
      SECTION        — 标题+附属内容组
      块级           — PARAGRAPH / HEADING / HR / EMPTY_LINE / BLOCKQUOTE
      列表           — LIST / ORDERED_LIST / LIST_ITEM / DEFINITION_ITEM
      代码           — CODE_BLOCK
      数学           — MATH_BLOCK
      图表           — MERMAID_BLOCK
      表格           — TABLE / TABLE_ROW / TABLE_CELL
      折叠           — DETAILS
      告示           — ADMONITION
      HTML块         — HTML_BLOCK / HTML_LINE
      内联（叶子）   — TEXT
    """

    # ── 文档结构 ────────────────────────────────────────
    DOCUMENT = auto()
    """文档根节点，全局唯一。"""

    SECTION = auto()
    """标题+附属内容组（Heading 下的连续块）。"""

    # ── 块级基础 ────────────────────────────────────────
    PARAGRAPH = auto()
    """普通段落。"""

    HEADING = auto()
    """标题（level 1-6）。"""

    HR = auto()
    """分隔线。"""

    EMPTY_LINE = auto()
    """空行。"""

    # ── 引用 ────────────────────────────────────────────
    BLOCKQUOTE = auto()
    """嵌套引用块（depth 表示嵌套深度）。"""

    # ── 列表 ────────────────────────────────────────────
    LIST = auto()
    """无序列表（连续 bullet LIST_ITEM 的父节点）。"""

    ORDERED_LIST = auto()
    """有序列表（连续 numbered LIST_ITEM 的父节点）。"""

    LIST_ITEM = auto()
    """列表项（depth 表示嵌套深度，bullet 表示是否无序）。"""

    DEFINITION_ITEM = auto()
    """定义列表项（term + definition）。"""

    # ── 代码 ────────────────────────────────────────────
    CODE_BLOCK = auto()
    """代码块（lang, title, attrs, highlight_lines 在 meta 中）。"""

    # ── 数学 ────────────────────────────────────────────
    MATH_BLOCK = auto()
    """数学公式块（source 在 meta 中）。"""

    # ── Mermaid ─────────────────────────────────────────
    MERMAID_BLOCK = auto()
    """Mermaid 图表块。"""

    # ── 表格 ────────────────────────────────────────────
    TABLE = auto()
    """表格。"""

    TABLE_ROW = auto()
    """表格行（作为 TABLE 的子节点）。"""

    TABLE_CELL = auto()
    """表格单元格（作为 TABLE_ROW 的子节点）。"""

    # ── 折叠 ────────────────────────────────────────────
    DETAILS = auto()
    """折叠块（<details><summary>...）。"""

    # ── 告示 ────────────────────────────────────────────
    ADMONITION = auto()
    """告示块（> [!NOTE/WARNING/...]）。"""

    # ── HTML块 ──────────────────────────────────────────
    HTML_BLOCK = auto()
    """HTML 块级元素（<div>/<pre>/<table> 等）。"""

    HTML_LINE = auto()
    """HTML 块内单行内容（作为 HTML_BLOCK 的子节点）。"""

    # ── 内联（叶子） ────────────────────────────────────
    TEXT = auto()
    """纯文本/内联内容（叶子节点，出现在块级节点的 content 中）。"""


# ═══════════════════════════════════════════════════════════
# SourceRange
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class SourceRange:
    """源码范围（用于调试定位和增量更新 diff）。

    Attributes:
        start: 起始字符偏移（从文档头算起）
        end: 结束字符偏移（开区间）
        line_start: 起始行号（从1开始）
        line_end: 结束行号（闭区间）
    """
    start: int = 0
    end: int = 0
    line_start: int = 0
    line_end: int = 0

    def __repr__(self):
        return (f"SourceRange(L{self.line_start}:{self.start}"
                f"-L{self.line_end}:{self.end})")


# ═══════════════════════════════════════════════════════════
# ASTNode 数据类
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class ASTNode:
    """AST 节点——Markdown 结构树中的基本单元。

    每个节点代表一个 Markdown 结构：
    - 内部节点（有 children）：SECTION, LIST, TABLE, DETAILS, ADMONITION, BLOCKQUOTE 等
    - 叶子节点（无 children，content 有值）：PARAGRAPH, HEADING, HR, CODE_BLOCK 等
    - 混合节点：BLOCKQUOTE（可有子节点深度嵌套，也有自己的 content）

    Attributes:
        type: 节点类型（NodeType 枚举）
        children: 子节点列表（内部节点用）
        content: 文本内容（叶子节点用，或作为 meta 的补充）
        meta: 元数据字典（按节点类型存放不同属性）
        range: 源码范围（可选，用于调试和增量更新）
    """
    type: NodeType
    children: list[ASTNode] = field(default_factory=list)
    content: str = ""
    meta: dict = field(default_factory=dict)
    range: SourceRange | None = None

    # ── 树操作 ──────────────────────────────────────────

    def add_child(self, child: ASTNode) -> None:
        """添加子节点到末尾。"""
        self.children.append(child)

    def find(self, node_type: NodeType) -> list[ASTNode]:
        """递归查找所有指定类型的子节点（DFS）。"""
        results = []
        for child in self.children:
            if child.type is node_type:
                results.append(child)
            results.extend(child.find(node_type))
        return results

    def find_first(self, node_type: NodeType) -> Optional[ASTNode]:
        """查找第一个指定类型的子节点（DFS）。"""
        for child in self.children:
            if child.type is node_type:
                return child
            found = child.find_first(node_type)
            if found is not None:
                return found
        return None

    # ── 序列化 ──────────────────────────────────────────

    def to_dict(self) -> dict:
        """递归转换为字典（用于调试/序列化/json 导出）。"""
        d: dict = {
            "type": self.type.name,
        }
        if self.content:
            d["content"] = self.content
        if self.meta:
            d["meta"] = dict(self.meta)
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.range:
            d["range"] = {
                "start": self.range.start,
                "end": self.range.end,
                "line_start": self.range.line_start,
                "line_end": self.range.line_end,
            }
        return d

    @staticmethod
    def from_dict(d: dict) -> ASTNode:
        """从字典递归构建 ASTNode。"""
        node = ASTNode(
            type=NodeType[d["type"]],
            content=d.get("content", ""),
            meta=d.get("meta", {}),
        )
        if "range" in d:
            node.range = SourceRange(**d["range"])
        for child_dict in d.get("children", []):
            node.add_child(ASTNode.from_dict(child_dict))
        return node

    # ── 调试输出 ────────────────────────────────────────

    def dump(self, indent: int = 0) -> str:
        """以缩进树形式打印 AST（调试用）。"""
        prefix = "  " * indent
        parts = [f"{prefix}{self.type.name}"]
        if self.content:
            parts[-1] += f"  {self.content[:50]!r}"
        if self.meta:
            meta_str = ", ".join(f"{k}={v}" for k, v in self.meta.items()
                                 if k not in ("rows",))
            if meta_str:
                parts[-1] += f"  [{meta_str}]"
        for child in self.children:
            parts.append(child.dump(indent + 1))
        return "\n".join(parts)

    def __repr__(self):
        if self.children:
            return (f"ASTNode({self.type.name}, "
                    f"children={len(self.children)}, "
                    f"content={self.content[:30]!r})")
        return (f"ASTNode({self.type.name}, "
                f"content={self.content[:40]!r})")
