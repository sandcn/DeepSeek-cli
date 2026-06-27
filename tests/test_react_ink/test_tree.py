"""Tree 组件单元测试。

覆盖 TreeNode 构造、字段默认值、status 校验、单/多层级树渲染、
Unicode/ASCII 连接线、展开折叠、缩进配置、空树、TuiComponent 继承、
frozen 约束、key 属性和 VNode 产出。
"""

from __future__ import annotations

import pytest
from dataclasses import FrozenInstanceError

from src.chat_ui.react_ink import Tree, TreeNode
from src.chat_ui.components.base import TuiComponent
from src.chat_ui.vdom.vnode import VNode


# ═══════════════════════════════════════════════════════════
# TestTreeNode
# ═══════════════════════════════════════════════════════════

class TestTreeNode:
    """TreeNode 数据结构测试。"""

    def test_tree_node_construction(self):
        """构造 TreeNode，验证字段默认值和赋值。"""
        node = TreeNode(label="root")
        assert node.label == "root"
        assert node.status == "running"
        assert node.children == []
        assert node.metadata == {}
        assert node.is_expanded is True

    def test_tree_node_all_fields_explicit(self):
        """所有字段显式赋值。"""
        child = TreeNode(label="child", status="done")
        node = TreeNode(
            label="parent",
            status="fail",
            children=[child],
            metadata={"tokens": 123},
            is_expanded=False,
        )
        assert node.label == "parent"
        assert node.status == "fail"
        assert node.children == [child]
        assert node.metadata == {"tokens": 123}
        assert node.is_expanded is False

    def test_tree_node_invalid_status(self):
        """非法 status 值引发 ValueError。"""
        with pytest.raises(ValueError, match="status"):
            TreeNode(label="bad", status="unknown")

    def test_tree_node_frozen(self):
        """TreeNode 是 frozen dataclass，修改属性引发 FrozenInstanceError。"""
        node = TreeNode(label="frozen")
        with pytest.raises(FrozenInstanceError):
            node.label = "new_label"  # type: ignore[misc]

    def test_tree_node_frozen_status(self):
        """修改 frozen 节点的 status 引发 FrozenInstanceError。"""
        node = TreeNode(label="frozen", status="running")
        with pytest.raises(FrozenInstanceError):
            node.status = "done"  # type: ignore[misc]

    def test_tree_node_frozen_children(self):
        """修改 frozen 节点的 children 引发 FrozenInstanceError。"""
        node = TreeNode(label="frozen")
        with pytest.raises(FrozenInstanceError):
            node.children = []  # type: ignore[misc]

    def test_tree_node_not_hashable_due_to_list_children(self):
        """TreeNode 含 list 字段 children，不可哈希。

        frozen dataclass 的 __hash__ 依赖所有字段可哈希，
        children: list 不可哈希，因此 TreeNode 整体不可哈希。
        """
        node = TreeNode(label="h")
        with pytest.raises(TypeError, match="unhashable"):
            hash(node)


# ═══════════════════════════════════════════════════════════
# TestTreeRendering
# ═══════════════════════════════════════════════════════════

class TestTreeRendering:
    """Tree 渲染测试。"""

    def test_tree_single_node(self):
        """单节点树渲染，验证含状态图标和 label。"""
        root = TreeNode(label="root", status="running")
        tree = Tree(root)
        output = tree.render()

        assert "⏺" in output
        assert "root" in output
        # 单节点应只有一行
        lines = output.split("\n")
        assert len(lines) == 1

    def test_tree_two_level(self):
        """1 父 + 2 子，验证 ├─ 和 └─ 连接线存在。"""
        root = TreeNode("root", children=[
            TreeNode("child1", status="done"),
            TreeNode("child2", status="fail"),
        ])
        tree = Tree(root)
        output = tree.render()

        # indent=2 → dash 为 "─ "（1 dash + space），connector 为 "├─ " / "└─ "
        assert "├─ " in output
        assert "└─ " in output
        # 共 3 行
        lines = output.split("\n")
        assert len(lines) == 3

    def test_tree_three_level(self):
        """三层嵌套，验证缩进逐层递增。"""
        root = TreeNode("A", children=[
            TreeNode("B", children=[
                TreeNode("C"),
            ]),
        ])
        tree = Tree(root)
        output = tree.render()
        lines = output.split("\n")

        # 第 1 行：根节点 A，无前缀/缩进
        assert "A" in lines[0]
        # 第 2 行：子节点 B，应有 └─ 前缀（indent=2 → dash 为 "─ "）
        assert "B" in lines[1]
        assert "└─ " in lines[1]
        # 第 3 行：孙节点 C，缩进应比 B 更深
        assert "C" in lines[2]

        # C 行应有延续线前缀（│ └─），B 行无延续线（直接 └─）
        b_connector_pos = lines[1].find("└─ ")
        c_connector_pos = lines[2].find("└─ ")
        assert b_connector_pos < c_connector_pos, (
            f"C 行 └─ 位置({c_connector_pos})应在 B 行 └─ 位置({b_connector_pos})之后"
        )

    def test_tree_status_icons(self):
        """running→⏺, done→✓, fail→✗ 三种状态各自渲染正确图标。"""
        root = TreeNode("root", children=[
            TreeNode("A", status="running"),
            TreeNode("B", status="done"),
            TreeNode("C", status="fail"),
        ])
        tree = Tree(root)
        output = tree.render()

        # 根节点 running → ⏺
        assert "⏺" in output
        assert "✓" in output
        assert "✗" in output
        # 确保各子节点行含对应图标
        lines = output.split("\n")
        assert any("✓" in l and "B" in l for l in lines)
        assert any("✗" in l and "C" in l for l in lines)

    def test_tree_collapsed_node(self):
        """is_expanded=False 时不渲染子节点。"""
        root = TreeNode("parent", is_expanded=False, children=[
            TreeNode("hidden_child"),
        ])
        tree = Tree(root)
        output = tree.render()

        assert "parent" in output
        assert "hidden_child" not in output
        lines = output.split("\n")
        assert len(lines) == 1  # 仅父节点一行

    def test_tree_indent_4(self):
        """indent=4 时孙节点缩进 4 空格。

        indent 影响非根节点子节点的前缀宽度。根的直接子节点
        前缀固定为空，缩进差异从第三层开始显现。
        """
        root = TreeNode("root", children=[
            TreeNode("child", children=[
                TreeNode("grandchild"),
            ]),
        ])
        tree = Tree(root, indent=4)
        output = tree.render()
        lines = output.split("\n")

        # 三层：root, child, grandchild
        assert len(lines) == 3
        gc_line = lines[2]
        assert "grandchild" in gc_line

        # indent=4: dash="── ", pipe_cont="│   ", spacer="    "
        # grandchild 的延续前缀为 4 字符空白
        leading = len(gc_line) - len(gc_line.lstrip(" │└├─"))
        assert leading >= 3, f"grandchild 行空白前缀应 >= 3，实际: {leading}"

        # 验证 indent=4 和 indent=2 输出不同
        tree2 = Tree(root, indent=2)
        output2 = tree2.render()
        assert output != output2

    def test_tree_ascii_fallback(self):
        """ascii_fallback=True 时使用 ASCII 连接线。"""
        root = TreeNode("root", children=[
            TreeNode("child1"),
            TreeNode("child2"),
        ])
        tree = Tree(root, ascii_fallback=True)
        output = tree.render()

        # ASCII 回退：indent=2 → dash="- ", connector="|- " / "\- "
        assert "|- " in output
        assert "\\- " in output
        # 确保无 Unicode box-drawing 字符
        assert "├" not in output
        assert "└" not in output
        assert "│" not in output

    def test_tree_empty_root(self):
        """root=None 时 render() 返回 ""。"""
        tree = Tree(root=None)
        output = tree.render()
        assert output == ""

    def test_tree_root_inherits_tuicomponent(self):
        """Tree 是 TuiComponent 子类。"""
        assert issubclass(Tree, TuiComponent)

    def test_tree_key(self):
        """key 属性返回 'tree'。"""
        tree = Tree(root=TreeNode("x"))
        assert tree.key == "tree"

    def test_tree_render_vnode(self):
        """render_vnode() 返回 VNode。"""
        root = TreeNode("root")
        tree = Tree(root)
        vnode = tree.render_vnode()

        assert isinstance(vnode, VNode)
        assert vnode.type == "tree"
        assert vnode.key == "tree"
        assert "text" in vnode.props
        assert "root" in vnode.props["text"]

    def test_tree_no_children_renders_only_root(self):
        """无子节点时仅渲染根节点。"""
        root = TreeNode("solo")
        tree = Tree(root)
        output = tree.render()
        lines = output.split("\n")
        assert len(lines) == 1

    def test_tree_multiple_children_last_uses_corner(self):
        """多个子节点中，最后一个用 └─ 而非 ├─。"""
        root = TreeNode("root", children=[
            TreeNode("first"),
            TreeNode("last"),
        ])
        tree = Tree(root)
        output = tree.render()

        # indent=2 → dash="─ ", connector="├─ " / "└─ "
        assert "├─ " in output  # 第一个子节点
        assert "└─ " in output  # 最后一个子节点

    def test_tree_pipe_prefix_for_intermediate_children(self):
        """三层结构中，中间层子节点前缀含 │ 延续线。"""
        root = TreeNode("root", children=[
            TreeNode("A", children=[
                TreeNode("A1"),
                TreeNode("A2"),
            ]),
            TreeNode("B"),
        ])
        tree = Tree(root)
        output = tree.render()

        # A 是第一个（非最后），其子节点 A1/A2 应使用 pipe 前缀延续
        # indent=2: A 行用 ├─，A1/A2 行前面应有 │ 前缀
        assert "│" in output

    def test_tree_standalone_instantiation(self):
        """Tree 可无参实例化（root 默认为 None）。"""
        tree = Tree()
        assert tree.root is None
        assert tree.render() == ""

    def test_tree_update_root(self):
        """update() 更新 root 属性。"""
        tree = Tree(root=TreeNode("old"))
        assert tree.root.label == "old"

        changed = tree.update({"root": TreeNode("new")})
        assert changed is True
        assert tree.root.label == "new"

    def test_tree_update_same_root_no_change(self):
        """update() 传入相同 root 时不标记变更。"""
        root = TreeNode("same")
        tree = Tree(root=root)
        changed = tree.update({"root": root})
        assert changed is False

    def test_tree_update_no_root_key(self):
        """update() 无 root key 时不标记变更。"""
        tree = Tree(root=TreeNode("x"))
        changed = tree.update({"other": "value"})
        assert changed is False
