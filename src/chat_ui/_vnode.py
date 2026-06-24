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
from typing import Any


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
        patches.append(Patch(kind=PatchKind.UPDATE_PROPS, path=path, new_props=new.props))

    # Children diff（基于 key 的简单算法）
    _diff_children(old.children, new.children, path, patches)

    return patches


def _diff_children(
    old_kids: list[VNode],
    new_kids: list[VNode],
    parent_path: tuple[int, ...],
    patches: list[Patch],
) -> None:
    """简单 children Diff：按 key 匹配 + 按位置比较。"""
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
            ))

    # 添加新节点（key 不在旧 children 中）
    for i, child in enumerate(new_kids):
        if child.key and child.key not in old_by_key:
            patches.append(Patch(
                kind=PatchKind.APPEND_CHILD,
                path=parent_path,
                new_vnode=child,
                new_index=i,
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


# ═══════════════════════════════════════════════════════════
# Patch 应用器
# ═══════════════════════════════════════════════════════════

def apply_patches(
    patches: list[Patch],
    old_root: VNode | None,
    new_root: VNode | None,
) -> VNode | None:
    """应用 Patch 列表，返回新 VNode 树（纯函数，无副作用）。

    Args:
        patches: Diff 产生的 Patch 列表
        old_root: 旧 VNode 树根节点
        new_root: 新 VNode 树根节点（用于 REPLACE）

    Returns:
        新 VNode 树根节点
    """
    have_replace = any(p.kind == PatchKind.REPLACE and p.path == () for p in patches)
    if have_replace:
        return new_root
    return old_root
