"""TreeView 层级结构展示组件。

提供 TreeNode 数据结构和 TreeView 渲染组件。
支持四种缩进线风格（light/heavy/dashed/none）、展开/折叠控制、
路径列表构建树、窄屏自适应等特性。

使用示例::

    root = TreeNode("项目")
    src = root.add_child(TreeNode("src"))
    src.add_child(TreeNode("main.py"))
    src.add_child(TreeNode("utils.py"))

    tree = TreeView(root, line_style="light")
    print(tree.render())
    # 项目
    # └─ src
    #    ├─ main.py
    #    └─ utils.py

    # 从路径列表构建:
    root = TreeNode.from_paths([
        "src/main.py", "src/utils/helper.py", "tests/test_main.py"
    ])
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from ._base import TuiComponent


# ═══════════════════════════════════════════════════════════
# TreeNode — 树节点数据结构
# ═══════════════════════════════════════════════════════════

@dataclass
class TreeNode:
    """树节点数据结构。

    采用组合 (Composite) 设计模式：TreeNode 既是叶子节点也可容纳子节点。
    ``children`` 列表为空时即视为叶子节点。

    Attributes:
        label: 节点显示文本。
        children: 子节点列表，默认为空。
        icon: 前缀图标（如文件夹/文件 emoji），默认为空。
        expanded: 是否展开子节点，默认 True。
        metadata: 附加数据字典（如文件大小、修改时间等），默认为 None。
    """

    label: str
    children: list[TreeNode] = field(default_factory=list)
    icon: str = ""
    expanded: bool = True
    metadata: dict | None = None

    def add_child(self, child: TreeNode) -> TreeNode:
        """添加子节点并返回该子节点，支持链式调用。

        Args:
            child: 要添加的子节点。

        Returns:
            被添加的子节点，便于继续在其上添加子节点。

        Example::

            root = TreeNode("root")
            src = root.add_child(TreeNode("src"))
            src.add_child(TreeNode("main.py"))
            src.add_child(TreeNode("utils.py"))

            # 或链式：
            root.add_child(TreeNode("src")).add_child(TreeNode("main.py"))
        """
        self.children.append(child)
        return child

    @classmethod
    def from_paths(
        cls, paths: list[str], separator: str = "/"
    ) -> TreeNode:
        """从路径列表构建树结构。

        将扁平路径列表（如 ``["a/b/c", "a/b/d"]``）转换为嵌套 TreeNode 树。
        路径前缀共享的节点自动合并为共同父节点。

        Args:
            paths: 路径字符串列表。
            separator: 路径分隔符，默认 ``"/"``。

        Returns:
            树的根节点（label 为空字符串，作为虚拟根）。

        Example::

            root = TreeNode.from_paths(["src/main.py", "src/utils.py"])
            # root (虚拟根)
            # └─ src
            #    ├─ main.py
            #    └─ utils.py
        """
        root = cls(label="")
        for path in paths:
            parts = [p for p in path.split(separator) if p]
            if not parts:
                continue
            current = root
            for part in parts:
                # 查找是否已有同名子节点
                existing = next(
                    (c for c in current.children if c.label == part), None
                )
                if existing is None:
                    existing = cls(label=part)
                    current.children.append(existing)
                current = existing
        return root

    @property
    def is_leaf(self) -> bool:
        """是否为叶子节点（无子节点）。"""
        return len(self.children) == 0

    @property
    def child_count(self) -> int:
        """子节点数量。"""
        return len(self.children)


# ═══════════════════════════════════════════════════════════
# 缩进线字符集
# ═══════════════════════════════════════════════════════════

_TREE_CHARS: dict[str, dict[str, str]] = {
    "light": {
        "vertical": "│",
        "branch": "├─ ",
        "last_branch": "└─ ",
    },
    "heavy": {
        "vertical": "┃",
        "branch": "┣━ ",
        "last_branch": "┗━ ",
    },
    "dashed": {
        "vertical": "┆",
        "branch": "┠─ ",
        "last_branch": "┖─ ",
    },
    "none": {
        "vertical": " ",
        "branch": "  ",
        "last_branch": "  ",
    },
}

# 折叠节点前缀
_COLLAPSED_PREFIX = "▶ "


# ═══════════════════════════════════════════════════════════
# TreeView — 层级结构渲染组件
# ═══════════════════════════════════════════════════════════

class TreeView(TuiComponent):
    """层级结构渲染组件。

    将 TreeNode 树结构渲染为带缩进线和分支符号的文本。
    继承 TuiComponent 基类，可通过 render_to_adapter() 输出到 OutputAdapter。

    Args:
        root: 树的根节点。
        show_lines: 是否显示缩进线，默认 True。关闭后仅保留缩进空白。
        indent_width: 每级缩进宽度（空格数），默认 3，与分支符号 ``├─ `` 宽度匹配。
        line_style: 缩进线风格，可选 ``"light"`` / ``"heavy"`` / ``"dashed"`` / ``"none"``，
            默认 ``"light"``。
        max_depth: 最大展开深度，``None`` 表示无限制。
            达到最大深度时，未展开的子节点显示 ``(+N)`` 计数。
    """

    def __init__(
        self,
        root: TreeNode,
        show_lines: bool = True,
        indent_width: int = 3,
        line_style: str = "light",
        max_depth: int | None = None,
    ) -> None:
        super().__init__()
        self._root = root
        self._show_lines = show_lines
        self._indent_width = indent_width
        self._line_style = line_style if line_style in _TREE_CHARS else "light"
        self._max_depth = max_depth

    # ── 公共 API ────────────────────────────────────────────────────────

    def render(self) -> str:
        """渲染树结构为文本。

        Returns:
            带缩进线和分支符号的树形文本，每行以换行符分隔。
        """
        lines = self._render_tree(self._root)
        return "\n".join(lines)

    # ── 渲染核心 ────────────────────────────────────────────────────────

    def _render_tree(self, root: TreeNode) -> list[str]:
        """递归渲染树节点列表。

        Args:
            root: 根节点。

        Returns:
            渲染后的文本行列表。
        """
        narrow = self._is_narrow_mode()
        chars = self._get_chars(narrow)
        # 窄屏时缩进宽度减半
        indent_w = max(1, self._indent_width // 2) if narrow else self._indent_width

        lines: list[str] = []
        self._render_node(
            root,
            lines=lines,
            prefix="",
            is_last=True,
            depth=0,
            chars=chars,
            indent_w=indent_w,
            narrow=narrow,
        )
        return lines

    def _render_node(
        self,
        node: TreeNode,
        lines: list[str],
        prefix: str,
        is_last: bool,
        depth: int,
        chars: dict[str, str],
        indent_w: int,
        narrow: bool,
    ) -> None:
        """递归渲染单个节点及其子树。

        Args:
            node: 当前节点。
            lines: 累积输出的行列表（原地修改）。
            prefix: 当前行的缩进前缀（祖先层级竖线/空白）。
            is_last: 当前节点是否为父节点的最后一个子节点。
            depth: 当前深度（根节点 depth=0）。
            chars: 缩进线字符集。
            indent_w: 每级缩进宽度。
            narrow: 是否窄屏模式。
        """
        # ── 根节点特殊处理 ──
        if depth == 0:
            label = node.label if node.label else ""
            icon = "" if narrow else node.icon
            # 虚拟根节点（from_paths 创建的 label="" 且无 icon）跳过该行
            if label or icon:
                lines.append(f"{icon}{label}")
            # 渲染子节点
            for i, child in enumerate(node.children):
                child_is_last = (i == len(node.children) - 1)
                self._render_node(
                    child, lines, prefix="",
                    is_last=child_is_last, depth=1,
                    chars=chars, indent_w=indent_w, narrow=narrow,
                )
            return

        # ── 构建当前节点行 ──
        icon = "" if narrow else node.icon
        branch = chars["last_branch"] if is_last else chars["branch"]

        # 使用 StyleSheet 注册的语义色包裹分支符号和标签
        from ..core.style import StyleSheet
        branch_style = StyleSheet.get("tree_branch")
        leaf_style = StyleSheet.get("tree_leaf")

        styled_branch = (branch_style.apply(branch) if branch_style else branch)
        styled_label = (
            leaf_style.apply(f"{icon}{node.label}")
            if leaf_style and node.is_leaf
            else f"{icon}{node.label}"
        )

        line = f"{prefix}{styled_branch}{styled_label}"
        lines.append(line)

        # ── 到达最大深度：显示子节点计数 ──
        if self._max_depth is not None and depth >= self._max_depth:
            if not node.is_leaf:
                # 追加一行计数提示
                child_prefix = prefix + (
                    self._indent_blank(indent_w) if is_last
                    else self._indent_cont(chars, indent_w)
                )
                count_hint = f"{child_prefix}  (+{node.child_count})"
                lines.append(count_hint)
            return

        # ── 折叠节点 ──
        if not node.expanded:
            child_prefix = prefix + (
                self._indent_blank(indent_w) if is_last
                else self._indent_cont(chars, indent_w)
            )
            collapsed = f"{child_prefix}  {_COLLAPSED_PREFIX}({node.child_count})"
            lines.append(collapsed)
            return

        # ── 渲染子节点 ──
        for i, child in enumerate(node.children):
            child_is_last = (i == len(node.children) - 1)
            child_prefix = prefix + (
                self._indent_blank(indent_w) if is_last
                else self._indent_cont(chars, indent_w)
            )
            self._render_node(
                child, lines,
                prefix=child_prefix,
                is_last=child_is_last, depth=depth + 1,
                chars=chars, indent_w=indent_w, narrow=narrow,
            )

    # ── 辅助方法 ────────────────────────────────────────────────────────

    @staticmethod
    def _indent_cont(chars: dict[str, str], indent_w: int) -> str:
        """持续缩进（该层级后续还有兄弟节点）：
        竖线 + 空白填充。"""
        return chars["vertical"] + " " * (indent_w - 1)

    @staticmethod
    def _indent_blank(indent_w: int) -> str:
        """空白缩进（该层级已是最后一个节点）：
        全空白填充，无竖线。"""
        return " " * indent_w

    @staticmethod
    def _is_narrow_mode() -> bool:
        """检测当前是否为窄屏模式。"""
        try:
            from ..terminal.narrow import is_narrow
            return is_narrow()
        except (ImportError, ModuleNotFoundError):
            return False

    def _get_chars(self, narrow: bool) -> dict[str, str]:
        """获取缩进线字符集。

        窄屏或无缩进线模式使用 "none" 字符集。
        """
        if narrow or not self._show_lines:
            return _TREE_CHARS["none"]
        return _TREE_CHARS[self._line_style]


__all__ = [
    "TreeNode",
    "TreeView",
]
