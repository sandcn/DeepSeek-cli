"""ast.optimizer — AST 优化器：合并/简化/裁剪节点。

减少 AST 树深度和节点数量，提高下游渲染效率。
所有优化策略可独立开关，默认全开（merge_code_blocks 除外）。
"""

from __future__ import annotations

from typing import Any

from .types import ASTNode, NodeType, SourceRange


class ASTOptimizer:
    """AST 优化器——合并/简化/裁剪节点。

    优化策略（可配置）：
      - merge_paragraphs: 合并连续 PARAGRAPH → 一个节点
      - wrap_sections: 将 HEADING + 后续块 → SECTION 父节点
      - strip_empty: 移除空 EMPTY_LINE 节点（除非在列表/代码块中）
      - merge_code_blocks: 合并连续同语言 CODE_BLOCK
      - normalize_lists: 确保 LIST_ITEM 都在 LIST/ORDERED_LIST 父节点下

    用法：
      optimizer = ASTOptimizer()
      optimized = optimizer.optimize(root_node)
    """

    def __init__(self, **options: Any) -> None:
        """初始化优化器，可传入选项覆盖默认值。

        Args:
            **options: 优化策略开关，如 merge_paragraphs=True 等。
        """
        self._options: dict[str, bool] = {
            "merge_paragraphs": True,
            "wrap_sections": True,
            "strip_empty": True,
            "merge_code_blocks": False,  # 流式场景通常不合并代码块
            "normalize_lists": True,
        }
        self._options.update(options)

    # ═══════════════════════════════════════════════════════
    # 公开接口
    # ═══════════════════════════════════════════════════════

    def optimize(self, node: ASTNode) -> ASTNode:
        """优化 AST 树，返回优化后的根节点。

        Args:
            node: 原始 AST 根节点。

        Returns:
            优化后的新 AST 根节点（默认原地修改，inplace=False 时不修改原始输入）。
        """
        if not self._options.get("inplace", True):
            node = self._deep_copy(node)
        node = self._optimize_recursive(node)
        return node

    # ═══════════════════════════════════════════════════════
    # 内部递归
    # ═══════════════════════════════════════════════════════

    def _optimize_recursive(self, node: ASTNode) -> ASTNode:
        """递归优化子树（后序遍历：先优化子节点，再处理当前层）。

        优化：将多遍扫描合并为单遍流水线，所有优化策略在一次遍历中完成。
        """
        opts = self._options

        # 1. 先优化子节点（后序遍历）
        optimized_children: list[ASTNode] = []
        for child in node.children:
            optimized = self._optimize_recursive(child)
            optimized_children.append(optimized)
        node.children = optimized_children

        # 2. 单遍流水线：合并所有优化策略为一次遍历
        children = node.children
        if not children:
            return node

        merged: list[ASTNode] = []
        i = 0
        n = len(children)

        # ★ 局部字典：管理临时缓存，避免在 dataclass 实例上动态设置属性
        _para_parts_cache: dict[int, list[str]] = {}
        _code_parts_cache: dict[int, list[str]] = {}

        while i < n:
            child = children[i]

            # ── strip_empty：跳过空行和空段落 ──
            if opts.get("strip_empty") and node.type not in (
                    NodeType.CODE_BLOCK, NodeType.LIST, NodeType.ORDERED_LIST):
                # 跳过 EMPTY_LINE
                if child.type is NodeType.EMPTY_LINE:
                    i += 1
                    continue
                # 跳过空内容且无子节点的 PARAGRAPH
                if (child.type is NodeType.PARAGRAPH
                        and not child.content.strip()
                        and not child.children):
                    i += 1
                    continue

            # ── merge_paragraphs：合并连续段落 ──
            if (opts.get("merge_paragraphs")
                    and child.type is NodeType.PARAGRAPH
                    and merged
                    and merged[-1].type is NodeType.PARAGRAPH):
                prev = merged[-1]
                # ★ 优化：改用 list 暂存 + 单次 join，避免 O(n²) 字符串复制
                # 将 prev 的 content 作为第一段，后续合并先缓存到临时 list
                # 使用局部字典代替动态属性，避免 dataclass __slots__ 兼容问题
                prev_id = id(prev)
                if prev_id not in _para_parts_cache:
                    _para_parts_cache[prev_id] = [prev.content]
                _para_parts_cache[prev_id].append(child.content)
                prev.children.extend(child.children)
                i += 1
                continue

            # ── merge_code_blocks：合并连续同语言代码块 ──
            if (opts.get("merge_code_blocks")
                    and child.type is NodeType.CODE_BLOCK
                    and merged
                    and merged[-1].type is NodeType.CODE_BLOCK
                    and merged[-1].meta.get("lang", "") == child.meta.get("lang", "")):
                prev = merged[-1]
                # 使用局部字典代替动态属性，避免 dataclass __slots__ 兼容问题
                prev_id = id(prev)
                if prev_id not in _code_parts_cache:
                    _code_parts_cache[prev_id] = [prev.content]
                _code_parts_cache[prev_id].append(child.content)
                for k, v in child.meta.items():
                    if k not in prev.meta or not prev.meta.get(k):
                        prev.meta[k] = v
                i += 1
                continue

            merged.append(child)
            i += 1
        
        # ★ 优化：段落合并后处理——将所有暂存的段落片段一次性 join
        for n in merged:
            nid = id(n)
            if nid in _para_parts_cache:
                n.content = "\n\n".join(_para_parts_cache[nid])

        # ★ 优化：代码块合并后处理——单次 join
        for n in merged:
            nid = id(n)
            if nid in _code_parts_cache:
                n.content = "\n".join(_code_parts_cache[nid])

        # 清理缓存
        _para_parts_cache.clear()
        _code_parts_cache.clear()

        # ── normalize_lists：确保 LIST_ITEM 在 LIST 父节点下 ──
        if opts.get("normalize_lists") and node.type not in (
                NodeType.LIST, NodeType.ORDERED_LIST):
            normalized: list[ASTNode] = []
            j = 0
            m = len(merged)
            while j < m:
                child = merged[j]
                if child.type is NodeType.LIST_ITEM:
                    items: list[ASTNode] = [child]
                    j += 1
                    while j < m and merged[j].type is NodeType.LIST_ITEM:
                        items.append(merged[j])
                        j += 1
                    # 根据第一个 LIST_ITEM 的 bullet 属性判断有序/无序
                    is_ordered = not items[0].meta.get('bullet', True)
                    list_type = NodeType.ORDERED_LIST if is_ordered else NodeType.LIST
                    list_node = ASTNode(list_type, children=items)
                    normalized.append(list_node)
                else:
                    normalized.append(child)
                    j += 1
            merged = normalized

        # ── wrap_sections：将 HEADING + 后续块包装为 SECTION ──
        if opts.get("wrap_sections"):
            wrapped: list[ASTNode] = []
            k = 0
            m = len(merged)
            while k < m:
                child = merged[k]
                if child.type is NodeType.HEADING:
                    section = ASTNode(NodeType.SECTION, children=[child])
                    k += 1
                    while k < m and merged[k].type is not NodeType.HEADING:
                        section.children.append(merged[k])
                        k += 1
                    wrapped.append(section)
                else:
                    wrapped.append(child)
                    k += 1
            merged = wrapped

        node.children = merged
        return node

    # ═══════════════════════════════════════════════════════
    # 工具方法
    # ═══════════════════════════════════════════════════════

    @staticmethod
    def _deep_copy(node: ASTNode) -> ASTNode:
        """手动浅拷贝 AST 树，确保不修改原始输入。

        用逐字段构造替代 copy.deepcopy，避免大 AST 树深拷贝的性能瓶颈。
        """
        new_node = ASTNode(
            type=node.type,
            content=node.content,
            meta=dict(node.meta),
        )
        if node.range is not None:
            new_node.range = SourceRange(
                start=node.range.start,
                end=node.range.end,
                line_start=node.range.line_start,
                line_end=node.range.line_end,
            )
        for child in node.children:
            new_node.add_child(ASTOptimizer._deep_copy(child))
        return new_node
