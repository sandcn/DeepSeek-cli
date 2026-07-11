"""ToolDAG — 工具调用 DAG 引擎

替代硬编码的四波串行模型（Wave 0-3），实现真正的依赖驱动并行调度：

1. 构建 DAG 节点（每个工具调用一个节点）
2. 三层依赖检测：
   - 显式依赖：参数值中的 ``$tool_call_id`` 引用
   - 隐式依赖：path 参数重叠（读依赖写、写依赖写同文件）
   - 元数据约束：user_select 独占层
3. Kahn 算法拓扑排序 → 分层输出
4. 每层内工具可并发执行，层间串行等待

安全回退：检测到环时返回 ``None``，调用方回退到全串行。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)

# ── 正则：匹配参数值中的 $tool_call_id 引用 ──────────────
# 匹配形如 "$call_xxx" 或 "${call_xxx}" 的引用
_TC_ID_REF_RE = None  # 延迟导入 re


def _compile_re():
    global _TC_ID_REF_RE
    if _TC_ID_REF_RE is None:
        import re
        _TC_ID_REF_RE = re.compile(r"\$(call_[a-zA-Z0-9_]+)")
    return _TC_ID_REF_RE


@dataclass
class ToolCallNode:
    """DAG 中的一个工具调用节点

    Attributes:
        tc_id: tool_call_id（LLM 生成的唯一 ID）
        name: 工具名
        arguments: 参数 dict
        parallel_safe: 是否并行安全（缓存 metadata）
        requires_terminal: 是否需要独占终端
        dependencies: 本节点依赖的 tc_id 集合（入边）
        dependents: 依赖本节点的 tc_id 集合（出边）
        layer: 拓扑层编号（-1 表示未分配）
    """
    tc_id: str
    name: str
    arguments: dict[str, Any]
    parallel_safe: bool
    requires_terminal: bool
    dependencies: set[str] = field(default_factory=set)
    dependents: set[str] = field(default_factory=set)
    layer: int = -1


class ToolDAG:
    """工具调用 DAG — 构建依赖图并执行拓扑排序

    用法::
        dag = ToolDAG(tool_calls, registry)
        layers = dag.topological_sort()
        if layers is not None:
            # 逐层并行执行
            for level_nodes in layers:
                # level_nodes 中的工具可并发执行
                ...
        else:
            # 存在环，回退到全串行
    """

    def __init__(self, tool_calls: list[dict], registry) -> None:
        """构建 DAG

        Args:
            tool_calls: LLM 返回的工具调用列表
                [{"id": str, "name": str, "arguments": dict}, ...]
            registry: ToolRegistry 实例（用于查询 metadata）
        """
        self._nodes: dict[str, ToolCallNode] = {}
        self._original_order: list[str] = [tc["id"] for tc in tool_calls]

        if tool_calls:
            self._build(tool_calls, registry)

    # ── 属性 ────────────────────────────────────────────────

    @property
    def nodes(self) -> dict[str, ToolCallNode]:
        """所有节点，按 tc_id 索引"""
        return dict(self._nodes)

    @property
    def size(self) -> int:
        """节点数量"""
        return len(self._nodes)

    @property
    def original_order(self) -> list[str]:
        """原始 tool_calls 顺序（tc_id 列表），只读"""
        return list(self._original_order)

    # ── 构建 ────────────────────────────────────────────────

    def _build(self, tool_calls: list[dict], registry) -> None:
        """构建 DAG：创建节点 + 三层依赖检测"""
        # 第一遍：创建所有节点
        node_map: dict[str, ToolCallNode] = {}
        for tc in tool_calls:
            tc_id = tc["id"]
            name = tc.get("name", "")
            arguments = tc.get("arguments", {})

            # 查询 metadata
            parallel_safe = False
            requires_terminal = False
            try:
                meta = registry.get_metadata(name)
                if meta is not None:
                    parallel_safe = meta.parallel_safe
                    requires_terminal = meta.requires_terminal
            except Exception:
                _logger.debug("ToolDAG: metadata 查询失败 '%s', 使用默认值", name, exc_info=True)

            node = ToolCallNode(
                tc_id=tc_id,
                name=name,
                arguments=arguments,
                parallel_safe=parallel_safe,
                requires_terminal=requires_terminal,
            )
            node_map[tc_id] = node

        self._nodes = node_map

        # 第二遍：三层依赖检测
        # 层 A：显式依赖（$tool_call_id 引用）
        self._detect_explicit_deps()

        # 层 B：隐式依赖（path 重叠）
        self._detect_path_overlap()

        # 层 C：元数据约束（user_select 独占层）
        self._add_user_select_constraints()

    # ── 显式依赖检测 ───────────────────────────────────────

    def _detect_explicit_deps(self) -> None:
        """检测参数值中的 ``$tool_call_id`` 显式引用"""
        ref_re = _compile_re()
        known_ids = set(self._nodes.keys())

        for tc_id, node in self._nodes.items():
            refs = self._scan_args_for_refs(node.arguments, ref_re)
            for ref_id in refs:
                if ref_id in known_ids and ref_id != tc_id:
                    # 添加依赖边：node 依赖 ref_id
                    node.dependencies.add(ref_id)
                    self._nodes[ref_id].dependents.add(tc_id)

    def _scan_args_for_refs(self, arguments: dict, ref_re) -> set[str]:
        """递归扫描参数值中的所有 ``$tool_call_id`` 引用

        支持嵌套 dict 和 list 中的字符串值。
        """
        refs: set[str] = set()
        for _key, value in arguments.items():
            if isinstance(value, str):
                for m in ref_re.finditer(value):
                    refs.add(m.group(1))
            elif isinstance(value, dict):
                refs |= self._scan_args_for_refs(value, ref_re)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        for m in ref_re.finditer(item):
                            refs.add(m.group(1))
                    elif isinstance(item, dict):
                        refs |= self._scan_args_for_refs(item, ref_re)
        return refs

    # ── 隐式依赖检测（path 重叠） ──────────────────────────

    @staticmethod
    def _extract_tool_paths(name: str, arguments: dict) -> tuple[set[str], set[str]]:
        """提取工具调用涉及的所有路径，按读/写分类并归一化

        Args:
            name: 工具名
            arguments: 工具参数字典

        Returns:
            (write_paths, read_paths): 写入路径集合（含创建/删除/移动），读取路径集合

        各工具的路径提取规则：
        - write_file/update_file/rm/mk → path 为写入路径
        - cp → destination 为写入路径，source 为读取路径
        - mv → destination 为写入路径，source 也为写入路径（源被删除）
        - read_file/search/find/ls → path 为读取路径
        - bash → 无法静态分析，不提取
        """
        write_paths: set[str] = set()
        read_paths: set[str] = set()

        def _normalize(p: str) -> str | None:
            if not isinstance(p, str) or not p.strip():
                return None
            try:
                return os.path.realpath(os.path.abspath(p.strip()))
            except (OSError, ValueError):
                return os.path.abspath(p.strip())

        # ── path 参数 ──
        # write_file/update_file → 写入；rm → 删除；mk → 创建
        # read_file/search/find/ls → 读取
        path_val = arguments.get("path")
        np = _normalize(path_val) if path_val else None
        if np:
            if name in ("read_file", "search", "find", "ls"):
                read_paths.add(np)
            else:
                write_paths.add(np)

        # ── file_path 参数别名（防御性：当前无工具使用此参数名） ──
        fp_val = arguments.get("file_path")
        nfp = _normalize(fp_val) if fp_val else None
        if nfp:
            _logger.debug("_extract_tool_paths: 工具 '%s' 使用了 file_path 参数: '%s'", name, nfp)
            if name in ("read_file", "search", "find", "ls"):
                read_paths.add(nfp)
            else:
                write_paths.add(nfp)

        # ── destination 参数 (cp/mv) → 写入目标 ──
        dest_val = arguments.get("destination")
        nd = _normalize(dest_val) if dest_val else None
        if nd:
            write_paths.add(nd)

        # ── source 参数 (cp/mv) ──
        src_val = arguments.get("source")
        ns = _normalize(src_val) if src_val else None
        if ns:
            if name == "mv":
                # mv 会删除 source → 写入操作（路径被修改）
                write_paths.add(ns)
            else:
                # cp 仅读取 source
                read_paths.add(ns)

        return write_paths, read_paths

    def _detect_path_overlap(self) -> None:
        """检测文件路径重叠导致的隐式依赖（支持多路径工具如 cp/mv）

        规则：
        - 读依赖写（读最新内容）：节点 A 读路径 P，节点 B 写路径 P → A 依赖 B
        - 写依赖写（写顺序保证）：节点 A 写路径 P，节点 B 写路径 P → 后者依赖前者
        - 读依赖读：无依赖（并行安全）
        - 不同路径：无依赖
        - 多路径工具（cp/mv）：每个路径分别参与上述规则判定
        """
        # 第一步：收集每个节点的写入路径和读取路径
        node_write_paths: dict[str, set[str]] = {}
        node_read_paths: dict[str, set[str]] = {}
        has_any_path: set[str] = set()  # 有路径信息的节点

        for tc_id, node in self._nodes.items():
            write_paths, read_paths = self._extract_tool_paths(node.name, node.arguments)
            if write_paths or read_paths:
                node_write_paths[tc_id] = write_paths
                node_read_paths[tc_id] = read_paths
                has_any_path.add(tc_id)

        if not has_any_path:
            return

        # 第二步：构建路径→节点索引，区分写入者和读取者
        # write_index: path → [(tc_id, original_order_index), ...]
        # read_index:  path → [tc_id, ...]
        write_index: dict[str, list[tuple[str, int]]] = {}
        read_index: dict[str, list[str]] = {}

        for tc_id in has_any_path:
            order = self._original_order.index(tc_id) if tc_id in self._original_order else -1

            for wp in node_write_paths.get(tc_id, set()):
                write_index.setdefault(wp, []).append((tc_id, order))

            for rp in node_read_paths.get(tc_id, set()):
                read_index.setdefault(rp, []).append(tc_id)

        all_paths = set(write_index.keys()) | set(read_index.keys())

        # 第三步：对每个路径检测依赖
        for path in all_paths:
            writers = write_index.get(path, [])
            readers = read_index.get(path, [])

            # —— 写依赖写：同路径写入者按原始顺序串行化 ——
            if len(writers) >= 2:
                # 按原始顺序排序
                writers_sorted = sorted(writers, key=lambda x: x[1])
                for k in range(len(writers_sorted) - 1):
                    earlier_id = writers_sorted[k][0]
                    later_id = writers_sorted[k + 1][0]
                    self._nodes[later_id].dependencies.add(earlier_id)
                    self._nodes[earlier_id].dependents.add(later_id)

            # —— 读依赖写：每个读取者依赖所有写入者 ——
            if writers and readers:
                for writer_id, _ in writers:
                    for reader_id in readers:
                        if reader_id != writer_id:
                            self._nodes[reader_id].dependencies.add(writer_id)
                            self._nodes[writer_id].dependents.add(reader_id)

            # read/read → 无依赖（并行安全）

        # —— 第四步：父子路径依赖（父目录创建先于子路径写入/读取） ——
        # 当工具 A 写入路径 P，工具 B 写入/读取路径 Q，
        # 且 P 是 Q 的父目录（Q 路径以 P 为前缀）时，B 依赖 A
        # 确保父目录先创建完毕，子路径操作才能正常执行
        write_path_list = list(write_index.keys())
        for i in range(len(write_path_list)):
            p_parent = write_path_list[i]
            p_parent_with_sep = p_parent + os.sep
            for j in range(len(write_path_list)):
                if i == j:
                    continue
                p_child = write_path_list[j]
                # 检查 p_parent 是否是 p_child 的父目录
                # 使用 p_parent + os.sep 前缀匹配，避免将自身误判为子路径
                if p_child.startswith(p_parent_with_sep):
                    # p_parent 中的写入者是 p_child 中写入者/读取者的前置依赖
                    parent_writers = write_index[p_parent]
                    child_writers = write_index.get(p_child, [])
                    child_readers = read_index.get(p_child, [])

                    # 子路径写入者依赖父路径写入者
                    for cw_id, _ in child_writers:
                        for pw_id, _ in parent_writers:
                            if cw_id != pw_id:
                                self._nodes[cw_id].dependencies.add(pw_id)
                                self._nodes[pw_id].dependents.add(cw_id)

                    # 子路径读取者依赖父路径写入者
                    for cr_id in child_readers:
                        for pw_id, _ in parent_writers:
                            if cr_id != pw_id:
                                self._nodes[cr_id].dependencies.add(pw_id)
                                self._nodes[pw_id].dependents.add(cr_id)

    # ── user_select 独占层约束 ──────────────────────────────

    def _add_user_select_constraints(self) -> None:
        """user_select 节点需要独占终端，
        因此与所有其他节点建立依赖关系（所有其他节点依赖 user_select）。

        这确保 user_select 独占一层。
        """
        user_select_ids = [
            tc_id for tc_id, node in self._nodes.items()
            if node.name == "user_select"
        ]
        non_user_ids = [
            tc_id for tc_id in self._nodes
            if tc_id not in user_select_ids
        ]

        for us_id in user_select_ids:
            us_node = self._nodes[us_id]
            for other_id in non_user_ids:
                # 其他所有节点依赖 user_select（user_select 先独占终端执行）
                self._nodes[other_id].dependencies.add(us_id)
                us_node.dependents.add(other_id)

    # ── 环检测 ──────────────────────────────────────────────

    def has_cycle(self) -> bool:
        """检测 DAG 是否存在环

        使用 DFS 着色法：
        - 0 = 未访问
        - 1 = 正在访问（当前路径中）
        - 2 = 已完成访问
        """
        color: dict[str, int] = {tc_id: 0 for tc_id in self._nodes}

        def _dfs(tc_id: str) -> bool:
            if color[tc_id] == 1:
                return True  # 环
            if color[tc_id] == 2:
                return False  # 已确认无环
            color[tc_id] = 1
            for dep_id in self._nodes[tc_id].dependents:  # 沿出边遍历
                if _dfs(dep_id):
                    return True
            color[tc_id] = 2
            return False

        for tc_id in self._nodes:
            if color[tc_id] == 0:
                if _dfs(tc_id):
                    return True
        return False

    # ── 拓扑排序（Kahn 算法） ───────────────────────────────

    def topological_sort(self) -> list[list[str]] | None:
        """Kahn 算法拓扑排序

        Returns:
            - ``list[list[str]]``: 按层分组的 tc_id 列表。
              每层内的工具调用无依赖关系，可并发执行。
            - ``None``: 存在环，无法拓扑排序
        """
        if not self._nodes:
            return []

        # 计算入度
        in_degree: dict[str, int] = {tc_id: 0 for tc_id in self._nodes}
        for tc_id, node in self._nodes.items():
            for dep_id in node.dependencies:
                if dep_id in in_degree:
                    in_degree[tc_id] += 1

        # 收集入度为 0 的节点
        queue: list[str] = [
            tc_id for tc_id, deg in in_degree.items() if deg == 0
        ]

        layers: list[list[str]] = []
        processed: int = 0

        while queue:
            current_layer: list[str] = list(queue)
            layers.append(current_layer)
            queue.clear()

            for tc_id in current_layer:
                processed += 1
                # 更新入度：移除本节点，其所有后继入度 -1
                for dep_id in self._nodes[tc_id].dependents:
                    if dep_id in in_degree:
                        in_degree[dep_id] -= 1
                        if in_degree[dep_id] == 0:
                            if dep_id not in queue and dep_id not in [
                                x for layer in layers for x in layer
                            ]:
                                queue.append(dep_id)

        # 检查是否所有节点都已处理
        if processed != len(self._nodes):
            _logger.warning("ToolDAG: 拓扑排序未处理所有节点 (processed=%d, total=%d)，存在环",
                           processed, len(self._nodes))
            return None

        # 分配层号
        for layer_idx, layer in enumerate(layers):
            for tc_id in layer:
                if tc_id in self._nodes:
                    self._nodes[tc_id].layer = layer_idx

        return layers

    # ── 工具方法 ────────────────────────────────────────────

    def get_node(self, tc_id: str) -> ToolCallNode | None:
        """按 tc_id 获取节点"""
        return self._nodes.get(tc_id)

    def get_level(self, tc_id: str) -> int:
        """获取节点的拓扑层号，未分配返回 -1"""
        node = self._nodes.get(tc_id)
        return node.layer if node is not None else -1
