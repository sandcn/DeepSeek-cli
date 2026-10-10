#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件沙盒管理器

记录大模型修改文件的信息，支持在消息截断时恢复文件状态。
"""

import asyncio
import contextvars
import os
import threading
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .internal.shared._sandbox_history import (
    _FileHistory,
    fold_index,
    normalize_removed_indices,
)
from .file_change_record import FileChangeRecord  # noqa: F401 — re-exported for backward compat


class SandboxManager:
    """文件沙盒管理器

    管理文件修改历史，支持按消息索引恢复文件状态。

    ⚠️ 锁层次（必须遵守，防止死锁）:
        ContextManager._lock → SandboxManager.lock → _FileHistory._lock
    解释：ContextManager 在持有 _lock 期间可能通过 on_messages_changed 回调
    调用本类的 shift_indices()/remap_indices()。因此 SandboxManager.lock
    永远不得在 ContextManager._lock 之前获取，否则形成 ABBA 死锁。
    _FileHistory._lock 在最内层，仅在需要直接访问 _fh.file_history 时获取。
    """

    def __init__(self, max_history_per_file: int = 100, owner_id: Any = None):
        """
        初始化沙盒管理器

        Args:
            max_history_per_file: 每个文件最大历史记录数
            owner_id: 所属会话标识（ChatSession 实例 id）。用于多会话隔离：
                同一会话的重复 initialize 复用本实例（保留状态），不同会话
                则重建，避免串扰。
        """
        # 文件历史记录（委托给 _FileHistory）
        self._fh = _FileHistory(max_history_per_file)
        # 所属会话标识（None 表示未绑定）
        self.owner_id = owner_id

        # 按消息索引组织的记录：{message_index: List[FileChangeRecord]}
        self.message_history: Dict[int, List[FileChangeRecord]] = {}
        # 当前消息索引
        self.current_message_index = 0
        # 锁用于线程安全
        self.lock = threading.RLock()

    # ── 配置属性 ───────────────────────────────────────

    @property
    def max_history_per_file(self) -> int:
        """每个文件最大历史记录数（委托给 _FileHistory）。"""
        return self._fh.max_history_per_file

    @max_history_per_file.setter
    def max_history_per_file(self, value: int) -> None:
        self._fh.max_history_per_file = value

    @property
    def file_history(self) -> dict:
        """文件历史记录的只读副本（向后兼容，返回浅拷贝 dict）。

        注意：返回的 dict 值是原始 list 引用，不建议外部直接修改。
        需要修改请通过 SandboxManager 公共方法（record_file_change 等）。
        浅拷贝在 _FileHistory 内部锁下完成，避免并发结构变更竞争。
        """
        return self._fh.snapshot()

    # ── 当前消息索引管理 ───────────────────────────────────

    def _update_current_index(self, new_idx: int) -> None:
        """统一更新当前消息索引，作为所有写入 current_message_index 的唯一入口。"""
        self.current_message_index = new_idx

    def _rebuild_message_history(self) -> None:
        """从 file_history 重建 message_history。

        消除 shift_indices/remap_indices 中重复的 message_history 重建逻辑。
        经 _FileHistory 的持锁快照遍历所有记录并按 message_index 重新分组
        （不直接访问 file_history，避免与并发 record 竞争）。
        """
        new_mh: dict[int, list[FileChangeRecord]] = {}
        for r in self._fh.all_records_snapshot():
            new_mh.setdefault(r.message_index, []).append(r)
        self.message_history = new_mh

    def _prune_evicted_for_file(self, file_path: str) -> None:
        """清理 message_history 中已被 _FileHistory 淘汰（超出历史上限）的记录。

        历史上限截断只发生在 _FileHistory 内部；若不同步清理 message_history，
        两表会不一致（remap 时这些「幽灵记录」会被当作 orphan 重挂）。
        仅在持有 ``self.lock`` 时调用。
        """
        kept_ids = self._fh.kept_record_ids(file_path)
        for idx in list(self.message_history.keys()):
            records = self.message_history[idx]
            filtered = [
                r for r in records
                if r.file_path != file_path or id(r) in kept_ids
            ]
            if not filtered:
                del self.message_history[idx]
            elif len(filtered) != len(records):
                self.message_history[idx] = filtered

    def record_file_change(self, file_path: str, content_before: Optional[str],
                          content_after: Optional[str], message_index: int,
                          tool_name: str = "write_file",
                          record_type: str = "file") -> FileChangeRecord:
        """
        记录文件修改

        Args:
            file_path: 文件路径
            content_before: 修改前的内容，None表示路径不存在
            content_after: 修改后的内容，None表示路径被删除
            message_index: 关联的消息索引
            tool_name: 工具名称
            record_type: 记录类型，"file"（默认）或 "directory"

        Returns:
            FileChangeRecord: 创建的记录

        注意：同一消息索引多次修改同一文件时，每条记录独立追加，不会合并。
        确保回滚时可以精确恢复每个中间状态。

        锁顺序：先取 ``self.lock`` 再取 ``_FileHistory._lock``（声明顺序），
        消除旧实现「先在 _FileHistory 内部持锁、后在 self.lock 写
        message_history」的锁序歧义。
        """
        with self.lock:
            record = self._fh.record(
                file_path, content_before, content_after, message_index,
                tool_name, record_type,
            )
            self.message_history.setdefault(message_index, []).append(record)
            self._prune_evicted_for_file(file_path)
            self._update_current_index(
                max(self.current_message_index, message_index),
            )
        return record

    def record_file_changes_batch(
        self,
        changes: Iterable[Tuple[str, Optional[str], Optional[str], str, str]],
        message_index: int,
    ) -> List[FileChangeRecord]:
        """批量记录文件修改（同一消息索引），返回创建的记录列表。

        用于目录级操作（cp/mv/rm 的目录树）——一次持锁完成全部记录，
        避免逐条 ``asyncio.to_thread`` 的高频线程切换开销。

        Args:
            changes: 可迭代的 5 元组
                ``(file_path, content_before, content_after, tool_name, record_type)``。
            message_index: 关联的消息索引（本批统一）。
        """
        records: List[FileChangeRecord] = []
        touched_paths: set = set()
        with self.lock:
            bucket = self.message_history.setdefault(message_index, [])
            for file_path, content_before, content_after, tool_name, record_type in changes:
                record = self._fh.record(
                    file_path, content_before, content_after, message_index,
                    tool_name, record_type,
                )
                bucket.append(record)
                records.append(record)
                touched_paths.add(file_path)
            for path in touched_paths:
                self._prune_evicted_for_file(path)
            if records:
                self._update_current_index(
                    max(self.current_message_index, message_index),
                )
        return records

    async def async_record_file_change(self, file_path: str, content_before: Optional[str],
                                       content_after: Optional[str], message_index: int,
                                       tool_name: str = "write_file",
                                       record_type: str = "file") -> FileChangeRecord:
        """异步记录文件修改（内部同步操作已包装为 async）"""
        return await asyncio.to_thread(
            self.record_file_change, file_path, content_before, content_after,
            message_index, tool_name, record_type,
        )

    async def async_record_file_changes_batch(
        self,
        changes: Iterable[Tuple[str, Optional[str], Optional[str], str, str]],
        message_index: int,
    ) -> List[FileChangeRecord]:
        """异步批量记录文件修改（包装为 to_thread）。"""
        return await asyncio.to_thread(
            self.record_file_changes_batch, list(changes), message_index,
        )

    def update_message_index(self, new_index: int):
        """更新当前消息索引"""
        with self.lock:
            self._update_current_index(new_index)

    def get_current_message_index_safe(self) -> int:
        """线程安全地获取当前消息索引"""
        with self.lock:
            return self.current_message_index

    def get_file_state_at_message(self, file_path: str, message_index: int) -> Optional[str]:
        """
        获取文件在指定消息索引时的状态

        Args:
            file_path: 文件路径
            message_index: 消息索引

        Returns:
            文件内容，None表示文件不存在

        语义修复：旧实现无法区分「无沙盒记录」与「记录显示该索引时文件不存在」
        （两者均返回 None → 都回退读磁盘，后者会返回操作前的错误内容）。
        现以 ``has_history`` 判定：有记录则以记录快照为准（含 None=不存在），
        无记录才回退磁盘。
        """
        # Phase 1: 有沙盒记录 → 以记录快照为准（None 表示该索引时不存在）
        if self._fh.has_history(file_path):
            return self._fh.get_snapshot(file_path, message_index)

        # Phase 2: 无任何记录时回退到磁盘状态
        if os.path.exists(file_path):
            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    return f.read()
            except Exception:
                return None
        else:
            return None

    def _get_record_type_at_message(self, file_path: str, message_index: int) -> Optional[str]:
        """获取指定路径在指定消息索引时的记录类型（"file" / "directory" / None）。"""
        return self._fh.get_record_type_at_message(file_path, message_index)

    def restore_to_message(self, target_message_index: int) -> Dict[str, bool]:
        """
        恢复到指定消息索引的文件状态

        Args:
            target_message_index: 目标消息索引

        Returns:
            {file_path: success} 字典，表示每个文件的恢复结果

        锁策略：
        - Phase 1-2：委托 _FileHistory.restore()（内部持锁→释放→文件 I/O→持锁清理）
        - Phase 3：持 SandboxManager.lock 清理 message_history 并更新索引
        """
        # Phase 1-2: 委托 _FileHistory 执行文件恢复（含 file_history 清理）
        results = self._fh.restore(target_message_index)

        # Phase 3: 清理 message_history 并更新索引
        with self.lock:
            for idx in list(self.message_history.keys()):
                if idx > target_message_index:
                    del self.message_history[idx]
                else:
                    self.message_history[idx] = [
                        r for r in self.message_history[idx]
                        if r.message_index <= target_message_index
                    ]
                    if not self.message_history[idx]:
                        del self.message_history[idx]
            self._update_current_index(target_message_index)

        return results

    async def async_restore_to_message(self, target_message_index: int) -> Dict[str, bool]:
        """异步恢复到指定消息索引的文件状态，使用 asyncio.to_thread 避免阻塞"""
        return await asyncio.to_thread(self.restore_to_message, target_message_index)

    def _remove_records_after_index(self, message_index: int):
        """移除指定消息索引之后的所有记录"""
        with self.lock:
            # 委托 _FileHistory 清理 file_history
            self._fh.remove_after_index(message_index)

            # 清理 message_history
            for idx in list(self.message_history.keys()):
                if idx > message_index:
                    del self.message_history[idx]
                else:
                    self.message_history[idx] = [
                        r for r in self.message_history[idx]
                        if r.message_index <= message_index
                    ]
                    if not self.message_history[idx]:
                        del self.message_history[idx]

    def reindex_records(self, predicate, new_index: int) -> int:
        """将满足 predicate 的文件变更记录重映射到 new_index（后台 subagent 索引修复用）。

        场景（2026-08-18，后台 subagent 与上下文压缩并发）：后台 subagent
        的文件变更经 contextvar 快照关联到派发轮次的消息索引；若 MainAgent
        在后台 subagent 运行期间发生上下文压缩（remap_indices 删除派发轮次
        消息），派发索引已失效（悬空记录——消息列表无该索引，回滚丢失）。
        本方法把满足 predicate 的记录 message_index 改为 new_index 并重建
        message_history，使记录关联到当前有效索引（语义降级：文件变更本身
        已生效，重挂保证「回滚到当前状态」仍可恢复）。

        Args:
            predicate: 单参函数（FileChangeRecord）→ bool，选择要重挂的记录。
            new_index: 重挂后的消息索引。

        Returns:
            实际重挂的记录数（0 表示无匹配记录）。
        """
        count = 0
        with self.lock:
            count = self._fh.reindex_records(predicate, new_index)
            if count:
                self._rebuild_message_history()
                self._update_current_index(
                    max(self.current_message_index, new_index),
                )
        return count

    def get_sandbox_info(self, message_index: int) -> Dict[str, Any]:
        """
        获取指定消息的沙盒信息

        Args:
            message_index: 消息索引

        Returns:
            包含沙盒信息的字典
        """
        with self.lock:
            if message_index not in self.message_history:
                return {"file_changes": [], "count": 0}

            records = self.message_history[message_index]
            file_changes = []
            for record in records:
                file_changes.append({
                    "file_path": record.file_path,
                    "change_type": record.get_change_type(),
                    "tool_name": record.tool_name,
                    "timestamp": record.timestamp
                })

            return {
                "file_changes": file_changes,
                "count": len(records)
            }

    def get_all_file_changes(self) -> List[FileChangeRecord]:
        """获取所有文件修改记录（按消息索引排序）"""
        return self._fh.get_all_records()

    # ── 视图 / 统计查询（TUI 沙盒界面数据源，2026-10） ──────────

    def get_file_paths(self) -> List[str]:
        """所有有沙盒记录的文件路径（升序）。"""
        return sorted(self._fh.snapshot().keys())

    def get_file_history(self, file_path: str) -> List[FileChangeRecord]:
        """单个文件的全部修改记录（按消息索引 + 时间戳排序）。

        与 ``get_all_file_changes`` 的差别：本方法只取指定路径，且**保留
        中间记录**（视图「文件历史时间线」需要逐次修改，而非首末聚合）。
        """
        with self.lock:
            records = list(self._fh.snapshot().get(file_path, ()) or ())
        records.sort(key=lambda r: (r.message_index, r.timestamp))
        return records

    def get_message_groups(self) -> List[Tuple[int, List[FileChangeRecord]]]:
        """按消息索引分组的记录（升序；线程安全快照）。

        视图「消息维度」的数据源：每条消息索引对应一组文件变更记录。
        """
        with self.lock:
            groups = {
                idx: list(records) for idx, records in self.message_history.items()
            }
        out: List[Tuple[int, List[FileChangeRecord]]] = []
        for idx in sorted(groups):
            records = sorted(groups[idx], key=lambda r: r.timestamp)
            if records:
                out.append((idx, records))
        return out

    def get_message_records(self, message_index: int) -> List[FileChangeRecord]:
        """指定消息索引下的记录（O(1) 集合查询，供轨迹检查器关联用）。"""
        try:
            idx = int(message_index)
        except (TypeError, ValueError):
            return []
        with self.lock:
            return list(self.message_history.get(idx, ()) or ())

    def find_records(self, *, file_path: Optional[str] = None,
                     tool_name: Optional[str] = None,
                     message_index: Optional[int] = None,
                     change_type: Optional[str] = None) -> List[FileChangeRecord]:
        """按条件过滤全部记录（``None`` 表示该条件不过滤）。

        ``change_type`` 取 :meth:`FileChangeRecord.get_change_type` 的结果
        （"新建文件" / "修改文件" / "删除文件" 及目录变体）。
        """
        out: List[FileChangeRecord] = []
        for r in self.get_all_file_changes():
            if file_path is not None and r.file_path != file_path:
                continue
            if tool_name is not None and r.tool_name != tool_name:
                continue
            if message_index is not None and r.message_index != message_index:
                continue
            if change_type is not None and r.get_change_type() != change_type:
                continue
            out.append(r)
        return out

    def get_extended_stats(self) -> Dict[str, Any]:
        """扩展统计（视图统计面板数据源）。

        在 ``get_stats`` 基础上增加：消息分组数、当前索引、工具分布、
        变更类型分布、缓存内容字符数、回滚次数。
        """
        with self.lock:
            groups = len(self.message_history)
            current = self.current_message_index
        base = self.get_stats()
        records = self.get_all_file_changes()
        tool_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        content_chars = 0
        revert_count = 0
        for r in records:
            name = str(r.tool_name or "")
            tool_counts[name] = tool_counts.get(name, 0) + 1
            label = r.get_change_type()
            type_counts[label] = type_counts.get(label, 0) + 1
            if isinstance(r.content_before, str):
                content_chars += len(r.content_before)
            if isinstance(r.content_after, str):
                content_chars += len(r.content_after)
            if name.startswith("revert") or name.startswith("undo-revert"):
                revert_count += 1
        base.update({
            "message_groups": groups,
            "current_message_index": current,
            "tool_counts": tool_counts,
            "type_counts": type_counts,
            "content_chars": content_chars,
            "revert_count": revert_count,
        })
        return base

    # ── 回滚 / 撤销回滚（视图操作；保持沙盒记录一致） ──────────

    def get_last_revert(self, file_path: Optional[str] = None
                        ) -> Optional[FileChangeRecord]:
        """最近一次回滚记录（``tool_name`` 以 ``revert`` / ``undo-revert`` 开头）。"""
        for r in reversed(self.get_all_file_changes()):
            name = str(r.tool_name or "")
            if name.startswith("revert") or name.startswith("undo-revert"):
                if file_path is None or r.file_path == file_path:
                    return r
        return None

    def preview_revert(self, file_path: str) -> Tuple[Optional[str], Optional[str]]:
        """预览「回滚到首次变更前」：返回 ``(当前末态, 目标内容)``。

        无记录时返回 ``(None, None)``（调用方按「无文件需还原」处理）。
        """
        records = self.get_file_history(file_path)
        if not records:
            return (None, None)
        return (records[-1].content_after, records[0].content_before)

    def revert_file(self, file_path: str
                    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """把文件回滚到其**首次变更前**的状态，并记录回滚动作。

        Returns:
            ``(ok, before_content, after_content)``：失败为
            ``(False, ...)``；成功时 ``before_content`` = 回滚前状态（当前
            末态），``after_content`` = 回滚后状态（首次变更前，``None``
            表示文件应不存在）。
        """
        records = self.get_file_history(file_path)
        if not records:
            return (False, None, None)
        target = records[0].content_before   # 首次变更前（None = 不存在）
        current = records[-1].content_after  # 当前末态
        if not self._apply_content(file_path, target):
            return (False, current, target)
        try:
            idx = self.get_current_message_index_safe()
            self.record_file_change(
                file_path, current, target, idx, tool_name="revert",
            )
        except Exception:
            pass
        return (True, current, target)

    def undo_last_revert(self, file_path: Optional[str] = None
                         ) -> Tuple[bool, str]:
        """撤销最近一次回滚（把文件恢复到回滚前状态）。

        Returns:
            ``(ok, path)``：``path`` 为受影响的文件路径（``""`` 表示无
            可撤销的回滚记录）。
        """
        rec = self.get_last_revert(file_path)
        if rec is None:
            return (False, "")
        path = rec.file_path
        restore = rec.content_before  # 回滚前状态
        current = rec.content_after   # 回滚后状态
        if not self._apply_content(path, restore):
            return (False, path)
        try:
            idx = self.get_current_message_index_safe()
            self.record_file_change(
                path, current, restore, idx, tool_name="undo-revert",
            )
        except Exception:
            pass
        return (True, path)

    @staticmethod
    def _apply_content(file_path: str, content: Optional[str]) -> bool:
        """把文件设置为指定状态（原子写入；``None`` 表示删除）。"""
        try:
            from ._atomic_io import atomic_write_text, remove_path

            if content is None:
                remove_path(file_path)
            else:
                atomic_write_text(file_path, content)
            return True
        except Exception:
            return False

    def shift_indices(self, insert_at: int):
        """当在消息列表中插入一条消息后，将 >= insert_at 的索引全部 +1。"""
        self.shift_indices_by(insert_at, 1)

    def shift_indices_by(self, insert_at: int, delta: int):
        """将 >= insert_at 的所有记录索引整体平移 delta（delta 可为负）。

        用于消息列表头部结构变化（如系统提词重建导致 system 条数变化）
        后同步沙盒索引。索引维护委托 _FileHistory（持内部锁）执行。
        """
        if delta == 0:
            return
        with self.lock:
            self._fh.shift_indices(insert_at, delta)
            self._rebuild_message_history()
            if self.current_message_index >= insert_at:
                new_idx = self.current_message_index + delta
                self._update_current_index(new_idx if new_idx >= 0 else 0)

    def remap_indices(self, removed_indices: List[int]):
        """当消息列表删除了某些索引后，重新映射沙盒记录的索引。

        Args:
            removed_indices: 被删除的消息索引列表（删除前的原始索引）
        """
        if not removed_indices:
            return
        with self.lock:
            removed_set = set(removed_indices)

            def new_idx(old):
                if old in removed_set:
                    return -1
                return old - sum(1 for r in removed_set if r < old)

            # 经 _FileHistory 持锁重映射 file_history，返回保留记录 id 集合
            kept_ids = self._fh.remap_indices(new_idx)

            # 收集 orphan 记录：message_history 中存在但已不在 file_history
            # 保留集合中的记录（new_idx 仍有效时按新索引重挂）
            orphan_records: List[FileChangeRecord] = []
            for records in list(self.message_history.values()):
                for r in records:
                    ni = new_idx(r.message_index)
                    if ni >= 0 and id(r) not in kept_ids:
                        r.message_index = ni
                        orphan_records.append(r)

            # 从 file_history 重建 message_history，再合并 orphan
            self._rebuild_message_history()
            for r in orphan_records:
                self.message_history.setdefault(r.message_index, []).append(r)

            # 更新 current_message_index
            new_val = new_idx(self.current_message_index)
            self._update_current_index(new_val if new_val >= 0 else 0)

    def fold_indices(self, removed_indices: List[int], insert_index: Optional[int] = None):
        """上下文压缩后的索引折叠重映射（保留记录，不丢弃）。

        与 ``remap_indices``（消息删除即失效，用于 /undo、/editmsg 等会回滚
        文件的路径）不同：上下文压缩只把历史消息折叠为一条摘要，磁盘上的
        文件变更仍然有效，因此**不得丢弃**被折叠区间内的沙盒记录，否则
        ``/changes`` 与后续回滚都丢失这些文件的历史。

        映射规则（``removed_indices`` 为删除前的原始索引）：
          - 被折叠区间内的记录 → 重挂到折叠锚点（``insert_index``，为 None 时
            取最小被移除索引）；
          - 锚点之前的记录 → 索引不变；
          - 其余记录 → 按「删除 N 条 + 新增 M 条」平移。

        Args:
            removed_indices: 被折叠（移除）的消息索引列表（删除前原始索引）。
            insert_index: 折叠后新增摘要消息的插入位置（删除前坐标）；
                None 表示纯删除、无新增消息（降级删除策略）。
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
        with self.lock:
            self._fh.fold_indices(removed_sorted, insert_index)
            self._rebuild_message_history()
            self._update_current_index(
                fold_index(
                    self.current_message_index,
                    removed_sorted, removed_set, anchor, inserted,
                ),
            )

    def clear(self):
        """清空所有沙盒记录"""
        with self.lock:
            self._fh.clear()
            self.message_history.clear()
            self._update_current_index(0)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        stats = self._fh.get_stats()
        with self.lock:
            stats["current_message_index"] = self.current_message_index
        return stats


# 全局沙盒管理器实例
_global_sandbox_manager: Optional[SandboxManager] = None
_global_lock = threading.RLock()


def get_sandbox_manager() -> Optional[SandboxManager]:
    """获取全局沙盒管理器实例"""
    with _global_lock:
        return _global_sandbox_manager


def set_sandbox_manager(manager: SandboxManager):
    """设置全局沙盒管理器实例"""
    global _global_sandbox_manager
    with _global_lock:
        _global_sandbox_manager = manager


def create_sandbox_manager(max_history_per_file: int = 100,
                           owner_id: Any = None) -> SandboxManager:
    """创建并设置全局沙盒管理器（owner_id 用于多会话隔离）。"""
    manager = SandboxManager(
        max_history_per_file=max_history_per_file, owner_id=owner_id,
    )
    set_sandbox_manager(manager)
    return manager


# 线程局部存储，用于存储当前消息索引
_thread_local = threading.local()

# contextvars 用于 asyncio 环境下的安全传播（当 to_thread 切换线程时仍可读取）
_message_index_contextvar = contextvars.ContextVar('message_index', default=None)


class SandboxContext:
    """沙盒上下文管理器"""

    def __init__(self, message_index: int):
        self.message_index = message_index
        self.previous_index = None
        self._ctx_token = None

    def __enter__(self):
        """进入上下文，设置当前消息索引"""
        self.previous_index = getattr(_thread_local, 'current_message_index', None)
        _thread_local.current_message_index = self.message_index
        # 保存 token 精确恢复 contextvar（嵌套场景下用值恢复可能被兄弟
        # context 的写入覆盖——token reset 保证恢复到本上下文进入前的状态）
        self._ctx_token = _message_index_contextvar.set(self.message_index)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，恢复之前的消息索引"""
        if self.previous_index is not None:
            _thread_local.current_message_index = self.previous_index
        else:
            if hasattr(_thread_local, 'current_message_index'):
                delattr(_thread_local, 'current_message_index')
        if self._ctx_token is not None:
            try:
                _message_index_contextvar.reset(self._ctx_token)
            except (ValueError, RuntimeError):
                # 跨 context 恢复（token 不属当前 context）→ 退回按值恢复
                _message_index_contextvar.set(self.previous_index)
            finally:
                self._ctx_token = None
        else:
            _message_index_contextvar.set(self.previous_index)


def get_current_message_index() -> Optional[int]:
    """获取当前消息索引，优先使用 contextvars（asyncio 安全），回退到 threading.local。"""
    idx = _message_index_contextvar.get(None)
    if idx is not None:
        return idx
    return getattr(_thread_local, 'current_message_index', None)


def set_current_message_index(message_index: int):
    """设置当前线程的消息索引"""
    _thread_local.current_message_index = message_index
    _message_index_contextvar.set(message_index)


def clear_current_message_index():
    """清除当前线程的消息索引"""
    _message_index_contextvar.set(None)
    if hasattr(_thread_local, 'current_message_index'):
        delattr(_thread_local, 'current_message_index')


def record_file_change_from_context(file_path: str, content_before: Optional[str],
                                   content_after: Optional[str],
                                   tool_name: str = "write_file",
                                   record_type: str = "file") -> Optional[FileChangeRecord]:
    """
    从上下文记录文件修改

    使用当前线程的消息索引记录文件修改。
    如果没有设置消息索引或没有沙盒管理器，返回None。
    """
    sandbox_manager = get_sandbox_manager()
    if not sandbox_manager:
        return None

    message_index = get_current_message_index()
    if message_index is None:
        # 尝试使用沙盒管理器的当前索引（线程安全）
        message_index = sandbox_manager.get_current_message_index_safe()

    if message_index is not None:
        return sandbox_manager.record_file_change(
            file_path, content_before, content_after, message_index, tool_name,
            record_type,
        )

    return None


async def async_record_file_change_from_context(
    file_path: str, content_before: Optional[str],
    content_after: Optional[str],
    tool_name: str = "write_file",
    record_type: str = "file",
) -> Optional[FileChangeRecord]:
    """异步版本：从上下文记录文件修改，使用 asyncio.to_thread 包装"""
    return await asyncio.to_thread(
        record_file_change_from_context, file_path, content_before,
        content_after, tool_name, record_type,
    )


def record_file_changes_from_context(
    changes: Iterable[Tuple[str, Optional[str], Optional[str], str, str]],
) -> List[FileChangeRecord]:
    """从上下文批量记录文件修改（同一消息索引）。

    Args:
        changes: 可迭代的 5 元组
            ``(file_path, content_before, content_after, tool_name, record_type)``。

    Returns:
        创建的 FileChangeRecord 列表；无沙盒管理器 / 无消息索引时为空列表。
    """
    sandbox_manager = get_sandbox_manager()
    if not sandbox_manager:
        return []
    message_index = get_current_message_index()
    if message_index is None:
        message_index = sandbox_manager.get_current_message_index_safe()
    if message_index is None:
        return []
    return sandbox_manager.record_file_changes_batch(changes, message_index)


async def async_record_file_changes_from_context(
    changes: Iterable[Tuple[str, Optional[str], Optional[str], str, str]],
) -> List[FileChangeRecord]:
    """异步版本：从上下文批量记录文件修改（一次 to_thread，避免逐条线程切换）。"""
    return await asyncio.to_thread(
        record_file_changes_from_context, list(changes),
    )
