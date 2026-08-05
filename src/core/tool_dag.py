"""ToolDAG — 工具调用 DAG 引擎

替代硬编码的四波串行模型（Wave 0-3），实现真正的依赖驱动并行调度：

1. 构建 DAG 节点（每个工具调用一个节点）
2. 四层依赖检测：
   - 显式依赖：参数值中的 ``$tool_call_id`` 引用
   - 隐式依赖：path 参数重叠（读依赖写、写依赖写同文件）
   - 元数据约束：user_select 独占层
   - 工具类别约束：bash 隔离/read→write/bash 链式
3. Kahn 算法拓扑排序 → 分层输出
4. 每层内工具可并发执行，层间串行等待

检测到环时 ``topological_sort()`` 返回 ``None``，调用方并发执行剩余工具。
"""

from __future__ import annotations

import logging
import os
from collections import deque
from dataclasses import dataclass, field
from typing import Any

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
        tool_category: 调度约束分类（"read"/"write"/"bash"/"interactive"/"general"），默认 "general"
        dependencies: 本节点依赖的 tc_id 集合（入边）
        dependents: 依赖本节点的 tc_id 集合（出边）
        layer: 拓扑层编号（-1 表示未分配）
    """
    tc_id: str
    name: str
    arguments: dict[str, Any]
    parallel_safe: bool
    requires_terminal: bool
    tool_category: str = "general"
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
            # 存在环，并发执行
    """

    # ── LRU 缓存常量 ─────────────────────────────────────────
    _PATH_CACHE_MAX = 4096  # _path_exists 缓存最大条目数

    def __init__(self, tool_calls: list[dict], registry) -> None:
        """构建 DAG

        Args:
            tool_calls: LLM 返回的工具调用列表
                [{"id": str, "name": str, "arguments": dict}, ...]
            registry: ToolRegistry 实例（用于查询 metadata）
        """
        self._nodes: dict[str, ToolCallNode] = {}
        self._original_order: list[str] = [tc["id"] for tc in tool_calls]
        self._path_cache: dict[tuple[str, str], bool] = {}

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
        """构建 DAG：创建节点 + 四层依赖检测"""
        # 第一遍：创建所有节点
        self._build_create_nodes(tool_calls, registry)

        # 第二遍：四层依赖检测
        # 层 A：显式依赖（$tool_call_id 引用）
        self._detect_explicit_deps()

        # 层 B：隐式依赖（path 重叠）
        self._detect_path_overlap()

        # 层 C：元数据约束（user_select 独占层）
        self._add_user_select_constraints()

        # 层 D：工具类别约束（bash 隔离 / read→write / bash 链式）
        self._detect_tool_category_constraints()

    def _build_create_nodes(self, tool_calls: list[dict], registry) -> dict[str, ToolCallNode]:
        """创建 DAG 节点（不执行依赖检测）

        遍历 tool_calls 列表，为每个工具调用创建 ToolCallNode，
        查询 metadata 并存入 self._nodes。

        Args:
            tool_calls: 工具调用列表
                [{"id": str, "name": str, "arguments": dict}, ...]
            registry: ToolRegistry 实例（用于查询 metadata）

        Returns:
            新创建的节点映射 {tc_id: ToolCallNode}
        """
        created: dict[str, ToolCallNode] = {}
        for tc in tool_calls:
            tc_id = tc["id"]
            name = tc.get("name", "")
            arguments = tc.get("arguments", {})

            # 查询 metadata
            parallel_safe = False
            requires_terminal = False
            tool_category = "general"
            try:
                meta = registry.get_metadata(name)
                if meta is not None:
                    parallel_safe = meta.parallel_safe
                    requires_terminal = meta.requires_terminal
                    tool_category = getattr(meta, 'tool_category', 'general')
            except Exception:
                _logger.debug("ToolDAG: metadata 查询失败 '%s', 使用默认值", name, exc_info=True)

            node = ToolCallNode(
                tc_id=tc_id,
                name=name,
                arguments=arguments,
                parallel_safe=parallel_safe,
                requires_terminal=requires_terminal,
                tool_category=tool_category,
            )
            self._nodes[tc_id] = node
            created[tc_id] = node

        return created

    # ── 缓存管理 ────────────────────────────────────────────

    def _clear_path_cache(self) -> None:
        """清除 _path_cache 全部条目

        在 add_batch（新增节点）和 remove_nodes（删除节点）后调用，
        确保缓存一致性——旧缓存条目可能不包含新节点的连通信息，
        或引用了已被删除的节点。
        """
        self._path_cache.clear()

    def add_batch(self, new_tool_calls: list[dict], registry,
                  prev_non_dispatch_ids: set[str] | None = None) -> None:
        """扩展 DAG：添加一批新的 tool_calls，建立跨批依赖

        将新一批工具调用追加到当前 DAG 中：
        1. 为新工具创建节点
        2. 扩展 original_order
        2.5. 清除路径缓存（在依赖重检之前，确保 _path_exists 不命中 stale 条目）
        3. 重跑全部四层依赖检测（set 天然去重，重复边无副作用）
        4. 添加批间依赖边：prev_non_dispatch_ids → 所有新节点

        Args:
            new_tool_calls: 新批次的工具调用列表
                [{"id": str, "name": str, "arguments": dict}, ...]
            registry: ToolRegistry 实例（用于查询 metadata）
            prev_non_dispatch_ids: 上一批中非 dispatch_agent 的 tc_id 集合。
                None 表示不添加批间依赖边。
        """
        if not new_tool_calls:
            return

        # 第 1 步：创建新节点
        new_nodes = self._build_create_nodes(new_tool_calls, registry)

        # 第 2 步：扩展 original_order
        self._original_order.extend(tc["id"] for tc in new_tool_calls)

        # 第 2.5 步：清除缓存（在依赖重检之前，确保 _path_exists 不命中 stale 条目）
        self._clear_path_cache()

        # 第 3 步：重跑全部四层依赖检测
        # set 天然去重，现有边被再次添加是无操作的（无副作用）
        self._detect_explicit_deps()
        self._detect_path_overlap()
        self._add_user_select_constraints()
        self._detect_tool_category_constraints()

        # 第 4 步：添加批间依赖边
        if prev_non_dispatch_ids:
            new_ids = set(new_nodes.keys())
            for new_id in new_ids:
                for prev_id in prev_non_dispatch_ids:
                    if prev_id in self._nodes and prev_id != new_id:
                        self._nodes[new_id].dependencies.add(prev_id)
                        self._nodes[prev_id].dependents.add(new_id)

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
    def _extract_tool_paths(name: str, arguments: dict) -> tuple[set[str], set[str], set[str]]:
        """提取工具调用涉及的所有路径，按读/写/删分类并归一化

        Args:
            name: 工具名
            arguments: 工具参数字典

        Returns:
            (write_paths, read_paths, delete_paths): 写入路径集合、读取路径集合、删除路径集合

        各工具的路径提取规则：
        - write_file/update_file/mk → path 为写入路径
        - rm → path 为删除路径（文件被移除，非写入）
        - cp → destination 为写入路径，source 为读取路径
        - mv → destination 为写入路径，source 为删除路径（源被移除）
        - read_file/search/find/ls → path 为读取路径
        - bash → 无法静态分析，不提取
        """
        write_paths: set[str] = set()
        read_paths: set[str] = set()
        delete_paths: set[str] = set()

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
            elif name == "rm":
                delete_paths.add(np)
            else:
                write_paths.add(np)

        # ── file_path 参数别名（防御性：当前无工具使用此参数名） ──
        fp_val = arguments.get("file_path")
        nfp = _normalize(fp_val) if fp_val else None
        if nfp:
            _logger.debug("_extract_tool_paths: 工具 '%s' 使用了 file_path 参数: '%s'", name, nfp)
            if name in ("read_file", "search", "find", "ls"):
                read_paths.add(nfp)
            elif name == "rm":
                delete_paths.add(nfp)
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
                # mv 会删除 source → 删除路径
                delete_paths.add(ns)
            else:
                # cp 仅读取 source
                read_paths.add(ns)

        return write_paths, read_paths, delete_paths

    # ── 子方法：隐式依赖路径检测 ────────────────────────────

    def _collect_node_paths(self) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], set[str]]:
        """收集每个节点的写入/读取/删除路径

        Returns:
            (node_write_paths, node_read_paths, node_delete_paths, has_any_path):
                node_write_paths: tc_id → 写入路径集合
                node_read_paths:  tc_id → 读取路径集合
                node_delete_paths: tc_id → 删除路径集合
                has_any_path:    有路径信息的 tc_id 集合
        """
        node_write_paths: dict[str, set[str]] = {}
        node_read_paths: dict[str, set[str]] = {}
        node_delete_paths: dict[str, set[str]] = {}
        has_any_path: set[str] = set()

        for tc_id, node in self._nodes.items():
            write_paths, read_paths, delete_paths = self._extract_tool_paths(node.name, node.arguments)
            if write_paths or read_paths or delete_paths:
                node_write_paths[tc_id] = write_paths
                node_read_paths[tc_id] = read_paths
                node_delete_paths[tc_id] = delete_paths
                has_any_path.add(tc_id)

        return node_write_paths, node_read_paths, node_delete_paths, has_any_path

    def _build_path_indexes(
        self,
        node_write_paths: dict[str, set[str]],
        node_read_paths: dict[str, set[str]],
        node_delete_paths: dict[str, set[str]],
        has_any_path: set[str],
    ) -> tuple[dict[str, list[tuple[str, int]]], dict[str, list[str]], dict[str, list[tuple[str, int]]], set[str]]:
        """构建路径→节点倒排索引

        Args:
            node_write_paths: _collect_node_paths 返回的写入路径
            node_read_paths:  _collect_node_paths 返回的读取路径
            node_delete_paths: _collect_node_paths 返回的删除路径
            has_any_path:     有路径信息的 tc_id 集合

        Returns:
            (write_index, read_index, delete_index, all_paths):
                write_index: path → [(tc_id, original_order_index), ...]
                read_index:  path → [tc_id, ...]
                delete_index: path → [(tc_id, original_order_index), ...]
                all_paths:   所有出现过的路径集合
        """
        write_index: dict[str, list[tuple[str, int]]] = {}
        read_index: dict[str, list[str]] = {}
        delete_index: dict[str, list[tuple[str, int]]] = {}

        for tc_id in has_any_path:
            order = self._original_order.index(tc_id) if tc_id in self._original_order else -1

            for wp in node_write_paths.get(tc_id, set()):
                write_index.setdefault(wp, []).append((tc_id, order))

            for rp in node_read_paths.get(tc_id, set()):
                read_index.setdefault(rp, []).append(tc_id)

            for dp in node_delete_paths.get(tc_id, set()):
                delete_index.setdefault(dp, []).append((tc_id, order))

        all_paths = set(write_index.keys()) | set(read_index.keys()) | set(delete_index.keys())
        return write_index, read_index, delete_index, all_paths

    def _add_path_deps_for_single_path(
        self,
        path: str,
        writers: list[tuple[str, int]],
        readers: list[str],
        deleters: list[tuple[str, int]],
    ) -> None:
        """对单个路径检测并添加依赖边

        规则：
        - 读依赖写：每个读取者依赖所有写入者
        - 删除依赖读：删除者依赖读取者（读取者先读，删除者再删除）
        - 写/删混合串行化：同路径的写入者和删除者按 original_order 串行

        Args:
            path:      当前正在处理的路径（仅用于日志/上下文）
            writers:   [(tc_id, original_order_index), ...]
            readers:   [tc_id, ...]
            deleters:  [(tc_id, original_order_index), ...]
        """
        # —— 读依赖写：每个读取者依赖所有写入者 ——
        if writers and readers:
            for writer_id, _ in writers:
                for reader_id in readers:
                    if reader_id != writer_id:
                        self._nodes[reader_id].dependencies.add(writer_id)
                        self._nodes[writer_id].dependents.add(reader_id)

        # —— 删除依赖读：deleter 依赖 reader（reader 先读，deleter 再删除） ——
        if deleters and readers:
            for deleter_id, _ in deleters:
                for reader_id in readers:
                    if reader_id != deleter_id:
                        self._nodes[deleter_id].dependencies.add(reader_id)
                        self._nodes[reader_id].dependents.add(deleter_id)

        # —— 写/删混合串行化：同路径的写入者和删除者按原始顺序串行 ——
        # 处理场景：write+write、delete+delete、delete+write（write+delete）
        # 将 writers 和 deleters 合并，统一按 original_order 排序串行
        mutators = writers + deleters
        if len(mutators) >= 2:
            mutators_sorted = sorted(mutators, key=lambda x: x[1])
            for k in range(len(mutators_sorted) - 1):
                earlier_id = mutators_sorted[k][0]
                later_id = mutators_sorted[k + 1][0]
                self._nodes[later_id].dependencies.add(earlier_id)
                self._nodes[earlier_id].dependents.add(later_id)

    def _add_parent_child_deps(
        self,
        write_index: dict[str, list[tuple[str, int]]],
        read_index: dict[str, list[str]],
        delete_index: dict[str, list[tuple[str, int]]],
    ) -> None:
        """检测父子路径依赖并添加依赖边

        当工具 A 写入路径 P，工具 B 写入/读取/删除路径 Q，
        且 P 是 Q 的父目录（Q 路径以 P 为前缀）时，B 依赖 A。
        确保父目录先创建完毕，子路径操作才能正常执行。

        规则：
        - 子路径写入者依赖父路径写入者
        - 子路径读取者依赖父路径写入者
        - 子路径删除者依赖父路径写入者（父目录先存在，才能删除其子文件）
        - 仅 write_index 中的路径作为父路径（delete 路径不产生创建语义）
        - 跳过父路径自身（防止将父路径自身误判为子路径）

        Args:
            write_index:  path → [(tc_id, original_order_index), ...]
            read_index:   path → [tc_id, ...]
            delete_index: path → [(tc_id, original_order_index), ...]
        """
        write_path_list = list(write_index.keys())
        all_child_paths = list(set(write_path_list) | set(read_index.keys()) | set(delete_index.keys()))
        for i in range(len(write_path_list)):
            p_parent = write_path_list[i]
            p_parent_with_sep = p_parent + os.sep
            for j in range(len(all_child_paths)):
                p_child = all_child_paths[j]
                # 跳过父路径自身（防止将父路径自身误判为子路径）
                if p_child == p_parent:
                    continue
                # 检查 p_parent 是否是 p_child 的父目录
                if p_child.startswith(p_parent_with_sep):
                    # p_parent 中的写入者是 p_child 中写入者/读取者/删除者的前置依赖
                    parent_writers = write_index[p_parent]
                    child_writers = write_index.get(p_child, [])
                    child_readers = read_index.get(p_child, [])
                    child_deleters = delete_index.get(p_child, [])

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

                    # 子路径删除者依赖父路径写入者（父目录先存在，才能删除其子文件）
                    for cd_id, _ in child_deleters:
                        for pw_id, _ in parent_writers:
                            if cd_id != pw_id:
                                self._nodes[cd_id].dependencies.add(pw_id)
                                self._nodes[pw_id].dependents.add(cd_id)

    def _detect_path_overlap(self) -> None:
        """检测文件路径重叠导致的隐式依赖（支持多路径工具如 cp/mv）

        规则：
        - 读依赖写（读最新内容）：节点 A 读路径 P，节点 B 写路径 P → A 依赖 B
        - 写依赖写（写顺序保证）：节点 A 写路径 P，节点 B 写路径 P → 后者依赖前者
        - 删除依赖读（删除前确保读取完成）：节点 A 删路径 P，节点 B 读路径 P → A 依赖 B
        - 写/删混合串行化：同路径的写入、删除按原始顺序串行
        - 删除依赖删除：同路径的多次删除按原始顺序串行
        - 读依赖读：无依赖（并行安全）
        - 不同路径：无依赖
        - 多路径工具（cp/mv）：每个路径分别参与上述规则判定
        - 父子路径：仅写入类路径作为父路径（删除类路径不含创建语义，不触发父子依赖）

        委托给 4 个子方法按顺序执行：
        1. _collect_node_paths() — 收集所有节点的路径信息
        2. _build_path_indexes() — 构建路径→节点倒排索引
        3. _add_path_deps_for_single_path() — 对每个路径检测并添加依赖边
        4. _add_parent_child_deps() — 检测父子路径依赖
        """
        node_write_paths, node_read_paths, node_delete_paths, has_any_path = self._collect_node_paths()
        if not has_any_path:
            return

        write_index, read_index, delete_index, all_paths = self._build_path_indexes(
            node_write_paths, node_read_paths, node_delete_paths, has_any_path)

        for path in all_paths:
            writers = write_index.get(path, [])
            readers = read_index.get(path, [])
            deleters = delete_index.get(path, [])
            self._add_path_deps_for_single_path(path, writers, readers, deleters)

        self._add_parent_child_deps(write_index, read_index, delete_index)

    # ── user_select 独占层约束 ──────────────────────────────

    def _add_user_select_constraints(self) -> None:
        """user_select 节点需要独占终端，
        因此与所有其他节点建立依赖关系（所有其他节点依赖 user_select）。

        这确保 user_select 独占一层。
        多个 user_select 节点之间按原始顺序串行化（Bug #2 修复），
        确保不会在同一层并发执行。
        """
        user_select_ids = [
            tc_id for tc_id, node in self._nodes.items()
            if node.name == "user_select"
        ]
        non_user_ids = [
            tc_id for tc_id in self._nodes
            if tc_id not in user_select_ids
        ]

        # —— 多个 user_select 节点串行化（按原始顺序） ——
        # user_select[i] → user_select[i+1]，确保各占一层
        if len(user_select_ids) >= 2:
            # 按 _original_order 排序保证确定性的层顺序
            us_sorted = sorted(
                user_select_ids,
                key=lambda tid: self._original_order.index(tid) if tid in self._original_order else -1,
            )
            for i in range(len(us_sorted) - 1):
                earlier_id = us_sorted[i]
                later_id = us_sorted[i + 1]
                self._nodes[later_id].dependencies.add(earlier_id)
                self._nodes[earlier_id].dependents.add(later_id)

        for us_id in user_select_ids:
            us_node = self._nodes[us_id]
            for other_id in non_user_ids:
                # 其他所有节点依赖 user_select（user_select 先独占终端执行）
                self._nodes[other_id].dependencies.add(us_id)
                us_node.dependents.add(other_id)

    # ── 工具类别约束检测 ───────────────────────────────────

    def _path_exists(self, from_id: str, to_id: str) -> bool:
        """BFS 可达性检测：判断从 from_id 出发能否到达 to_id

        沿出边 (dependents) 遍历 DAG。若存在路径（含直接边和间接路径）返回 True。
        节点不存在或不可达返回 False。

        使用 LRU 缓存（手动 dict）加速重复查询：
        - 节点不存在 → 不缓存（节点状态可能变化）
        - from_id == to_id → 不缓存（短路判断）
        - 其他结果 → 写入 _path_cache（不超过 _PATH_CACHE_MAX 条目）

        Args:
            from_id: 起始节点 tc_id
            to_id: 目标节点 tc_id
        """
        # 节点不存在：直接返回 False，不缓存（节点状态可能变化）
        if from_id not in self._nodes or to_id not in self._nodes:
            return False
        # 自环：直接返回 True，不缓存（短路判断无需缓存）
        if from_id == to_id:
            return True

        # ── 查缓存 ──
        key = (from_id, to_id)
        if key in self._path_cache:
            return self._path_cache[key]

        # ── BFS 遍历 ──
        visited: set[str] = set()
        queue: deque[str] = deque([from_id])
        visited.add(from_id)

        while queue:
            current = queue.popleft()
            for dep_id in self._nodes[current].dependents:
                if dep_id == to_id:
                    # 写缓存后返回
                    if len(self._path_cache) < self._PATH_CACHE_MAX:
                        self._path_cache[key] = True
                    return True
                if dep_id not in visited:
                    visited.add(dep_id)
                    queue.append(dep_id)

        # 不可达：写缓存后返回
        if len(self._path_cache) < self._PATH_CACHE_MAX:
            self._path_cache[key] = False
        return False

    # ── 子方法：工具类别约束检测 ────────────────────────────

    def _classify_tool_nodes(self) -> tuple[list[str], list[str], list[str], list[str]]:
        """按 tool_category 分类所有节点

        遍历 self._original_order，根据每个节点的 tool_category 分类：
        - read_nodes:     类别为 read 的 tc_id 列表（按原始顺序）
        - write_nodes:    类别为 write 的 tc_id 列表（按原始顺序）
        - bash_nodes:     类别为 bash 的 tc_id 列表（按原始顺序）
        - non_bash_nodes: 类别为 read/write/interactive 的 tc_id 列表（不含 general）

        Returns:
            (read_nodes, write_nodes, bash_nodes, non_bash_nodes) 四元组
        """
        read_nodes: list[str] = []
        write_nodes: list[str] = []
        bash_nodes: list[str] = []
        non_bash_nodes: list[str] = []

        for tc_id in self._original_order:
            node = self._nodes.get(tc_id)
            if node is None:
                continue
            cat = node.tool_category
            if cat == "read":
                read_nodes.append(tc_id)
                non_bash_nodes.append(tc_id)
            elif cat == "write":
                write_nodes.append(tc_id)
                non_bash_nodes.append(tc_id)
            elif cat == "bash":
                bash_nodes.append(tc_id)
            elif cat == "interactive":
                non_bash_nodes.append(tc_id)
            # cat == "general": 不加入任何分类列表，不参与约束

        _logger.debug(
            "ToolDAG: 类别约束分类 — read=%d, write=%d, bash=%d, interactive=%d",
            len(read_nodes), len(write_nodes), len(bash_nodes),
            len(non_bash_nodes) - len(read_nodes) - len(write_nodes),
        )

        return read_nodes, write_nodes, bash_nodes, non_bash_nodes

    def _add_bash_non_bash_constraints(self, bash_nodes: list[str], non_bash_nodes: list[str]) -> None:
        """添加 bash ↔ non-bash 约束

        规则 a — bash ↔ non-bash 双向隔断：
            non-bash（read/write/interactive）在 bash 之前执行。
            若已有 non-bash→bash 路径（防环）或 bash→non-bash 路径（已有依赖），跳过。
        规则 b — bash → bash 链式串行：
            多个 bash 按原始顺序链式依赖，一次只运行一个 bash。

        Args:
            bash_nodes:     按原始顺序排列的 bash 节点 tc_id 列表
            non_bash_nodes: 按原始顺序排列的 non-bash 节点 tc_id 列表
        """
        # ── 规则 a：bash ↔ non-bash 双向隔断（non-bash → bash） ──
        if bash_nodes and non_bash_nodes:
            for bash_id in bash_nodes:
                for non_bash_id in non_bash_nodes:
                    # 防环：已有 non-bash→bash 或 bash→non-bash 路径时跳过
                    if self._path_exists(non_bash_id, bash_id):
                        continue
                    if self._path_exists(bash_id, non_bash_id):
                        continue
                    # 添加 non-bash → bash 边（bash 在所有 non-bash 之后执行）
                    self._nodes[bash_id].dependencies.add(non_bash_id)
                    self._nodes[non_bash_id].dependents.add(bash_id)

        # ── 规则 b：bash → bash 链式串行（按原始顺序） ──
        if len(bash_nodes) >= 2:
            for i in range(len(bash_nodes) - 1):
                earlier_id = bash_nodes[i]
                later_id = bash_nodes[i + 1]
                self._nodes[later_id].dependencies.add(earlier_id)
                self._nodes[earlier_id].dependents.add(later_id)

    def _add_read_write_constraints(self, read_nodes: list[str], write_nodes: list[str]) -> None:
        """添加 read → write 约束

        规则 c — read → write 默认边：
            read 类工具在 write 类工具之前执行。
            若已有 write→read 路径（防环）或已有 read→write 路径，跳过。

        Args:
            read_nodes:  按原始顺序排列的 read 节点 tc_id 列表
            write_nodes: 按原始顺序排列的 write 节点 tc_id 列表
        """
        if read_nodes and write_nodes:
            for read_id in read_nodes:
                for write_id in write_nodes:
                    # 防环：已有 write→read 路径时跳过
                    if self._path_exists(write_id, read_id):
                        continue
                    # 已有 read→write 路径时跳过
                    if self._path_exists(read_id, write_id):
                        continue
                    # 添加 read → write 边（read 优先于 write）
                    self._nodes[write_id].dependencies.add(read_id)
                    self._nodes[read_id].dependents.add(write_id)

    def _detect_tool_category_constraints(self) -> None:
        """检测工具类别调度约束（层 D）

        在显式依赖、路径重叠、user_select 约束之后执行，新增第四层依赖约束：

        规则 a — bash ↔ non-bash 双向隔断：
            non-bash（read/write/interactive）在 bash 之前执行
            若已有 non-bash→bash 路径（防环）或 bash→non-bash 路径（已有依赖），跳过
        规则 b — bash → bash 链式串行：
            多个 bash 按原始顺序链式依赖，一次只运行一个 bash
        规则 c — read → write 默认边：
            read 类工具在 write 类工具之前执行
            若已有 write→read 路径（防环），跳过
        规则 d — 防环保护：
            所有新边添加前均经 _path_exists 反方向检查，避免形成环

        general 类别不参与任何类别约束。

        委托给 3 个子方法按顺序执行：
        1. _classify_tool_nodes() — 按 tool_category 分类所有节点
        2. _add_bash_non_bash_constraints() — 添加 bash↔non-bash 约束
        3. _add_read_write_constraints() — 添加 read→write 约束
        """
        if not self._nodes:
            return

        read_nodes, write_nodes, bash_nodes, non_bash_nodes = self._classify_tool_nodes()
        self._add_bash_non_bash_constraints(bash_nodes, non_bash_nodes)
        self._add_read_write_constraints(read_nodes, write_nodes)

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
        processed_set: set[str] = set()
        processed: int = 0

        while queue:
            current_layer: list[str] = list(queue)
            layers.append(current_layer)
            queue.clear()

            for tc_id in current_layer:
                processed += 1
                processed_set.add(tc_id)
                # 更新入度：移除本节点，其所有后继入度 -1
                for dep_id in self._nodes[tc_id].dependents:
                    if dep_id in in_degree:
                        in_degree[dep_id] -= 1
                        if in_degree[dep_id] == 0:
                            if dep_id not in queue:
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

    # ── 节点移除 ────────────────────────────────────────────

    def remove_nodes(self, tc_ids: set[str]) -> None:
        """从 DAG 中移除指定 tc_id 集合对应的节点（含双向边清理）

        1. 双向清理出边/入边：从剩余节点的 dependencies/dependents 中移除已删除 ID
        2. 从 _nodes 字典中删除节点
        3. 从 _original_order 中移除已删除节点（防止无限增长）
        4. 清除 _path_cache

        Args:
            tc_ids: 要移除的 tc_id 集合。不存在的 ID 被静默跳过。

        Note:
            批清理场景（由 _execute_global_dag_async 在批执行完成后调用）：
            已完成的节点从 DAG 中移除后，下一批 add_batch 的 prev_non_dispatch_ids
            中若引用已删除节点，add_batch 第 4 步的 ``if prev_id in self._nodes``
            守卫会跳过该边。语义正确——已完成节点不再需要等待。
            未完成的节点仍保留在 _nodes 中，对应边正常创建。
        """
        if not tc_ids:
            return

        to_remove = {tid for tid in tc_ids if tid in self._nodes}
        if not to_remove:
            return

        # 第 1 步：双向清理边引用
        for tid in to_remove:
            node = self._nodes[tid]

            # 清理入边：从依赖我的节点（dependents）中移除我
            for dep_id in node.dependents:
                if dep_id in self._nodes:
                    self._nodes[dep_id].dependencies.discard(tid)

            # 清理出边：从我依赖的节点（dependencies）中移除我
            for dep_id in node.dependencies:
                if dep_id in self._nodes:
                    self._nodes[dep_id].dependents.discard(tid)

        # 第 2 步：从 _nodes 移除节点
        for tid in to_remove:
            del self._nodes[tid]

        # 第 3 步：从 _original_order 中移除已删除节点（防止无限增长）
        self._original_order = [tid for tid in self._original_order if tid not in to_remove]

        _logger.debug(
            "ToolDAG: removed %d nodes, remaining %d",
            len(to_remove), self.size,
        )

        # 第 4 步：清除缓存（删除节点后旧缓存可能引用已删除节点）
        self._clear_path_cache()

    # ── 工具方法 ────────────────────────────────────────────

    def get_node(self, tc_id: str) -> ToolCallNode | None:
        """按 tc_id 获取节点"""
        return self._nodes.get(tc_id)

    def get_level(self, tc_id: str) -> int:
        """获取节点的拓扑层号，未分配返回 -1"""
        node = self._nodes.get(tc_id)
        return node.layer if node is not None else -1