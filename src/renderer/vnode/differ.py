"""differ — VNode 树差异对比器。

比较两棵 VNode 树的差异，产出一组 VPatch 补丁，
专为流式追加场景优化（LLM 输出增量渲染）。
"""

from __future__ import annotations

from typing import Optional

from .types import (
    PatchType,
    VPatch,
    VNode,
    VNodeDiffResult,
)


class VNodeDiffer:
    """VNode 树差异对比器。

    比较两棵 VNode 树（旧树和新树），输出可应用的补丁列表。
    专为流式追加场景优化——大部分操作为 INSERT（追加新内容）。

    用法：
      differ = VNodeDiffer()
      result = differ.diff(old_root, new_root)
      for patch in result.patches:
          ...  # 应用补丁
    """

    # ──────────────────────────────────────────────
    # 公开接口
    # ──────────────────────────────────────────────

    def diff(self, old_root: VNode | None, new_root: VNode) -> VNodeDiffResult:
        """对比新旧两棵 VNode 树，返回差异补丁。

        Args:
            old_root: 旧树的根节点（None 表示首次渲染，所有节点均为 INSERT）
            new_root: 新树的根节点

        Returns:
            包含所有差异补丁的 VNodeDiffResult
        """
        result = VNodeDiffResult()

        # 首次渲染：新树所有节点均为 INSERT
        if old_root is None:
            self._collect_inserts(new_root, (), result)
            return result

        # 收集新旧两棵树的 key→VNode 映射（带路径）
        old_keyed, old_paths = self._collect_keyed(old_root)
        new_keyed, new_paths = self._collect_keyed(new_root)

        # 纯追加优化：跳过全量对比（用已有 keyed 判断）
        if self._is_append_only_fast(old_keyed, new_keyed):
            self._collect_inserts_only_fast(new_keyed, old_keyed, new_paths, result)
            return result

        # 常规 diff：检测 INSERT / UPDATE / DELETE / REORDER
        self._diff_nodes_fast(old_keyed, new_keyed, old_paths, new_paths, old_root, new_root, result)

        return result

    # ──────────────────────────────────────────────
    # 核心 diff 逻辑
    # ──────────────────────────────────────────────

    def _diff_nodes_fast(
        self,
        old_keyed: dict[str, VNode],
        new_keyed: dict[str, VNode],
        old_paths: dict[str, tuple[int, ...]],
        new_paths: dict[str, tuple[int, ...]],
        old_root: VNode,
        new_root: VNode,
        result: VNodeDiffResult,
    ) -> None:
        """对比两个 key→VNode 映射，使用 O(1) 路径查找。

        收集 INSERT / UPDATE / DELETE / REORDER 补丁并加入 result。
        """
        # ── 1. 扫描新树：检测 INSERT 和 UPDATE ──
        for key, new_node in new_keyed.items():
            if key not in old_keyed:
                # 新节点 → INSERT（O(1) 查路径）
                path = new_paths.get(key, ())
                result.add(VPatch(
                    type=PatchType.INSERT,
                    key=key,
                    path=path,
                    node=new_node,
                ))
            else:
                # 已有节点 → 检查内容/属性/子节点变化
                old_node = old_keyed[key]
                if (self._content_changed(old_node, new_node) or
                    self._props_changed(old_node, new_node) or
                    self._children_content_changed(old_node, new_node)):
                    path = new_paths.get(key, ())
                    result.add(VPatch(
                        type=PatchType.UPDATE,
                        key=key,
                        path=path,
                        node=new_node,
                        old_content=old_node.content,
                        new_content=new_node.content,
                        old_props=dict(old_node.props),
                        new_props=dict(new_node.props),
                    ))

        # ── 2. 扫描旧树：检测 DELETE（O(1) 查路径）──
        for key, old_node in old_keyed.items():
            if key not in new_keyed:
                path = old_paths.get(key, ())
                result.add(VPatch(
                    type=PatchType.DELETE,
                    key=key,
                    path=path,
                    node=old_node,
                ))

        # ── 3. 检测 REORDER（仅在存在 DELETE/INSERT 时触发）──
        has_deletes = any(p.type == PatchType.DELETE for p in result.patches)
        has_inserts = any(p.type == PatchType.INSERT for p in result.patches)
        if has_deletes or has_inserts:
            self._detect_reorder(old_root, new_root, old_paths, new_paths, result)

    # ──────────────────────────────────────────────
    # 内容/属性比较
    # ──────────────────────────────────────────────

    def _content_changed(self, old: VNode, new: VNode) -> bool:
        """检查两个 VNode 的 content 是否不同。"""
        return old.content != new.content

    def _props_changed(self, old: VNode, new: VNode) -> bool:
        """检查两个 VNode 的 props 是否不同。"""
        return old.props != new.props

    def _children_content_changed(self, old: VNode, new: VNode) -> bool:
        """检查两个 VNode 的子节点 content 和 props 是否不同。

        只比较 content 和 props，不比较 children 嵌套，
        因为每次构建 VNode 树时子节点均为新实例，
        默认的 identity 比较（is）几乎永远为 True。
        """
        if len(old.children) != len(new.children):
            return True
        for oc, nc in zip(old.children, new.children):
            if oc.content != nc.content or oc.props != nc.props:
                return True
        return False

    # ──────────────────────────────────────────────
    # 流式追加优化
    # ──────────────────────────────────────────────

    def _is_append_only_fast(
        self,
        old_keyed: dict[str, VNode],
        new_keyed: dict[str, VNode],
    ) -> bool:
        """基于已有 keyed 映射快速判断纯追加场景。

        纯追加需同时满足：
        1. 旧树所有 key 在新树中存在
        2. 旧树每个节点的 content/props 与新树对应节点完全一致
           （即旧树节点未发生任何变更，只是新树增加了新节点）

        满足条件时可以跳过全量对比，只生成 INSERT 补丁。
        """
        # 条件1：旧树所有 key 在新树中存在
        for key in old_keyed:
            if key not in new_keyed:
                return False

        # 条件2：旧树每个节点的 content/props 与新版一致（未发生变更）
        for key, old_node in old_keyed.items():
            new_node = new_keyed[key]
            if old_node.content != new_node.content or old_node.props != new_node.props:
                return False

        return True

    def _collect_inserts_only_fast(
        self,
        new_keyed: dict[str, VNode],
        old_keyed: dict[str, VNode],
        new_paths: dict[str, tuple[int, ...]],
        result: VNodeDiffResult,
    ) -> None:
        """纯追加场景：用已有 paths 快速收集 INSERT 补丁。"""
        for key, new_node in new_keyed.items():
            if key not in old_keyed:
                path = new_paths.get(key, ())
                result.add(VPatch(
                    type=PatchType.INSERT,
                    key=key,
                    path=path,
                    node=new_node,
                ))

    # ──────────────────────────────────────────────
    # REORDER 检测
    # ──────────────────────────────────────────────

    def _detect_reorder(
        self,
        old_root: VNode,
        new_root: VNode,
        old_paths: dict[str, tuple[int, ...]],
        new_paths: dict[str, tuple[int, ...]],
        result: VNodeDiffResult,
    ) -> None:
        """递归检测子节点顺序变化，生成 REORDER 补丁。

        对每层子节点，比较旧树和新树中对应位置的 key 序列，
        如果 key 集合相同但顺序不同，则视为重排序。

        Args:
            old_paths: 旧树 key→path 映射，用于 O(1) 路径查找
            new_paths: 新树 key→path 映射，用于 O(1) 路径查找
        """
        # 对根节点及其子节点逐层检查
        self._check_level_order(old_root, new_root, old_paths, new_paths, result)

    def _check_level_order(
        self,
        old_node: VNode,
        new_node: VNode,
        old_paths: dict[str, tuple[int, ...]],
        new_paths: dict[str, tuple[int, ...]],
        result: VNodeDiffResult,
    ) -> None:
        """检查同一层级的子节点顺序。

        Args:
            old_paths: 旧树 key→path 映射，用于 O(1) 路径查找
            new_paths: 新树 key→path 映射，用于 O(1) 路径查找
        """
        old_keys = [c.key for c in old_node.children if c.key]
        new_keys = [c.key for c in new_node.children if c.key]

        # 仅当 key 集合相同但顺序不同时标记 REORDER
        if set(old_keys) == set(new_keys) and old_keys != new_keys:
            # O(1) 查表替代全树 DFS（使用旧树路径）
            path = old_paths.get(old_node.key, ())
            result.add(VPatch(
                type=PatchType.REORDER,
                key=old_node.key,
                path=path,
            ))

        # 递归检查子节点
        old_child_map = {c.key: c for c in old_node.children if c.key}
        new_child_map = {c.key: c for c in new_node.children if c.key}
        common_keys = set(old_child_map.keys()) & set(new_child_map.keys())
        for key in common_keys:
            self._check_level_order(old_child_map[key], new_child_map[key], old_paths, new_paths, result)

    # ──────────────────────────────────────────────
    # 路径计算
    # ──────────────────────────────────────────────

    def _find_path(self, root: VNode, target_key: str,
                   max_depth: int = 100) -> tuple[int, ...]:
        """DFS 查找 target_key 对应的 VNode 路径（索引元组）。

        路径格式：(父级子索引, 子级子索引, ...)，空元组表示根节点自身。

        Args:
            root: 搜索起始根节点
            target_key: 目标节点的 key
            max_depth: 最大递归深度，默认 100，防止超深 VNode 树导致 RecursionError

        Returns:
            从根到目标节点的索引路径元组
        """
        def dfs(node: VNode, path: tuple[int, ...],
                depth: int = 0) -> tuple[int, ...] | None:
            if depth > max_depth:
                return None
            if node.key == target_key:
                return path
            for i, child in enumerate(node.children):
                found = dfs(child, path + (i,), depth + 1)
                if found is not None:
                    return found
            return None

        result = dfs(root, (), 0)
        return result if result is not None else ()

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _collect_keyed(self, root: VNode) -> tuple[dict[str, VNode], dict[str, tuple[int, ...]]]:
        """DFS 扁平化收集所有带 key 的节点到 key→VNode 映射，同时记录路径。

        Args:
            root: 树的根节点

        Returns:
            (key→VNode 映射, key→path 路径映射)
        """
        keyed: dict[str, VNode] = {}
        paths: dict[str, tuple[int, ...]] = {}
        self._dfs_collect(root, (), keyed, paths)
        return keyed, paths

    def _dfs_collect(self, node: VNode, path: tuple[int, ...],
                     keyed: dict[str, VNode],
                     paths: dict[str, tuple[int, ...]]) -> None:
        """递归收集节点及其子节点的 key→VNode 映射和路径。"""
        if node.key:
            keyed[node.key] = node
            paths[node.key] = path
        for i, child in enumerate(node.children):
            self._dfs_collect(child, path + (i,), keyed, paths)

    def _collect_inserts(
        self,
        node: VNode,
        base_path: tuple[int, ...],
        result: VNodeDiffResult,
    ) -> None:
        """首次渲染：收集所有节点作为 INSERT 补丁。"""
        # 跳过根节点自身（根节点不产生补丁）
        for i, child in enumerate(node.children):
            self._collect_insert_node(child, base_path + (i,), result)

    def _collect_insert_node(
        self,
        node: VNode,
        path: tuple[int, ...],
        result: VNodeDiffResult,
    ) -> None:
        """递归收集单个节点及其子孙的 INSERT 补丁。"""
        if node.key:
            result.add(VPatch(
                type=PatchType.INSERT,
                key=node.key,
                path=path,
                node=node,
            ))
        for i, child in enumerate(node.children):
            self._collect_insert_node(child, path + (i,), result)
