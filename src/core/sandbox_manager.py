#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件沙盒管理器

记录大模型修改文件的信息，支持在消息截断时恢复文件状态。
"""

import asyncio
import contextvars
import os
import threading
from typing import Any, Dict, List, Optional

from .internal.shared._sandbox_history import _FileHistory
from .file_change_record import FileChangeRecord  # noqa: F401 — re-exported for backward compat


class SandboxManager:
    """文件沙盒管理器

    管理文件修改历史，支持按消息索引恢复文件状态。

    ⚠️ 锁层次（必须遵守，防止死锁）:
        ContextManager._lock → SandboxManager.lock
    解释：ContextManager 在持有 _lock 期间可能通过 on_messages_changed 回调
    调用本类的 shift_indices()/remap_indices()。因此 SandboxManager.lock
    永远不得在 ContextManager._lock 之前获取，否则形成 ABBA 死锁。
    """

    def __init__(self, max_history_per_file: int = 100):
        """
        初始化沙盒管理器

        Args:
            max_history_per_file: 每个文件最大历史记录数
        """
        # 文件历史记录（委托给 _FileHistory）
        self._fh = _FileHistory(max_history_per_file)
        # 共享 dict 引用，保持向后兼容（外部通过 @property file_history 只读访问）
        self._file_history_ref = self._fh.file_history

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
        """
        return dict(self._file_history_ref)

    # ── 当前消息索引管理 ───────────────────────────────────

    def _update_current_index(self, new_idx: int) -> None:
        """统一更新当前消息索引，作为所有写入 current_message_index 的唯一入口。"""
        self.current_message_index = new_idx

    def _rebuild_message_history(self) -> None:
        """从 file_history 重建 message_history。

        消除 shift_indices/remap_indices 中重复的 message_history 重建逻辑。
        从 _fh.file_history 遍历所有记录并按 message_index 重新分组。
        """
        new_mh: dict[int, list[FileChangeRecord]] = {}
        for records in self._fh.file_history.values():
            for r in records:
                new_mh.setdefault(r.message_index, []).append(r)
        self.message_history = new_mh

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
        """
        # 委托给 _FileHistory 记录文件历史
        record = self._fh.record(
            file_path, content_before, content_after, message_index,
            tool_name, record_type,
        )

        # 管理消息索引映射
        with self.lock:
            if message_index not in self.message_history:
                self.message_history[message_index] = []
            self.message_history[message_index].append(record)
            self._update_current_index(
                max(self.current_message_index, message_index),
            )

        return record

    async def async_record_file_change(self, file_path: str, content_before: Optional[str],
                                       content_after: Optional[str], message_index: int,
                                       tool_name: str = "write_file",
                                       record_type: str = "file") -> FileChangeRecord:
        """异步记录文件修改（内部同步操作已包装为 async）"""
        return await asyncio.to_thread(
            self.record_file_change, file_path, content_before, content_after,
            message_index, tool_name, record_type,
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
        """
        # Phase 1: 查询 _FileHistory（纯内存操作，内部持锁）
        result = self._fh.get_snapshot(file_path, message_index)
        if result is not None:
            return result

        # Phase 2: 无记录时回退到磁盘状态
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

    def shift_indices(self, insert_at: int):
        """当在消息列表中插入一条消息后，将 >= insert_at 的索引全部 +1。"""
        with self.lock:
            for records in self._fh.file_history.values():
                for r in records:
                    if r.message_index >= insert_at:
                        r.message_index += 1
            self._rebuild_message_history()
            if self.current_message_index >= insert_at:
                self._update_current_index(self.current_message_index + 1)

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

            # 通过 file_history 更新所有记录的 message_index
            for path, records in list(self._fh.file_history.items()):
                new_records = []
                for r in records:
                    ni = new_idx(r.message_index)
                    if ni >= 0:
                        r.message_index = ni
                        new_records.append(r)
                if new_records:
                    self._fh.file_history[path] = new_records
                else:
                    del self._fh.file_history[path]

            # ★ 收集 orphan 记录：仅在 message_history 中存在但在 file_history
            #   中被移除的记录
            orphan_records: List[FileChangeRecord] = []
            for records in list(self.message_history.values()):
                for r in records:
                    ni = new_idx(r.message_index)
                    if ni >= 0:
                        if r.file_path in self._fh.file_history:
                            if r not in self._fh.file_history[r.file_path]:
                                r.message_index = ni
                                orphan_records.append(r)
                        else:
                            r.message_index = ni
                            orphan_records.append(r)

            # 从 file_history 重建 message_history，再合并 orphan
            self._rebuild_message_history()
            for r in orphan_records:
                self.message_history.setdefault(r.message_index, []).append(r)

            # 更新 current_message_index
            new_val = new_idx(self.current_message_index)
            self._update_current_index(new_val if new_val >= 0 else 0)

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


def create_sandbox_manager(max_history_per_file: int = 100) -> SandboxManager:
    """创建并设置全局沙盒管理器"""
    manager = SandboxManager(max_history_per_file=max_history_per_file)
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

    def __enter__(self):
        """进入上下文，设置当前消息索引"""
        self.previous_index = getattr(_thread_local, 'current_message_index', None)
        _thread_local.current_message_index = self.message_index
        _message_index_contextvar.set(self.message_index)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，恢复之前的消息索引"""
        if self.previous_index is not None:
            _thread_local.current_message_index = self.previous_index
            _message_index_contextvar.set(self.previous_index)
        else:
            if hasattr(_thread_local, 'current_message_index'):
                delattr(_thread_local, 'current_message_index')
            _message_index_contextvar.set(None)


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
