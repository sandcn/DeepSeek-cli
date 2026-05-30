"""vnode.types — VNode 渲染树类型体系 + VPatch 补丁类型。

VNode（虚拟节点）是 AST 和最终渲染输出之间的中间表示层，
专注于渲染属性和增量更新追踪，与 ASTNode（语义结构）职责分离。

设计原则：
  - VNode 比 ASTNode 更扁平（聚焦渲染，不含嵌套语义）
  - 每个 VNode 有稳定 key，用于 diff 追踪身份
  - VPatch 描述两棵 VNode 树之间的差异
  - 渲染结果可缓存在 VNode.rendered 中避免重复计算
"""

from __future__ import annotations

from dataclasses import field
from src._compat import dataclass
from enum import Enum, auto
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════
# VNodeType 枚举
# ═══════════════════════════════════════════════════════════

class VNodeType(Enum):
    """VNode 渲染节点类型。

    与 ASTNode.NodeType 对应，但更聚焦渲染形态：
    - 代码块拆为 CODE_FENCE + CODE_LINE×N + CODE_FENCE（支持行级增量）
    - 列表展平为扁平序列（避免嵌套 diff 复杂度）
    - 内联格式统一为 TEXT（内联已由 _render_inline 处理）
    """

    # ── 文档根 ────────────────────────────────────────
    ROOT = auto()
    """渲染树根节点。"""

    # ── 块级 ──────────────────────────────────────────
    PARAGRAPH = auto()
    """段落。"""

    HEADING = auto()
    """标题（props.level 1-6）。"""

    HR = auto()
    """分隔线。"""

    EMPTY = auto()
    """空行。"""

    # ── 引用 ──────────────────────────────────────────
    BLOCKQUOTE = auto()
    """引用块（props.depth 嵌套深度）。"""

    # ── 列表 ──────────────────────────────────────────
    LIST_ITEM = auto()
    """列表项（props.bullet/number/depth）。"""

    DEFINITION_ITEM = auto()
    """定义列表项。"""

    # ── 代码块（行级增量） ────────────────────────────
    CODE_FENCE = auto()
    """代码块围栏线（```lang 或 📄）。"""

    CODE_LINE = auto()
    """代码行（props.line_number, props.lang 用于高亮）。"""

    # ── 数学块 ────────────────────────────────────────
    MATH = auto()
    """数学公式块。"""

    # ── Mermaid ───────────────────────────────────────
    MERMAID = auto()
    """Mermaid 图表块。"""

    # ── 表格 ──────────────────────────────────────────
    TABLE = auto()
    """表格（props.rows/alignments）。"""

    # ── 折叠块 ────────────────────────────────────────
    DETAILS = auto()
    """折叠块（props.summary）。"""

    # ── 告示 ──────────────────────────────────────────
    ADMONITION = auto()
    """告示块（props.type/depth）。"""

    # ── HTML块 ────────────────────────────────────────
    HTML_BLOCK = auto()
    """HTML 块级元素（props.tag）。"""

    HTML_LINE = auto()
    """HTML 块内行。"""


# ═══════════════════════════════════════════════════════════
# VNode 数据类
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class VNode:
    """虚拟渲染节点——渲染树的构建单元。

    Attributes:
        type: 节点类型
        key: 稳定标识，用于 diff 追踪（如 'p:0', 'cb:3', 'cl:0:5'）
        content: 文本内容（已解析内联格式的纯文本）
        props: 渲染属性字典
        children: 子节点列表
        rendered: 缓存的 Rich renderable 对象（避免重复计算）
        line_count: 此节点在终端中的输出行数（用于行覆盖定位）
    """
    type: VNodeType
    key: str = ""
    content: str = ""
    props: dict = field(default_factory=dict)
    children: list[VNode] = field(default_factory=list)
    rendered: Any = None
    line_count: int = 0

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def add_child(self, child: VNode) -> None:
        self.children.append(child)

    def to_dict(self) -> dict:
        return {
            "type": self.type.name,
            "key": self.key,
            "content": self.content,
            "props": dict(self.props),
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self):
        return f"VNode({self.type.name}, key={self.key!r}, content={self.content[:30]!r})"


# ═══════════════════════════════════════════════════════════
# PatchType 枚举 + VPatch 数据类
# ═══════════════════════════════════════════════════════════

class PatchType(Enum):
    """补丁类型——描述两棵 VNode 树之间的差异操作。"""

    INSERT = auto()
    """插入新节点（新树中有，旧树中无）。"""

    UPDATE = auto()
    """更新节点（新旧树中 key 相同，但 content/props 变化）。"""

    DELETE = auto()
    """删除节点（旧树中有，新树中无）。"""

    MOVE = auto()
    """移动节点（位置变化）。"""

    REORDER = auto()
    """子节点重新排序（位置变化不影响 key 匹配）。"""


@dataclass(slots=True)
class VPatch:
    """虚拟补丁——描述对渲染树的一个原子修改操作。

    Attributes:
        type: 补丁类型
        key: 目标节点的 key
        path: 从根到目标节点的路径索引（如 (0, 2, 1)）
        node: 关联的 VNode（INSERT 时为新节点，UPDATE 时为带新属性的节点）
        old_content: 旧内容（UPDATE 时使用）
        new_content: 新内容（UPDATE 时使用）
        old_props: 旧属性（UPDATE 时使用）
        new_props: 新属性（UPDATE 时使用）
        index: 在兄弟节点中的位置（INSERT/MOVE 时使用）
    """
    type: PatchType
    key: str
    path: tuple[int, ...] = ()
    node: Optional[VNode] = None
    old_content: str = ""
    new_content: str = ""
    old_props: dict = field(default_factory=dict)
    new_props: dict = field(default_factory=dict)
    index: int = -1

    def __repr__(self):
        return (f"VPatch({self.type.name}, key={self.key!r}, "
                f"path={self.path})")


# ═══════════════════════════════════════════════════════════
# VNodeDiff 结果
# ═══════════════════════════════════════════════════════════

@dataclass(slots=True)
class VNodeDiffResult:
    """VNode 差异对比结果。

    Attributes:
        patches: 补丁列表（按应用顺序排列）
        stats: 各类型补丁的数量统计
    """
    patches: list[VPatch] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=lambda: {
        "insert": 0, "update": 0, "delete": 0, "move": 0, "reorder": 0,
    })

    def add(self, patch: VPatch) -> None:
        self.patches.append(patch)
        key = patch.type.name.lower()
        self.stats[key] = self.stats.get(key, 0) + 1

    def has_changes(self) -> bool:
        return len(self.patches) > 0

    def __repr__(self):
        parts = [f"{k}={v}" for k, v in self.stats.items() if v > 0]
        return f"VNodeDiffResult({', '.join(parts)}, total={len(self.patches)})"
