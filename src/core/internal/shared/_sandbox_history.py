#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件历史记录管理器（纯数据类）

管理文件修改历史，支持按消息索引查询和恢复文件状态。
不依赖 SandboxManager 的锁层次和索引映射。

线程模型：所有对 ``file_history`` 的读写都必须经本类方法（内部持
``self._lock``）。外部（SandboxManager）不得直接遍历 / 修改
``file_history``，否则与并发 ``record`` 的 dict 结构变更竞争
（Python 遍历中结构变化会抛 RuntimeError）。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
from bisect import bisect_left
from src._compat import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set

from ...file_change_record import FileChangeRecord
from ..._atomic_io import atomic_write_text

_logger = logging.getLogger(__name__)


def normalize_removed_indices(removed_indices: Iterable[int]) -> List[int]:
    """规范化被移除的消息索引：去重 + 升序（供折叠重映射使用）。"""
    cleaned = {int(i) for i in removed_indices if int(i) >= 0}
    return sorted(cleaned)


def fold_index(
    old_index: int,
    removed_sorted: List[int],
    removed_set: Set[int],
    anchor: int,
    inserted: int,
) -> int:
    """上下文折叠 / 隐藏后，单个消息索引的新位置（保留记录，不丢弃）。

    语义：``removed_sorted`` 中的消息被折叠进 ``anchor`` 位置的一条新消息
    （``inserted`` = 1，摘要压缩）或直接消失（``inserted`` = 0，降级删除）。
    与「删除即失效」（``remap_indices``）不同，本映射把被移除区间内的记录
    重挂到折叠锚点——文件变更已在磁盘生效，压缩只折叠上下文，记录必须保留
    （否则 ``/changes`` 与回滚丢失历史，无法正常还原）。

    Args:
        old_index: 记录的原消息索引。
        removed_sorted: 被移除消息索引的升序列表。
        removed_set: 同上，集合形式（O(1) 归属判定）。
        anchor: 折叠锚点（删除前坐标）——被移除区间的记录重挂于此。
        inserted: 锚点处新增消息条数（摘要折叠 1，纯删除 0）。

    Returns:
        新消息索引（>= 0）。
    """
    removed_before_anchor = bisect_left(removed_sorted, anchor)
    if old_index in removed_set:
        return anchor - removed_before_anchor
    removed_before = bisect_left(removed_sorted, old_index)
    if old_index < anchor:
        return old_index - removed_before
    return old_index - removed_before + inserted


def _path_depth(path: str) -> int:
    """路径深度（兼容 Windows / 混合分隔符）——恢复时深层优先处理。

    修复：旧实现用 ``path.count(os.sep)``，记录路径含异种分隔符
    （Windows 上 ``/`` 与 ``\\`` 混用）时深度失真，父/子目录恢复顺序错乱。
    """
    return len([seg for seg in re.split(r"[\\/]+", path) if seg])


def _read_text_if_file(path: str) -> Optional[str]:
    """读取普通文件的文本内容（供 restore 跳过「磁盘已等于目标」的文件）。

    返回 None 的情况（调用方一律按「需恢复」处理，即不跳过）：
    - 路径不存在 / 不是普通文件（目录）/ 是符号链接；
    - 读取失败（权限、I/O 错误）。

    解码参数（utf-8 / replace）与 ``atomic_write_text`` 写入侧保持一致，
    避免「字符等价但解码方式不同」被误判为「有变化」而多写一次。
    """
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


@dataclass(slots=True)
class FileSnapshot:
    """单个文件的恢复快照"""
    file_path: str
    target_content: Optional[str]   # None 表示路径不应存在
    target_type: Optional[str]      # "file" | "directory" | None


class _FileHistory:
    """文件历史记录管理器

    纯数据类：管理 file_history dict + 基于文件历史的查询/恢复操作。
    拥有自己的线程锁，与 SandboxManager 的锁层次独立。

    职责：
    - 文件修改记录（record）
    - 历史状态查询（get_snapshot / get_record_type_at_message / has_history）
    - 文件恢复（restore — 含原子写入、备份、类型转换）
    - 索引维护（shift_indices / reindex_records / remap_indices）
    - 记录清理（remove_after_index / _cleanup_old_records）
    - 元数据（get_all_records / get_stats / snapshot / deep_copy_records）
    """

    def __init__(self, max_history_per_file: int = 100) -> None:
        self.file_history: Dict[str, List[FileChangeRecord]] = {}
        self.max_history_per_file = max_history_per_file
        self._lock = threading.RLock()

    # ── 记录 ────────────────────────────────────────────

    def record(
        self,
        file_path: str,
        content_before: Optional[str],
        content_after: Optional[str],
        message_index: int,
        tool_name: str = "write_file",
        record_type: str = "file",
    ) -> FileChangeRecord:
        """记录文件修改（仅写入 file_history，不涉及 message_history）。"""
        with self._lock:
            record = FileChangeRecord(
                file_path=file_path,
                content_before=content_before,
                content_after=content_after,
                message_index=message_index,
                tool_name=tool_name,
                record_type=record_type,
            )
            self.file_history.setdefault(file_path, []).append(record)
            self._cleanup_old_records(file_path)
            return record

    def _cleanup_old_records(self, file_path: str) -> List[FileChangeRecord]:
        """清理旧记录，保持历史大小在限制内，返回被淘汰的记录列表。

        ★ 边界修复：``max_history_per_file <= 0`` 时旧实现
        ``records[-0:]`` 等价 ``records[0:]``（全量保留，限制失效）；
        现明确清空。被淘汰记录由 SandboxManager 同步从 message_history
        移除（保持两表一致）。
        """
        records = self.file_history.get(file_path)
        if not records:
            return []
        limit = self.max_history_per_file
        if limit <= 0:
            del self.file_history[file_path]
            return list(records)
        if len(records) > limit:
            evicted = records[:-limit]
            self.file_history[file_path] = records[-limit:]
            return list(evicted)
        return []

    # ── 查询 ────────────────────────────────────────────

    def has_history(self, file_path: str) -> bool:
        """该路径是否有沙盒历史记录（用于区分「无记录」与「记录为已删除」）。"""
        with self._lock:
            return bool(self.file_history.get(file_path))

    def get_snapshot(self, file_path: str, message_index: int) -> Optional[str]:
        """获取文件在指定消息索引时的内容（纯内存查询）。

        Returns:
            文件内容，None 表示文件不存在。
        """
        with self._lock:
            records = self.file_history.get(file_path)
            if not records:
                return None

            # ★ 修复：先排序再取首条——旧实现用未排序的 ``records[0]``，
            #   记录经 shift/remap 后可能乱序（insert 事件使部分索引后移），
            #   返回的「首次修改前状态」会取错记录。
            ordered = sorted(records, key=lambda r: r.message_index)

            last_record = None
            for record in ordered:
                if record.message_index <= message_index:
                    last_record = record
                else:
                    break

            if last_record:
                return last_record.content_after
            # 所有记录都在目标索引之后 → 返回首次修改前的状态
            return ordered[0].content_before

    def get_record_type_at_message(
        self, file_path: str, message_index: int,
    ) -> Optional[str]:
        """获取指定路径在指定消息索引时的记录类型（"file" / "directory" / None）。"""
        with self._lock:
            records = self.file_history.get(file_path)
            if not records:
                return None

            ordered = sorted(records, key=lambda r: r.message_index)
            last_record = None
            for record in ordered:
                if record.message_index <= message_index:
                    last_record = record
                else:
                    break
            if last_record:
                return last_record.record_type
            return ordered[0].record_type

    # ── 索引维护（持内部锁，供 SandboxManager 调用） ──────

    def snapshot(self) -> Dict[str, List[FileChangeRecord]]:
        """持锁返回 file_history 的浅拷贝（键映射到原 list 引用）。"""
        with self._lock:
            return dict(self.file_history)

    def all_records_snapshot(self) -> List[FileChangeRecord]:
        """持锁返回所有记录的扁平静态列表（用于重建 message_history）。"""
        with self._lock:
            return [r for records in self.file_history.values() for r in records]

    def kept_record_ids(self, file_path: str) -> Set[int]:
        """持锁返回该路径当前保留记录的 id 集合。"""
        with self._lock:
            return {id(r) for r in self.file_history.get(file_path, ())}

    def shift_indices(self, insert_at: int, delta: int = 1) -> None:
        """将 ``message_index >= insert_at`` 的记录整体平移 delta（可负）。

        持内部锁执行——修复旧实现（SandboxManager 持自身锁直接遍历
        file_history）与并发 record 的结构变更竞争。
        """
        if delta == 0:
            return
        with self._lock:
            for records in self.file_history.values():
                for r in records:
                    if r.message_index >= insert_at:
                        r.message_index += delta

    def reindex_records(self, predicate: Callable[[FileChangeRecord], bool],
                        new_index: int) -> int:
        """将满足 predicate 的记录 message_index 改为 new_index，返回数量。"""
        count = 0
        with self._lock:
            for records in self.file_history.values():
                for r in records:
                    if predicate(r) and r.message_index != new_index:
                        r.message_index = new_index
                        count += 1
        return count

    def remap_indices(self, new_index_of: Callable[[int], int]) -> Set[int]:
        """按 ``new_index_of(old) -> int`` 重映射所有记录索引。

        ``new_index_of`` 返回 < 0 表示丢弃该记录；返回 >= 0 表示保留并改索引。
        返回保留下来的记录 id 集合（供 SandboxManager 判定 orphan）。
        """
        kept_ids: Set[int] = set()
        with self._lock:
            for path, records in list(self.file_history.items()):
                new_records: List[FileChangeRecord] = []
                for r in records:
                    ni = new_index_of(r.message_index)
                    if ni >= 0:
                        r.message_index = ni
                        new_records.append(r)
                        kept_ids.add(id(r))
                if new_records:
                    self.file_history[path] = new_records
                else:
                    del self.file_history[path]
        return kept_ids

    def fold_indices(
        self,
        removed_indices: Iterable[int],
        insert_index: Optional[int] = None,
    ) -> None:
        """上下文折叠后的索引重映射（保留全部记录，不丢弃）。

        用于上下文压缩：``removed_indices`` 对应的消息被折叠为一条位于
        ``insert_index`` 的摘要（``insert_index`` 为 None 表示纯删除、无新增
        消息）。与 ``remap_indices``（删除即失效）不同，本方法把被移除区间内
        的记录重挂到折叠锚点（``insert_index`` 或最小被移除索引），其余记录
        按「删除 N 条 + 新增 M 条」平移——文件变更已在磁盘生效，不能因压缩
        丢失沙盒历史。

        持内部锁执行（与并发 record 的结构变更串行）。
        """
        removed_sorted = normalize_removed_indices(removed_indices)
        if not removed_sorted:
            return
        removed_set = set(removed_sorted)
        if insert_index is None:
            anchor = removed_sorted[0]
            inserted = 0
        else:
            anchor = max(0, int(insert_index))
            inserted = 1
        with self._lock:
            for records in self.file_history.values():
                for r in records:
                    r.message_index = fold_index(
                        r.message_index, removed_sorted, removed_set, anchor, inserted,
                    )

    # ── 恢复 ────────────────────────────────────────────

    def restore(self, target_message_index: int) -> Dict[str, bool]:
        """恢复到指定消息索引的文件状态。

        三阶段：
        - Phase 1：持锁收集快照数据
        - Phase 2：释放锁后执行文件 I/O（磁盘内容已等于目标的文件跳过写入；
          原子写已保证「要么新内容、要么原内容」，不做冗余备份）
        - Phase 3：重新持锁清理记录

        Returns:
            {file_path: success} 字典。
        """
        # Phase 1: 持锁收集快照
        with self._lock:
            affected_files_set: Set[str] = set()
            for records in self.file_history.values():
                for record in records:
                    if record.message_index > target_message_index:
                        affected_files_set.add(record.file_path)

            snapshots: List[FileSnapshot] = []
            for file_path in affected_files_set:
                target_content = self.get_snapshot(file_path, target_message_index)
                target_type = self.get_record_type_at_message(
                    file_path, target_message_index,
                )
                snapshots.append(FileSnapshot(file_path, target_content, target_type))

            # 按路径深度降序排列（深层优先），确保子路径先于父路径处理
            snapshots.sort(key=lambda s: _path_depth(s.file_path), reverse=True)

        # Phase 2: 释放锁后执行文件 I/O
        results: Dict[str, bool] = {}
        for snap in snapshots:
            try:
                if snap.target_content is None:
                    # 路径不应存在
                    if os.path.exists(snap.file_path) or os.path.islink(snap.file_path):
                        if os.path.isdir(snap.file_path) and not os.path.islink(snap.file_path):
                            shutil.rmtree(snap.file_path)
                        else:
                            os.remove(snap.file_path)
                    results[snap.file_path] = True
                elif snap.target_type == "directory":
                    # 恢复目录
                    if os.path.isfile(snap.file_path) or os.path.islink(snap.file_path):
                        os.remove(snap.file_path)
                    os.makedirs(snap.file_path, exist_ok=True)
                    results[snap.file_path] = True
                else:
                    # 恢复文件（原子写入）
                    if os.path.isdir(snap.file_path) and not os.path.islink(snap.file_path):
                        shutil.rmtree(snap.file_path)

                    # ★ 性能（restore 降 I/O）：磁盘内容已等于目标快照 → 跳过写入。
                    #   读取成本（实测 ~0.16ms/文件）远低于一整套写入
                    #   （mkstemp + 写 + chmod + os.replace，实测 ~2ms/文件），
                    #   且避免 os.replace 造成的 mtime 抖动（文件监视器/编辑器
                    #   热重载不再被无谓触发）。
                    if _read_text_if_file(snap.file_path) == snap.target_content:
                        results[snap.file_path] = True
                        continue

                    # ★ 性能：去掉冗余备份——``atomic_write_text`` 用「同目录临时
                    #   文件 + os.replace」，失败时原文件保持不动（要么新内容、
                    #   要么原内容），修复前额外 mkstemp + copy2 整份复制仅用于
                    #   异常回滚，属冗余 I/O（大文件时可达数十 ms/文件）。
                    #   父目录由 ``atomic_write_text`` 内部按需创建，避免前置
                    #   makedirs 与内部 makedirs 的重复系统调用。
                    atomic_write_text(snap.file_path, snap.target_content)
                    results[snap.file_path] = True
            except Exception as e:
                _logger.warning(
                    "恢复文件失败: %s — %s", snap.file_path, e,
                )
                results[snap.file_path] = False

        # Phase 3: 重新持锁清理记录
        with self._lock:
            self.remove_after_index(target_message_index)

        return results

    # ── 清理 ────────────────────────────────────────────

    def remove_after_index(self, message_index: int) -> None:
        """移除 file_history 中指定索引之后的所有记录。"""
        with self._lock:
            for file_path, records in list(self.file_history.items()):
                new_records = [
                    r for r in records if r.message_index <= message_index
                ]
                if new_records:
                    self.file_history[file_path] = new_records
                else:
                    del self.file_history[file_path]

    def clear(self) -> None:
        """清空所有 file_history 记录。"""
        with self._lock:
            self.file_history.clear()

    # ── 元数据 ──────────────────────────────────────────

    def get_all_records(self) -> List[FileChangeRecord]:
        """获取所有文件修改记录（按消息索引+时间戳排序）。"""
        with self._lock:
            all_records: List[FileChangeRecord] = []
            for records in self.file_history.values():
                all_records.extend(records)

        # 排序移出锁范围
        all_records.sort(key=lambda r: (r.message_index, r.timestamp))
        return all_records

    def get_stats(self) -> Dict[str, Any]:
        """获取 file_history 统计信息。"""
        with self._lock:
            total_records = sum(
                len(records) for records in self.file_history.values()
            )
            return {
                "total_files": len(self.file_history),
                "total_records": total_records,
                "max_history_per_file": self.max_history_per_file,
            }

    def deep_copy_records(self) -> Dict[str, List[FileChangeRecord]]:
        """深拷贝所有 file_history 记录（用于快照/备份）。"""
        import copy
        with self._lock:
            return copy.deepcopy(self.file_history)
