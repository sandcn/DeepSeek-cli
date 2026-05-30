#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件历史记录管理器（纯数据类）

管理文件修改历史，支持按消息索引查询和恢复文件状态。
不依赖 SandboxManager 的锁层次和索引映射。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from src._compat import dataclass
from typing import Any, Dict, List, Optional

from .file_change_record import FileChangeRecord

_logger = logging.getLogger(__name__)


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
    - 历史状态查询（get_snapshot / get_record_type_at_message）
    - 文件恢复（restore — 含原子写入、备份、类型转换）
    - 记录清理（remove_after_index / _cleanup_old_records）
    - 元数据（get_all_records / get_stats / deep_copy_records）
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

    def _cleanup_old_records(self, file_path: str) -> None:
        """清理旧记录，保持历史大小在限制内。"""
        records = self.file_history.get(file_path)
        if records is None:
            return
        if len(records) > self.max_history_per_file:
            keep_count = self.max_history_per_file
            self.file_history[file_path] = records[-keep_count:]

    # ── 查询 ────────────────────────────────────────────

    def get_snapshot(self, file_path: str, message_index: int) -> Optional[str]:
        """获取文件在指定消息索引时的内容（纯内存查询）。

        Returns:
            文件内容，None 表示文件不存在。
        """
        with self._lock:
            records = self.file_history.get(file_path)
            if records is None:
                return None

            last_record = None
            for record in sorted(records, key=lambda r: r.message_index):
                if record.message_index <= message_index:
                    last_record = record
                else:
                    break

            if last_record:
                return last_record.content_after
            # 所有记录都在目标索引之后 → 返回首次修改前的状态
            return records[0].content_before

    def get_record_type_at_message(
        self, file_path: str, message_index: int,
    ) -> Optional[str]:
        """获取指定路径在指定消息索引时的记录类型（"file" / "directory" / None）。"""
        with self._lock:
            records = self.file_history.get(file_path)
            if records is None:
                return None

            last_record = None
            for record in sorted(records, key=lambda r: r.message_index):
                if record.message_index <= message_index:
                    last_record = record
                else:
                    break
            if last_record:
                return last_record.record_type
            return records[0].record_type

    # ── 恢复 ────────────────────────────────────────────

    def restore(self, target_message_index: int) -> Dict[str, bool]:
        """恢复到指定消息索引的文件状态。

        三阶段：
        - Phase 1：持锁收集快照数据
        - Phase 2：释放锁后执行文件 I/O
        - Phase 3：重新持锁清理记录

        Returns:
            {file_path: success} 字典。
        """
        # Phase 1: 持锁收集快照
        with self._lock:
            affected_files_set: set[str] = set()
            for records in self.file_history.values():
                for record in records:
                    if record.message_index > target_message_index:
                        affected_files_set.add(record.file_path)

            snapshots: list[FileSnapshot] = []
            for file_path in affected_files_set:
                target_content = self.get_snapshot(file_path, target_message_index)
                target_type = self.get_record_type_at_message(
                    file_path, target_message_index,
                )
                snapshots.append(FileSnapshot(file_path, target_content, target_type))

            # 按路径深度降序排列（深层优先），确保子路径先于父路径处理
            snapshots.sort(key=lambda s: s.file_path.count(os.sep), reverse=True)

        # Phase 2: 释放锁后执行文件 I/O
        results: Dict[str, bool] = {}
        for snap in snapshots:
            backup_path = None
            try:
                if snap.target_content is None:
                    # 路径不应存在
                    if os.path.exists(snap.file_path):
                        if os.path.isdir(snap.file_path):
                            shutil.rmtree(snap.file_path)
                        else:
                            os.remove(snap.file_path)
                    results[snap.file_path] = True
                elif snap.target_type == "directory":
                    # 恢复目录
                    if os.path.isfile(snap.file_path):
                        os.remove(snap.file_path)
                    os.makedirs(snap.file_path, exist_ok=True)
                    results[snap.file_path] = True
                else:
                    # 恢复文件（原子写入）
                    if os.path.isdir(snap.file_path):
                        shutil.rmtree(snap.file_path)

                    os.makedirs(
                        os.path.dirname(snap.file_path) or '.', exist_ok=True,
                    )

                    # 备份当前文件
                    if os.path.isfile(snap.file_path):
                        fd_bak, backup_path = tempfile.mkstemp(
                            prefix=f".bak_{os.path.basename(snap.file_path)}.",
                            dir=os.path.dirname(snap.file_path) or '.',
                        )
                        os.close(fd_bak)
                        shutil.copy2(snap.file_path, backup_path)

                    # 原子写入
                    fd_new, tmp_path = tempfile.mkstemp(
                        prefix=f".new_{os.path.basename(snap.file_path)}.",
                        dir=os.path.dirname(snap.file_path) or '.',
                    )
                    try:
                        with os.fdopen(
                            fd_new, 'w', encoding='utf-8', errors='replace',
                        ) as f:
                            f.write(snap.target_content)
                        os.replace(tmp_path, snap.file_path)
                        tmp_path = None  # type: ignore[assignment]
                    finally:
                        if tmp_path is not None:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass

                    # 清理备份
                    if backup_path is not None:
                        try:
                            os.unlink(backup_path)
                        except OSError:
                            pass

                    results[snap.file_path] = True
            except Exception as e:
                _logger.warning(
                    "恢复文件失败: %s — %s", snap.file_path, e,
                )
                results[snap.file_path] = False
                # 异常路径：尝试从备份恢复
                try:
                    if backup_path is not None and os.path.exists(backup_path):
                        try:
                            if os.path.isfile(backup_path):
                                shutil.copy2(backup_path, snap.file_path)
                        except Exception:
                            _logger.debug(
                                "沙盒还原 shutil.copy2 失败: %s", snap.file_path,
                            )
                    for _p in [backup_path]:
                        if _p and os.path.exists(_p):
                            os.unlink(_p)
                except OSError:
                    pass

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
