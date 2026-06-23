"""单元测试 — chat_ui VDOM 引擎 (CVNode/CVPatch/diff/build_vnode)。"""

from __future__ import annotations
import pytest
from src.chat_ui._vdom import (
    CVNode, CVPatch, CVPatchType,
    build_vnode, diff, apply_patches, _reset_counter,
)


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _make_node(key: str, type: str = "Text", props: dict | None = None,
               children: list[CVNode] | None = None) -> CVNode:
    """创建测试用 CVNode。"""
    return CVNode(
        key=key,
        type=type,
        props=props if props is not None else {},
        children=children if children is not None else [],
    )


def _make_tree_single() -> CVNode:
    """创建单节点树。"""
    return _make_node("root:0", "Box")


def _make_tree_nested() -> CVNode:
    """创建嵌套树: root → [child_a, child_b → [grandchild]]。"""
    gc = _make_node("gc:0", "Text")
    child_b = _make_node("child_b:0", "Box", children=[gc])
    child_a = _make_node("child_a:0", "Text")
    return _make_node("root:0", "Box", children=[child_a, child_b])


def _make_tree_with_props() -> CVNode:
    """创建带属性的树。"""
    return _make_node("root:0", "Box", props={"color": "red", "size": 10})


def _clone_tree(node: CVNode) -> CVNode:
    """深拷贝 CVNode 树（用于模拟"相同"树）。"""
    return CVNode(
        key=node.key,
        type=node.type,
        props=dict(node.props),
        children=[_clone_tree(child) for child in node.children],
    )


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_vdom_counter():
    """每个测试前重置 VDOM 全局计数器。"""
    _reset_counter()


# ═══════════════════════════════════════════════════════════
# TestCVNode
# ═══════════════════════════════════════════════════════════

class TestCVNode:
    """CVNode 数据类基本属性测试。"""

    def test_cvnode_creation(self):
        """创建 CVNode 并验证属性。"""
        node = _make_node("text:0", "Text", props={"content": "hello"})
        assert node.key == "text:0"
        assert node.type == "Text"
        assert node.props == {"content": "hello"}
        assert node.children == []

    def test_cvnode_children(self):
        """CVNode 可以有 children。"""
        child1 = _make_node("child:0", "Text")
        child2 = _make_node("child:1", "Text")
        parent = _make_node("parent:0", "Box", children=[child1, child2])

        assert len(parent.children) == 2
        assert parent.children[0].key == "child:0"
        assert parent.children[1].key == "child:1"

    def test_cvnode_props(self):
        """CVNode props 默认为空 dict。"""
        node = CVNode(key="k", type="Box")
        assert node.props == {}
        assert isinstance(node.props, dict)


# ═══════════════════════════════════════════════════════════
# TestDiffSameTree
# ═══════════════════════════════════════════════════════════

class TestDiffSameTree:
    """两棵相同树的 diff 测试。"""

    def test_diff_same_tree_empty(self):
        """两棵相同空树（仅根节点无子）diff 返回空列表。"""
        old = _make_node("root:0", "Box")
        new = _clone_tree(old)
        patches = diff(old, new)
        assert patches == []

    def test_diff_same_tree_single_node(self):
        """两棵相同单节点树 diff 返回空。"""
        old = _make_tree_single()
        new = _clone_tree(old)
        patches = diff(old, new)
        assert patches == []

    def test_diff_same_tree_nested(self):
        """两棵相同嵌套树 diff 返回空。"""
        old = _make_tree_nested()
        new = _clone_tree(old)
        patches = diff(old, new)
        assert patches == []


# ═══════════════════════════════════════════════════════════
# TestDiffInsert
# ═══════════════════════════════════════════════════════════

class TestDiffInsert:
    """插入节点的 diff 测试。"""

    def test_diff_pure_append(self):
        """纯追加子节点 → INSERT 补丁。"""
        old = _make_node("root:0", "Box", children=[
            _make_node("child:0", "Text"),
        ])
        new = _make_node("root:0", "Box", children=[
            _make_node("child:0", "Text"),
            _make_node("child:1", "Text"),  # 新增
        ])

        patches = diff(old, new)

        insert_patches = [p for p in patches if p.type == CVPatchType.INSERT]
        assert len(insert_patches) == 1
        assert insert_patches[0].key == "child:1"
        assert insert_patches[0].parent_key == "root:0"
        assert insert_patches[0].node is not None
        assert insert_patches[0].node.type == "Text"

    def test_diff_first_render(self):
        """old_root=None → 全部 INSERT。"""
        new = _make_tree_nested()
        patches = diff(None, new)

        # nested: root, child_a, child_b, gc — 4 个节点
        keys = {p.key for p in patches}
        assert all(p.type == CVPatchType.INSERT for p in patches)
        assert keys == {"root:0", "child_a:0", "child_b:0", "gc:0"}

    def test_diff_insert_middle(self):
        """中间插入 → INSERT 补丁（不触发现有节点的 REORDER）。"""
        child_a = _make_node("a:0", "Text")
        child_c = _make_node("c:0", "Text")
        old = _make_node("root:0", "Box", children=[child_a, child_c])

        # 在 a 和 c 之间插入 b
        child_b = _make_node("b:0", "Text")
        new = _make_node("root:0", "Box", children=[
            _clone_tree(child_a),
            child_b,
            _clone_tree(child_c),
        ])

        patches = diff(old, new)
        insert_patches = [p for p in patches if p.type == CVPatchType.INSERT]
        assert len(insert_patches) == 1
        assert insert_patches[0].key == "b:0"


# ═══════════════════════════════════════════════════════════
# TestDiffDelete
# ═══════════════════════════════════════════════════════════

class TestDiffDelete:
    """删除节点的 diff 测试。"""

    def test_diff_delete_node(self):
        """删除子节点 → DELETE 补丁。"""
        old = _make_node("root:0", "Box", children=[
            _make_node("a:0", "Text"),
            _make_node("b:0", "Text"),
        ])
        new = _make_node("root:0", "Box", children=[
            _make_node("a:0", "Text"),
            # b:0 被删除
        ])

        patches = diff(old, new)
        delete_patches = [p for p in patches if p.type == CVPatchType.DELETE]
        assert len(delete_patches) == 1
        assert delete_patches[0].key == "b:0"

    def test_diff_clear_all(self):
        """清空所有 children → 全部 DELETE。"""
        old = _make_tree_nested()
        patches = diff(old, None)

        assert len(patches) > 0
        assert all(p.type == CVPatchType.DELETE for p in patches)
        # 后序遍历：子节点先于父节点
        keys = [p.key for p in patches]
        assert keys.index("gc:0") < keys.index("child_b:0")
        assert keys.index("child_a:0") < keys.index("root:0")
        assert keys.index("child_b:0") < keys.index("root:0")


# ═══════════════════════════════════════════════════════════
# TestDiffUpdate
# ═══════════════════════════════════════════════════════════

class TestDiffUpdate:
    """属性变更的 diff 测试。"""

    def test_diff_props_update(self):
        """子节点属性变更 → UPDATE 补丁。"""
        old_child = _make_node("c:0", "Text", props={"content": "old"})
        old = _make_node("root:0", "Box", children=[old_child])

        new_child = _make_node("c:0", "Text", props={"content": "new"})
        new = _make_node("root:0", "Box", children=[new_child])

        patches = diff(old, new)
        update_patches = [p for p in patches if p.type == CVPatchType.UPDATE]
        assert len(update_patches) == 1
        assert update_patches[0].key == "c:0"
        assert update_patches[0].node is not None
        assert update_patches[0].node.props == {"content": "new"}
        assert update_patches[0].old_props == {"content": "old"}

    def test_diff_root_props_update(self):
        """根节点属性变更 → UPDATE 补丁。"""
        old = _make_node("root:0", "Box", props={"color": "red"})
        new = _make_node("root:0", "Box", props={"color": "blue"})

        patches = diff(old, new)
        update_patches = [p for p in patches if p.type == CVPatchType.UPDATE]
        assert len(update_patches) == 1
        assert update_patches[0].key == "root:0"
        assert update_patches[0].old_props == {"color": "red"}

    def test_diff_props_unchanged_no_update(self):
        """属性未变更时不产生 UPDATE 补丁。"""
        old = _make_node("root:0", "Box", props={"color": "red"})
        new = _clone_tree(old)

        patches = diff(old, new)
        update_patches = [p for p in patches if p.type == CVPatchType.UPDATE]
        assert update_patches == []


# ═══════════════════════════════════════════════════════════
# TestApplyPatches
# ═══════════════════════════════════════════════════════════

class TestApplyPatches:
    """apply_patches 函数测试。"""

    def test_apply_patches_returns_new_root(self):
        """apply_patches 返回 new_root。"""
        old = _make_tree_single()
        new = _clone_tree(old)
        patches = diff(old, new)

        result = apply_patches(patches, old, new)
        assert result is new

    def test_apply_patches_with_none_new_root(self):
        """new_root=None 时 apply_patches 返回 None。"""
        old = _make_tree_single()
        patches = diff(old, None)

        result = apply_patches(patches, old, None)
        assert result is None

    def test_apply_patches_with_insert_patches(self):
        """有 INSERT 补丁时 apply_patches 仍返回 new_root。"""
        old = _make_tree_single()
        new = _make_node("root:0", "Box", children=[
            _make_node("child:0", "Text"),
        ])
        patches = diff(old, new)

        result = apply_patches(patches, old, new)
        assert result is new


# ═══════════════════════════════════════════════════════════
# TestBuildVnode
# ═══════════════════════════════════════════════════════════

class TestBuildVnode:
    """build_vnode 函数测试。"""

    def test_build_vnode_simple_component(self):
        """从简单 TuiComponent 构建 CVNode。"""
        from src.chat_ui._components import ErrorBlock

        comp = ErrorBlock("test error")
        vnode = build_vnode(comp)

        assert isinstance(vnode, CVNode)
        assert vnode.type == "ErrorBlock"
        assert vnode.key.startswith("errorblock:")
        assert "message" in vnode.props
        assert vnode.props["message"] == "test error"
        assert vnode.children == []

    def test_build_vnode_generates_keys(self):
        """build_vnode 自动生成唯一 key。"""
        from src.chat_ui._components import ErrorBlock

        _reset_counter()
        vnode1 = build_vnode(ErrorBlock("msg1"))
        vnode2 = build_vnode(ErrorBlock("msg2"))

        assert vnode1.key != vnode2.key
        assert vnode1.key.startswith("errorblock:")
        assert vnode2.key.startswith("errorblock:")

    def test_build_vnode_with_children(self):
        """build_vnode 递归构建子组件。"""
        from src.chat_ui._components import TuiComponent, ErrorBlock

        parent = TuiComponent()
        child1 = ErrorBlock("err1")
        child2 = ErrorBlock("err2")
        parent._ensure_children().extend([child1, child2])

        _reset_counter()
        vnode = build_vnode(parent)

        assert vnode.type == "TuiComponent"
        assert len(vnode.children) == 2
        assert vnode.children[0].type == "ErrorBlock"
        assert vnode.children[1].type == "ErrorBlock"
        assert vnode.children[0].props["message"] == "err1"
        assert vnode.children[1].props["message"] == "err2"

    def test_build_vnode_props_excludes_private(self):
        """build_vnode 提取的属性不包含私有属性（_ 开头）。"""
        from src.chat_ui._components import UserMsgBlock

        comp = UserMsgBlock("hello")
        vnode = build_vnode(comp)

        # 不应包含 _children 等私有属性
        for key in vnode.props:
            assert not key.startswith("_"), f"不应包含私有属性: {key}"

    def test_build_vnode_props_excludes_callable(self):
        """build_vnode 提取的属性不包含可调用对象。"""
        from src.chat_ui._components import ErrorBlock

        comp = ErrorBlock("test")
        vnode = build_vnode(comp)

        # render, render_to_adapter 等方法不应出现在 props 中
        for key in vnode.props:
            assert key not in ("render", "render_to_adapter", "render_children",
                               "add_child", "_ensure_children"), \
                f"不应包含可调用对象: {key}"

    def test_build_vnode_with_component_tree(self):
        """build_vnode 正确构建嵌套组件树。"""
        from src.chat_ui._components import TuiComponent, ErrorBlock, NotificationBlock

        # 构建: root → [err, notif]
        root = TuiComponent()
        err = ErrorBlock("an error")
        notif = NotificationBlock("a notification")
        root._ensure_children().extend([err, notif])

        _reset_counter()
        vnode = build_vnode(root)

        assert vnode.type == "TuiComponent"
        assert len(vnode.children) == 2
        types = {c.type for c in vnode.children}
        assert types == {"ErrorBlock", "NotificationBlock"}


# ═══════════════════════════════════════════════════════════
# TestDiffReorder（边界场景）
# ═══════════════════════════════════════════════════════════

class TestDiffReorder:
    """子节点顺序变更的 REORDER 补丁测试。"""

    def test_diff_reorder_swapped(self):
        """两个子节点交换位置 → REORDER 补丁。"""
        child_a = _make_node("a:0", "Text")
        child_b = _make_node("b:0", "Text")
        old = _make_node("root:0", "Box", children=[child_a, child_b])

        new = _make_node("root:0", "Box", children=[
            _clone_tree(child_b),
            _clone_tree(child_a),
        ])

        patches = diff(old, new)
        reorder_patches = [p for p in patches if p.type == CVPatchType.REORDER]
        assert len(reorder_patches) == 1
        assert reorder_patches[0].key == "root:0"

    def test_diff_reorder_not_triggered_on_insert(self):
        """插入新节点时不单独产生 REORDER（key 集合不同）。"""
        child_a = _make_node("a:0", "Text")
        old = _make_node("root:0", "Box", children=[child_a])

        child_b = _make_node("b:0", "Text")
        new = _make_node("root:0", "Box", children=[
            child_b,          # 新节点在前
            _clone_tree(child_a),
        ])

        patches = diff(old, new)
        reorder_patches = [p for p in patches if p.type == CVPatchType.REORDER]
        assert reorder_patches == [], \
            "插入新节点导致 key 集合变化，不应产生 REORDER"


# ═══════════════════════════════════════════════════════════
# TestDiffMixedScenarios（综合场景）
# ═══════════════════════════════════════════════════════════

class TestDiffMixedScenarios:
    """混合变更的 diff 综合测试。"""

    def test_diff_insert_update_delete(self):
        """同时有 INSERT、UPDATE、DELETE。"""
        child_a = _make_node("a:0", "Text", props={"v": "1"})
        child_b = _make_node("b:0", "Text", props={"v": "2"})
        child_c = _make_node("c:0", "Text", props={"v": "3"})
        old = _make_node("root:0", "Box", children=[child_a, child_b, child_c])

        # a 属性变化、b 删除、d 插入、c 不变
        new_a = _make_node("a:0", "Text", props={"v": "99"})
        new_d = _make_node("d:0", "Text", props={"v": "4"})
        new_c = _clone_tree(child_c)
        new = _make_node("root:0", "Box", children=[new_a, new_d, new_c])

        patches = diff(old, new)

        patch_types = {p.type for p in patches}
        assert CVPatchType.UPDATE in patch_types
        assert CVPatchType.INSERT in patch_types
        assert CVPatchType.DELETE in patch_types

        # 验证具体补丁
        delete_keys = {p.key for p in patches if p.type == CVPatchType.DELETE}
        insert_keys = {p.key for p in patches if p.type == CVPatchType.INSERT}
        update_keys = {p.key for p in patches if p.type == CVPatchType.UPDATE}

        assert "b:0" in delete_keys
        assert "d:0" in insert_keys
        assert "a:0" in update_keys

    def test_diff_both_none(self):
        """old_root 和 new_root 都为 None → 空列表。"""
        patches = diff(None, None)
        assert patches == []


# ═══════════════════════════════════════════════════════════
# TestPatchSorting（补丁排序验证）
# ═══════════════════════════════════════════════════════════

class TestPatchSorting:
    """补丁深度优先排序测试。"""

    def test_patches_parent_before_child_on_insert(self):
        """INSERT 补丁：父节点排在子节点前面。"""
        parent = _make_node("p:0", "Box")
        child = _make_node("c:0", "Text")
        new = _make_node("root:0", "Box", children=[
            _make_node("p:0", "Box", children=[_make_node("c:0", "Text")]),
        ])

        old = _make_node("root:0", "Box", children=[])
        patches = diff(old, new)

        insert_keys = [p.key for p in patches if p.type == CVPatchType.INSERT]
        # 父节点("p:0")应在子节点("c:0")前
        p_idx = insert_keys.index("p:0") if "p:0" in insert_keys else -1
        c_idx = insert_keys.index("c:0") if "c:0" in insert_keys else -1
        if p_idx >= 0 and c_idx >= 0:
            assert p_idx < c_idx, "父节点应排在子节点前面"


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
