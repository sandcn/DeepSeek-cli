"""Tree — 通用树控件。

TuiComponent 子类，支持任意 TreeNode 树的声明式渲染，
产出 Unicode box-drawing 树形文本。提供 ASCII 回退方案。

使用示例:
    root = TreeNode("Root", children=[
        TreeNode("Child 1", status="done"),
        TreeNode("Child 2", children=[
            TreeNode("Grandchild", status="running"),
        ]),
    ])
    tree = Tree(root)
    print(tree.render())
    # ⏺ Root
    # ├── ✓ Child 1
    # └── ⏺ Child 2
    #     └── ⏺ Grandchild
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .base import TuiComponent

if TYPE_CHECKING:
    from ..vdom.vnode import VNode


# ── 状态图标映射 ──────────────────────────────────────

_STATUS_ICONS: dict[str, str] = {
    "running": "⏺",
    "done": "✓",
    "fail": "✗",
}

# Unicode box-drawing 字符
_UNICODE_CONNECTORS = {
    "pipe":     "│",
    "tee":      "├",
    "corner":   "└",
    "dash":     "── ",
    "space":    "   ",
    "pipe_space": "│  ",
}

# ASCII 回退字符
_ASCII_CONNECTORS = {
    "pipe":     "|",
    "tee":      "|",
    "corner":   "\\",
    "dash":     "-- ",
    "space":    "   ",
    "pipe_space": "|  ",
}


# ── TreeNode 数据结构 ──────────────────────────────────

@dataclass(frozen=True)
class TreeNode:
    """树节点。

    Attributes:
        label: 节点标签文本。
        status: 节点状态，合法值 "running" / "done" / "fail"，默认 "running"。
        children: 子节点列表。
        metadata: 附加元数据字典（如 tokens, elapsed 等），留给消费者扩展。
        is_expanded: 是否展开子节点，默认 True。False 时子节点不渲染。
    """
    label: str
    status: str = "running"
    children: list["TreeNode"] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    is_expanded: bool = True

    _VALID_STATUSES = frozenset({"running", "done", "fail"})

    def __post_init__(self) -> None:
        if self.status not in self._VALID_STATUSES:
            raise ValueError(
                f"TreeNode status 必须为 {sorted(self._VALID_STATUSES)}，"
                f"实际: {self.status!r}"
            )


# ── Tree 组件 ──────────────────────────────────────────


class Tree(TuiComponent):
    """通用树控件。

    递归渲染 TreeNode 树，使用 Unicode box-drawing 字符绘制连接线。
    支持 ASCII 回退模式和多级缩进。

    使用示例:
        root = TreeNode("Root", children=[
            TreeNode("Child", status="done"),
        ])
        tree = Tree(root, indent=2)
        print(tree.render())

    Attributes:
        root: 根节点（None 时渲染空字符串）。
        indent: 缩进宽度（空格数），默认 2。
        ascii_fallback: True 时使用 ASCII 连接线。
    """

    def __init__(
        self,
        root: TreeNode | None = None,
        indent: int = 2,
        ascii_fallback: bool = False,
        children: list[TuiComponent] | None = None,
    ):
        """初始化树控件。

        Args:
            root: 根节点，None 时 render() 返回空字符串。
            indent: 缩进宽度（空格数），默认 2。
            ascii_fallback: True 时使用 ASCII 回退连接线。
            children: 子组件列表（TuiComponent 协议兼容）。
        """
        super().__init__(children=children)
        self.root: TreeNode | None = root
        self._indent: int = indent
        if self._indent < 1 or self._indent > 8:
            raise ValueError(f"indent 必须在 1-8 之间，实际: {self._indent}")
        self._ascii: bool = ascii_fallback

    # ── 基类方法 ──

    @property
    def key(self) -> str:
        return "tree"

    def update(self, props: dict) -> bool:
        changed = False
        if "root" in props and props["root"] is not self.root:
            self.root = props["root"]
            changed = True
        if "indent" in props:
            new_indent = int(props["indent"])
            if new_indent != self._indent:
                self._indent = new_indent
                changed = True
        if "ascii_fallback" in props and props["ascii_fallback"] != self._ascii:
            self._ascii = bool(props["ascii_fallback"])
            changed = True
        return changed

    def render_vnode(self) -> "VNode":
        from ..vdom.vnode import VNode
        return VNode(
            type="tree",
            key=self.key,
            props={
                "text": self.render(),
            },
        )

    def render(self) -> str:
        """递归渲染整棵树。

        Returns:
            Unicode box-drawing 树形文本字符串。root 为 None 时返回 ""。
        """
        if self.root is None:
            return ""
        lines: list[str] = []
        connectors = _ASCII_CONNECTORS if self._ascii else _UNICODE_CONNECTORS

        # 动态构建 indent 感知的连接器组件
        dash_char = "-" if self._ascii else "─"
        dash_count = max(1, self._indent - 2)      # 至少 1 个 dash
        dash_str = dash_char * dash_count + " "     # 宽度 = dash_count + 1
        pipe_cont = connectors["pipe"] + " " * (self._indent - 1)  # 宽度 = indent
        spacer = " " * self._indent                 # 宽度 = indent

        # 空 label 根节点（如 subagent_tree 产出的容器节点）：跳过根，直接从 children 渲染
        root = self.root
        if root.label == "" and root.children:
            for i, child in enumerate(root.children):
                is_last = (i == len(root.children) - 1)
                self._render_node(child, "", is_last, True, lines, connectors,
                                  dash_str, pipe_cont, spacer)
        else:
            self._render_node(root, "", True, True, lines, connectors,
                              dash_str, pipe_cont, spacer)

        return "\n".join(lines)

    # ── 内部递归渲染 ──

    def _render_node(
        self,
        node: TreeNode,
        prefix: str,
        is_last: bool,
        is_root: bool,
        lines: list[str],
        connectors: dict[str, str],
        dash_str: str,
        pipe_cont: str,
        spacer: str,
    ) -> None:
        """递归渲染单个节点及其子节点。

        Args:
            node: 当前节点。
            prefix: 前缀字符串（祖先节点累积的缩进和连接线）。
            is_last: 当前节点是否为父节点的最后一个子节点（影响连接线形状）。
            is_root: 是否为根节点（根节点不绘制连接线前缀）。
            lines: 累积的行列表。
            connectors: 连接线字符集。
            dash_str: indent 感知的 dash 字符串。
            pipe_cont: indent 感知的 pipe 延续字符串。
            spacer: indent 感知的空白间隔字符串。
        """
        # ── 组装当前行 ──
        icon = _STATUS_ICONS.get(node.status, " ")

        if is_root:
            # 根节点：前缀为空
            line = f"{icon} {node.label}"
        else:
            connector = connectors["corner"] if is_last else connectors["tee"]
            line = f"{prefix}{connector}{dash_str}{icon} {node.label}"

        lines.append(line)

        # ── 子节点 ──
        if not node.is_expanded or not node.children:
            return

        child_count = len(node.children)
        for i, child in enumerate(node.children):
            is_last_child = (i == child_count - 1)

            if is_root:
                # 根节点的子节点：前缀为空
                child_prefix = ""
            elif is_last:
                # 父节点为末子节点：空白间隔（无 pipe 延续线）
                child_prefix = prefix + spacer
            else:
                # 父节点非末子节点：pipe 延续线
                child_prefix = prefix + pipe_cont

            self._render_node(
                child, child_prefix, is_last_child, False, lines, connectors,
                dash_str, pipe_cont, spacer,
            )
