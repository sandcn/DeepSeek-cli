"""chat_ui 轻量 VDOM 引擎 — 专用于组件树结构 diff。

与 ``src/api/renderer/vnode/`` 的 VNode 体系明确区分：
  - VNode (api 层)：用于 Markdown AST 渲染，关注内容级增量更新（行级、内联）
  - CVNode (chat_ui 层)：用于 chat_ui 组件树结构 diff，关注组件增删改和属性变更

设计原则：
  - key-based 对比，O(n) 复杂度
  - 仅在组件树结构变化（增/删/移/属性变更）时产生补丁，内容更新走 RenderCommand 管道
  - CVNode/CVPatch 前缀命名（C = Chat），与 api 层 VNode/VPatch 明确区分
  - 纯 Python 实现，零外部依赖
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._components import TuiComponent

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 模块级计数器（用于生成唯一 key）
# ═══════════════════════════════════════════════════════════

_counter: int = 0

_counter_lock = threading.Lock()


def _reset_counter() -> None:
    """重置全局计数器（仅供测试使用）。"""
    global _counter
    _counter = 0


def _next_key() -> int:
    """生成下一个唯一整数 key（线程安全）。"""
    with _counter_lock:
        global _counter
        key = _counter
        _counter += 1
        return key


# ═══════════════════════════════════════════════════════════
# 核心类型
# ═══════════════════════════════════════════════════════════

class CVPatchType(Enum):
    """补丁类型 — 描述组件树结构差异的操作类别。

    INSERT: 新增节点（新树有，旧树无）
    DELETE: 删除节点（旧树有，新树无）
    UPDATE: 属性变更（key 相同但 props 不同）
    REORDER: 子节点顺序变更（同一父节点下 children 排列变化）
    """
    INSERT = "insert"
    DELETE = "delete"
    UPDATE = "update"
    REORDER = "reorder"


@dataclass
class CVNode:
    """轻量虚拟 DOM 节点 — chat_ui 组件树结构表示。

    与 src/api/renderer/vnode/types.py 的 VNode 明确区分：
    - VNode 用于 Markdown AST 渲染（内容级 diff，行级增量）
    - CVNode 用于 chat_ui 组件树结构 diff（树级 diff，组件增删改）

    Attributes:
        key: 唯一标识（如 "box:0", "text:1", "static:2"）
        type: 组件类型名（如 "Box", "Text", "Static"）
        props: 组件属性快照（用于 UPDATE 检测）
        children: 子 CVNode 列表
    """
    key: str
    type: str
    props: dict = field(default_factory=dict)
    children: list[CVNode] = field(default_factory=list)


@dataclass
class CVPatch:
    """虚拟补丁 — 对组件树的一个原子修改操作。

    Attributes:
        type: 补丁类型
        key: 目标节点的 key
        node: INSERT/UPDATE 时的新节点（UPDATE 时为带新 props 的节点）
        parent_key: 父节点 key（INSERT 时用于定位插入位置）
        index: 在父节点 children 中的索引（INSERT/REORDER 时使用）
        old_props: UPDATE 时的旧属性（用于回滚/日志）
    """
    type: CVPatchType
    key: str
    node: CVNode | None = None
    parent_key: str | None = None
    index: int = -1
    old_props: dict | None = None


# ═══════════════════════════════════════════════════════════
# 构建 CVNode 树
# ═══════════════════════════════════════════════════════════

def build_vnode(component: TuiComponent) -> CVNode:
    """从 TuiComponent 树构建 CVNode 树。

    递归遍历组件树，为每个组件生成唯一 key，提取可序列化属性作为 props。
    Rich Style 等不可序列化属性转为字符串表示，无法转换的属性跳过。

    Args:
        component: TuiComponent 实例（可能含 children 子组件树）

    Returns:
        CVNode 树根节点
    """
    type_name = type(component).__name__
    key = f"{type_name.lower()}:{_next_key()}"
    props = _extract_props(component)

    children: list[CVNode] = []
    for child in component.children:
        children.append(build_vnode(child))

    return CVNode(key=key, type=type_name, props=props, children=children)


def _extract_props(component: TuiComponent) -> dict:
    """从 TuiComponent 实例提取可序列化属性。

    规则：
    - 跳过以 '_' 开头的私有属性
    - 跳过可调用对象（方法）
    - Rich Style 对象 → repr 字符串
    - 基本类型（str/int/float/bool/None/list/tuple/dict）直接保留
    - 其他类型尝试 str()，失败则跳过并记录 debug 日志
    """
    props: dict = {}
    from rich.style import Style
    for attr_name in dir(component):
        if attr_name.startswith('_'):
            continue
        if attr_name == 'children':
            continue
        try:
            value = getattr(component, attr_name)
        except Exception:
            continue
        if callable(value):
            continue
        # Rich Style 对象
        if isinstance(value, Style):
            props[attr_name] = repr(value)
        elif isinstance(value, (str, int, float, bool, type(None))):
            props[attr_name] = value
        elif isinstance(value, (list, tuple)):
            # 浅拷贝，内部元素若含不可序列化对象则转字符串
            try:
                props[attr_name] = list(value)
            except Exception:
                props[attr_name] = str(value)
        elif isinstance(value, dict):
            try:
                props[attr_name] = dict(value)
            except Exception:
                props[attr_name] = str(value)
        else:
            try:
                props[attr_name] = str(value)
            except Exception:
                _logger.debug(
                    "build_vnode: 跳过不可序列化属性 %s.%s (type=%s)",
                    type(component).__name__, attr_name, type(value).__name__,
                )
    return props


# ═══════════════════════════════════════════════════════════
# Diff 算法
# ═══════════════════════════════════════════════════════════

def diff(old_root: CVNode | None, new_root: CVNode | None) -> list[CVPatch]:
    """对比两棵 CVNode 树，返回补丁列表。

    key-based O(n) 对比算法：
    1. 收集新旧两棵树的所有节点（key → (node, parent_key, index) 映射）
    2. 遍历新树：旧树中不存在的 key → INSERT；存在但 props 不同 → UPDATE
    3. 遍历旧树：新树中不存在的 key → DELETE
    4. 检测同一父节点下子节点顺序变化 → REORDER
    5. 按深度优先顺序排序补丁（先父后子，确保 INSERT 时父节点已存在）

    快速路径：
    - old_root 为 None（首次渲染）→ 全部 INSERT，不执行对比
    - 纯追加检测：新树含旧树全部节点且旧节点 props 未变 → 仅 INSERT + REORDER 检测
    - 纯删除检测：new_root 为 None → 全部 DELETE

    Args:
        old_root: 旧 CVNode 树根（None 表示首次渲染）
        new_root: 新 CVNode 树根（None 表示清空）

    Returns:
        补丁列表（按应用顺序排列，先父后子）
    """
    patches: list[CVPatch] = []

    # ── 首次渲染：全部 INSERT ──────────────────────────
    if old_root is None:
        if new_root is None:
            return patches
        _collect_inserts(new_root, None, patches)
        return patches

    # ── 清空：全部 DELETE ──────────────────────────────
    if new_root is None:
        _collect_deletes(old_root, patches)
        return patches

    # ── 收集节点映射 ───────────────────────────────────
    # old_map: key → (node, parent_key, index_in_parent)
    old_map: dict[str, tuple[CVNode, str | None, int]] = {}
    _collect_nodes(old_root, None, -1, old_map)

    # new_map: key → (node, parent_key, index_in_parent)
    new_map: dict[str, tuple[CVNode, str | None, int]] = {}
    _collect_nodes(new_root, None, -1, new_map)

    old_keys = set(old_map.keys())
    new_keys = set(new_map.keys())

    # ── 收集父子关系（用于 REORDER 检测） ───────────────
    parent_children_old: dict[str, list[str]] = {}
    parent_children_new: dict[str, list[str]] = {}
    for key, (_, parent_key, _) in old_map.items():
        if parent_key is not None:
            parent_children_old.setdefault(parent_key, []).append(key)
    for key, (_, parent_key, _) in new_map.items():
        if parent_key is not None:
            parent_children_new.setdefault(parent_key, []).append(key)

    # ── 快速路径：纯追加（旧节点全不变，仅有新增） ──────
    if old_keys.issubset(new_keys) and _all_unchanged(old_map, new_map, old_keys):
        for key in new_keys - old_keys:
            node, parent_key, index = new_map[key]
            patches.append(CVPatch(
                type=CVPatchType.INSERT,
                key=key,
                node=node,
                parent_key=parent_key,
                index=index,
            ))
        # 纯追加时仍需检查 REORDER：新增子节点可能改变兄弟顺序
        _detect_reorder(parent_children_old, parent_children_new, patches)
        _sort_patches_depth_first(patches, new_map, old_map)
        return patches

    # ── INSERT：新树有、旧树无 ──────────────────────────
    for key in new_keys - old_keys:
        node, parent_key, index = new_map[key]
        patches.append(CVPatch(
            type=CVPatchType.INSERT,
            key=key,
            node=node,
            parent_key=parent_key,
            index=index,
        ))

    # ── DELETE：旧树有、新树无 ──────────────────────────
    for key in old_keys - new_keys:
        patches.append(CVPatch(
            type=CVPatchType.DELETE,
            key=key,
        ))

    # ── UPDATE：key 相同但 props 不同 ───────────────────
    for key in old_keys & new_keys:
        old_node, _, _ = old_map[key]
        new_node, _, _ = new_map[key]
        if old_node.props != new_node.props:
            patches.append(CVPatch(
                type=CVPatchType.UPDATE,
                key=key,
                node=new_node,
                old_props=dict(old_node.props),
            ))

    # ── REORDER：同一父节点下子节点顺序变化 ────────────
    _detect_reorder(parent_children_old, parent_children_new, patches)

    # ── 深度优先排序（先父后子） ────────────────────────
    _sort_patches_depth_first(patches, new_map, old_map)

    return patches


def _collect_nodes(
    root: CVNode,
    parent_key: str | None,
    index: int,
    out: dict[str, tuple[CVNode, str | None, int]],
) -> None:
    """深度优先收集树中所有节点到 key→(node, parent_key, index) 映射。"""
    out[root.key] = (root, parent_key, index)
    for i, child in enumerate(root.children):
        _collect_nodes(child, root.key, i, out)


def _detect_reorder(
    parent_children_old: dict[str, list[str]],
    parent_children_new: dict[str, list[str]],
    patches: list[CVPatch],
) -> None:
    """检测子节点顺序变化，生成 REORDER 补丁。

    当父节点下子节点 key 集合完全相同时检测整体顺序。
    当有新增/删除子节点时，检测仅含旧 key 的子序列顺序变化。
    """
    common_parents = set(parent_children_old.keys()) & set(parent_children_new.keys())
    for parent_key in common_parents:
        old_seq = parent_children_old[parent_key]
        new_seq = parent_children_new[parent_key]
        if set(old_seq) == set(new_seq):
            # 子节点集合相同，整体顺序不同 → REORDER
            if old_seq != new_seq:
                patches.append(CVPatch(
                    type=CVPatchType.REORDER,
                    key=parent_key,
                    index=-1,
                ))
        else:
            # 有新增/删除，但旧 key 子集可能仍有顺序变化
            old_set = set(old_seq)
            new_set = set(new_seq)
            filtered_new = [k for k in new_seq if k in old_set]
            filtered_old = [k for k in old_seq if k in new_set]
            if len(filtered_new) >= 2 and filtered_new != filtered_old:
                patches.append(CVPatch(
                    type=CVPatchType.REORDER,
                    key=parent_key,
                    index=-1,
                ))


def _collect_inserts(
    root: CVNode,
    parent_key: str | None,
    patches: list[CVPatch],
    index: int = -1,
) -> None:
    """深度优先收集整棵树为 INSERT 补丁列表。"""
    patches.append(CVPatch(
        type=CVPatchType.INSERT,
        key=root.key,
        node=root,
        parent_key=parent_key,
        index=index,
    ))
    for i, child in enumerate(root.children):
        _collect_inserts(child, root.key, patches, i)


def _collect_deletes(
    root: CVNode,
    patches: list[CVPatch],
) -> None:
    """深度优先收集整棵树为 DELETE 补丁列表（后序遍历：先子后父）。"""
    for child in root.children:
        _collect_deletes(child, patches)
    patches.append(CVPatch(
        type=CVPatchType.DELETE,
        key=root.key,
    ))


def _all_unchanged(
    old_map: dict[str, tuple[CVNode, str | None, int]],
    new_map: dict[str, tuple[CVNode, str | None, int]],
    old_keys: set[str],
) -> bool:
    """检查旧树中所有节点在新树中 props 是否完全不变。"""
    for key in old_keys:
        old_node, _, _ = old_map[key]
        new_node, _, _ = new_map[key]
        if old_node.props != new_node.props:
            return False
    return True


def _sort_patches_depth_first(
    patches: list[CVPatch],
    new_map: dict[str, tuple[CVNode, str | None, int]],
    old_map: dict[str, tuple[CVNode, str | None, int]] | None = None,
) -> None:
    """按深度优先顺序排序补丁（先父后子）。

    排序规则：
    1. INSERT/DELETE: 父节点排在子节点前面（深度越小越靠前）
    2. UPDATE: 排在 INSERT/DELETE 之后
    3. REORDER: 排在最后

    同深度时按 index 排序。
    """
    # 计算每个 key 的深度（从根的层级数）
    depth_cache: dict[str, int] = {}

    def get_depth(key: str) -> int:
        if key in depth_cache:
            return depth_cache[key]
        if key not in new_map:
            if old_map is not None and key in old_map:
                _, parent_key, _ = old_map[key]
                if parent_key is None:
                    depth_cache[key] = 0
                else:
                    depth_cache[key] = get_depth(parent_key) + 1
            else:
                depth_cache[key] = 0
            return depth_cache[key]
        _, parent_key, _ = new_map[key]
        if parent_key is None:
            depth_cache[key] = 0
        else:
            depth_cache[key] = get_depth(parent_key) + 1
        return depth_cache[key]

    def sort_key(patch: CVPatch) -> tuple[int, int, int]:
        # type_prio: INSERT=0, DELETE=0, UPDATE=1, REORDER=2
        type_order = {
            CVPatchType.INSERT: 0,
            CVPatchType.DELETE: 0,
            CVPatchType.UPDATE: 1,
            CVPatchType.REORDER: 2,
        }
        depth = get_depth(patch.key)
        return (type_order.get(patch.type, 3), depth, patch.index)

    patches.sort(key=sort_key)


# ═══════════════════════════════════════════════════════════
# 补丁应用
# ═══════════════════════════════════════════════════════════

def apply_patches(
    patches: list[CVPatch],
    old_root: CVNode | None,
    new_root: CVNode | None,
) -> CVNode | None:
    """应用补丁列表到旧树，返回新树。

    当前实现：由于我们已经持有 new_root，直接返回它即完成"应用"。
    提供此函数以保持接口完整性，便于未来扩展（如增量应用补丁到 live tree）。

    Args:
        patches: diff() 返回的补丁列表
        old_root: 旧 CVNode 树根
        new_root: 新 CVNode 树根

    Returns:
        应用补丁后的 CVNode 树根（等价于 new_root）
    """
    _logger.debug(
        "apply_patches: %d patches, returning new_root (type=%s)",
        len(patches),
        type(new_root).__name__ if new_root is not None else "None",
    )
    return new_root
