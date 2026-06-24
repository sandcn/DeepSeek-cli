"""VNode — 虚拟 DOM 节点，用于声明式 TUI 组件树 Diff。

定义 VNode（虚拟节点）和 Patch（变更操作）类型，
支持差分渲染：每次事件更新后计算 old_tree → new_tree 的最小变更集，
仅输出变化的 ANSI 序列。

使用方式：
    old = VNode(type="thinking_block", props={"text": "Hello"})
    new = VNode(type="thinking_block", props={"text": "Hello World"})
    patches = diff(old, new)
    apply_patches(patches, adapter)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    pass


class PatchKind(Enum):
    """变更操作类型。"""
    REPLACE = auto()       # 全量替换节点
    UPDATE_PROPS = auto()  # 仅更新属性（如样式变化）
    APPEND_CHILD = auto()  # 添加子节点
    REMOVE_CHILD = auto()  # 移除子节点
    REORDER = auto()       # 子节点重排
    NOOP = auto()          # 无变更


@dataclass
class Patch:
    """单个变更操作。"""
    kind: PatchKind
    path: tuple[int, ...] = ()       # 从根到目标节点的路径（索引序列）
    new_vnode: "VNode | None" = None  # 新节点（REPLACE/APPEND_CHILD 时使用）
    new_props: dict[str, Any] | None = None  # 新属性（UPDATE_PROPS 时使用）
    old_index: int = -1               # 旧索引（REMOVE_CHILD/REORDER 时使用）
    new_index: int = -1               # 新索引（APPEND_CHILD/REORDER 时使用）
    node: "VNode | None" = None       # 目标节点引用（UPDATE_PROPS: 旧节点; REMOVE_CHILD/APPEND_CHILD: 父节点）
    child_key: str = ""               # 子节点 key（REMOVE_CHILD 时使用）

    def __repr__(self) -> str:
        return f"Patch({self.kind.name}, path={self.path}, new_vnode={self.new_vnode})"


@dataclass
class VNode:
    """虚拟 DOM 节点。

    属性：
        type: 组件类型标识（如 "thinking_block", "answer_block", "bottom_bar"）
        props: 属性字典（如 {"text": "...", "style": "dim"}）
        key: 稳定标识符，用于列表 Diff 优化（可选）
        children: 子 VNode 列表
        version: 差分渲染版本号（内部使用）
    """
    type: str
    props: dict[str, Any] = field(default_factory=dict)
    key: str = ""
    children: list["VNode"] = field(default_factory=list)
    version: int = 0

    def __hash__(self) -> int:
        return hash((self.type, self.key, self.version))

    def __repr__(self) -> str:
        kids = f", children={len(self.children)}" if self.children else ""
        return f"VNode({self.type!r}, key={self.key!r}{kids})"


# ═══════════════════════════════════════════════════════════
# Diff 算法
# ═══════════════════════════════════════════════════════════

def diff(old: VNode | None, new: VNode | None, path: tuple[int, ...] = ()) -> list[Patch]:
    """计算两个 VNode 树之间的最小变更集。

    Args:
        old: 旧 VNode 树根节点（首次渲染时为 None）
        new: 新 VNode 树根节点
        path: 当前路径（内部递归使用）

    Returns:
        Patch 列表，按深度优先顺序排列
    """
    patches: list[Patch] = []

    if old is None:
        # 首次渲染：全量 REPLACE
        if new is not None:
            patches.append(Patch(kind=PatchKind.REPLACE, path=path, new_vnode=new))
        return patches

    if new is None:
        # 节点移除
        patches.append(Patch(kind=PatchKind.REMOVE_CHILD, path=path))
        return patches

    # 类型变化 → 全量替换
    if old.type != new.type:
        patches.append(Patch(kind=PatchKind.REPLACE, path=path, new_vnode=new))
        return patches

    # Props 变化 → 仅更新属性
    if old.props != new.props:
        patches.append(Patch(kind=PatchKind.UPDATE_PROPS, path=path, new_props=new.props, node=old))

    # Children diff（基于 key 的简单算法）
    _diff_children(old.children, new.children, path, patches, parent_node=old)

    return patches


def _diff_children(
    old_kids: list[VNode],
    new_kids: list[VNode],
    parent_path: tuple[int, ...],
    patches: list[Patch],
    parent_node: VNode | None = None,
) -> None:
    """简单 children Diff：按 key 匹配 + 按位置比较。

    Args:
        old_kids: 旧子节点列表
        new_kids: 新子节点列表
        parent_path: 父节点路径
        patches: 累积的 Patch 列表
        parent_node: 父 VNode 引用（用于 REMOVE_CHILD/APPEND_CHILD 时设置 node 字段）
    """
    # 使用 key 建立索引
    old_by_key: dict[str, int] = {}
    for i, child in enumerate(old_kids):
        if child.key:
            old_by_key[child.key] = i

    new_by_key: dict[str, int] = {}
    for i, child in enumerate(new_kids):
        if child.key:
            new_by_key[child.key] = i

    # 移除旧节点（key 不在新 children 中）
    for i, child in enumerate(old_kids):
        if child.key and child.key not in new_by_key:
            patches.append(Patch(
                kind=PatchKind.REMOVE_CHILD,
                path=parent_path,
                old_index=i,
                node=parent_node,
                child_key=child.key,
            ))

    # 添加新节点（key 不在旧 children 中）
    for i, child in enumerate(new_kids):
        if child.key and child.key not in old_by_key:
            patches.append(Patch(
                kind=PatchKind.APPEND_CHILD,
                path=parent_path,
                new_vnode=child,
                new_index=i,
                node=parent_node,
            ))

    # 对匹配的节点递归 diff
    matched_pairs: list[tuple[int, int]] = []
    for i, child in enumerate(new_kids):
        if child.key and child.key in old_by_key:
            matched_pairs.append((old_by_key[child.key], i))

    for old_idx, new_idx in matched_pairs:
        child_patches = diff(
            old_kids[old_idx], new_kids[new_idx],
            path=parent_path + (new_idx,),
        )
        patches.extend(child_patches)

    # ── 按位置回退匹配（无 key 节点）──
    # 有 key 的节点已通过上述 key-matching 处理，
    # 此处仅处理无 key 节点的按位置对齐。
    # 策略：遍历 min(len(old), len(new)) 位置，
    # 对同一位置的两个节点递归 diff（仅当它们都没有 key 时）。
    min_len = min(len(old_kids), len(new_kids))
    for pos in range(min_len):
        old_child = old_kids[pos]
        new_child = new_kids[pos]
        # 跳过已通过 key 匹配处理过的节点对
        if old_child.key and new_child.key:
            continue
        # 位置匹配的节点对递归 diff
        child_patches = diff(
            old_child, new_child,
            path=parent_path + (pos,),
        )
        patches.extend(child_patches)

    # 超出部分：旧 children 多于新 children → 标记 REMOVE_CHILD
    if len(old_kids) > len(new_kids):
        for idx in range(min_len, len(old_kids)):
            patches.append(Patch(
                kind=PatchKind.REMOVE_CHILD,
                path=parent_path,
                old_index=idx,
                node=parent_node,
                child_key=old_kids[idx].key,
            ))

    # 超出部分：新 children 多于旧 children → 标记 APPEND_CHILD
    if len(new_kids) > len(old_kids):
        for idx in range(min_len, len(new_kids)):
            patches.append(Patch(
                kind=PatchKind.APPEND_CHILD,
                path=parent_path,
                new_vnode=new_kids[idx],
                new_index=idx,
                node=parent_node,
            ))


# ═══════════════════════════════════════════════════════════
# Patch 应用器
# ═══════════════════════════════════════════════════════════

def _navigate_to_parent(root: "VNode", path: tuple) -> "VNode":
    """按路径导航到父节点（路径最后一级的父节点）。

    Args:
        root: VNode 树根节点
        path: 目标节点路径（索引序列），如 (0, 2) 表示 root.children[0].children[2]

    Returns:
        目标节点的父节点；若 path 为空或仅一级，返回 root 本身。
    """
    if not path or len(path) <= 1:
        return root
    node = root
    for idx in path[:-1]:
        if isinstance(idx, int) and node.children and idx < len(node.children):
            node = node.children[idx]
        else:
            return root
    return node


def apply_patches(
    root: VNode | None,
    patches: list[Patch],
    render_cb: "Callable[[VNode], None] | None" = None,
) -> VNode | None:
    """应用 patches 并渲染变更节点。

    Args:
        root: 旧 VNode 树根节点（首次渲染时为 None）
        patches: Diff 产生的 Patch 列表
        render_cb: 可选渲染回调，接收 VNode 并输出到终端。
                   为 None 时仅更新树结构，不触发渲染。

    Returns:
        更新后的 VNode 树根节点。

    对于每个非 NOOP patch：
      - REPLACE：若新旧根不同，调用 render_cb 重新渲染整个树
      - APPEND_CHILD：渲染新增的子节点
      - REMOVE_CHILD：从父节点移除子节点（终端行由后续重绘覆盖）
      - UPDATE_PROPS：合并新 props 并重新渲染该节点
    """
    if root is None:
        # 首次渲染：每个非 NOOP patch 都应该有完整节点信息
        for p in patches:
            if p.kind != PatchKind.NOOP and render_cb:
                target = p.new_vnode if p.new_vnode is not None else p.node
                if target is not None:
                    render_cb(target)
        return root

    for p in patches:
        if p.kind == PatchKind.NOOP:
            continue
        elif p.kind == PatchKind.REPLACE:
            # 按路径导航到父节点，替换对应索引的子节点
            if p.path == ():
                # 根级别替换：直接替换根节点
                if render_cb and p.new_vnode:
                    render_cb(p.new_vnode)
                root = p.new_vnode
            else:
                # 非根替换：导航到父节点，替换对应索引的子节点
                parent = _navigate_to_parent(root, p.path)
                child_idx = p.path[-1] if p.path else -1
                if (isinstance(child_idx, int) and child_idx >= 0
                        and parent.children and child_idx < len(parent.children)):
                    if render_cb and p.new_vnode:
                        render_cb(p.new_vnode)
                    parent.children[child_idx] = p.new_vnode
        elif p.kind == PatchKind.UPDATE_PROPS:
            # 更新属性：合并新 props 并重新渲染该节点
            if p.node is not None and p.new_props:
                p.node.props.update(p.new_props)
            if render_cb and p.node:
                render_cb(p.node)
        elif p.kind == PatchKind.APPEND_CHILD:
            # 添加子节点：渲染新增子节点，追加到父节点
            # p.node 指向父节点（由 diff 设置），若为 None 则通过路径导航
            actual_parent = p.node
            if actual_parent is None and p.path:
                actual_parent = _navigate_to_parent(root, p.path)
            if render_cb and p.new_vnode:
                render_cb(p.new_vnode)
            if p.new_vnode is not None and actual_parent is not None:
                actual_parent.children.append(p.new_vnode)
        elif p.kind == PatchKind.REMOVE_CHILD:
            # 移除子节点：从父节点 children 中过滤掉
            # 终端不需要主动清除（内容区域会被后续输出覆盖）
            # p.node 指向父节点（由 diff 设置），若为 None 则通过路径导航
            actual_parent = p.node
            if actual_parent is None and p.path:
                actual_parent = _navigate_to_parent(root, p.path)
            if actual_parent is not None and p.child_key:
                actual_parent.children = [
                    c for c in actual_parent.children
                    if c.key != p.child_key
                ]
        # REORDER 暂不处理（当前 diff 算法不产生 REORDER patch）

    return root
