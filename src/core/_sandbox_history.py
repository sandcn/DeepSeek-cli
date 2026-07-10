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

# ── 沙盒根目录 ─────────────────────────────────────────
# 所有文件操作的工作目录，作为路径白名单校验的基准
_SANDBOX_ROOT: str = os.getcwd()


def _validate_sandbox_path(
    file_path: str,
    sandbox_root: str | None = _SANDBOX_ROOT,
) -> str:
    """验证文件路径是否在沙盒范围内，防止路径穿越。

    使用 os.path.realpath() 解析符号链接，然后进行双重穿越检测：
    1. 基础检测：归一化后以 ``..`` 开头或为绝对路径 → 拦截
    2. 符号链接解析后，若提供了 sandbox_root，验证真实路径在沙盒根目录下

    ⚠ TOCTOU 声明：此函数校验与后续文件 I/O 之间存在 TOCTOU 窗口
       （校验→操作期间，恶意进程可将普通文件替换为指向沙盒外的符号链接）。
       此为单人本地 CLI 场景，风险极低，当前不做 TOCTOU 消除处理。

    Args:
        file_path: 待验证的文件路径
        sandbox_root: 沙盒根目录（默认当前工作目录）。
                      为 None 时不进行沙盒白名单校验（仅做基础穿越检测）。

    Returns:
        验证通过的安全路径（已解析符号链接，若文件不存在则返回 normpath 结果）。

    Raises:
        ValueError: 路径校验失败时抛出。
    """
    # 基础归一化 + 穿越检测
    normalized = os.path.normpath(file_path)
    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(
            f"路径穿越已拦截: {file_path!r} (归一化后: {normalized!r})"
        )

    # 尝试解析符号链接（路径不存在时回退到 normpath 结果）
    try:
        real_path = os.path.realpath(file_path)
    except (FileNotFoundError, OSError):
        # 路径不存在，不可能是符号链接穿越，回退到 normpath 结果
        return normalized

    # 沙盒根目录白名单校验
    if sandbox_root is not None:
        sandbox_real = os.path.realpath(sandbox_root)
        common = os.path.commonpath([real_path, sandbox_real])
        if common != sandbox_real:
            raise ValueError(
                f"路径不在沙盒范围内: {file_path!r} "
                f"(realpath: {real_path!r}, sandbox: {sandbox_real!r})"
            )

    return real_path


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
        """记录文件修改（仅写入 file_history，不涉及 message_history）。

        记录前验证路径安全性，拒绝穿越路径。

        Raises:
            ValueError: 路径校验失败（穿越沙盒范围）。
        """
        # 路径安全校验：记录时就拒绝穿越路径
        _validate_sandbox_path(file_path)

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

    def shift_indices(self, insert_at: int) -> None:
        """当消息列表中插入消息后，将所有 >= insert_at 的索引 +1。"""
        with self._lock:
            for records in self.file_history.values():
                for r in records:
                    if r.message_index >= insert_at:
                        r.message_index += 1

    def remap_indices(self, removed_indices: List[int]) -> None:
        """当消息列表删除消息后，重新映射所有记录的 message_index。

        Args:
            removed_indices: 被删除的消息索引列表（删除前的原始索引）
        """
        if not removed_indices:
            return
        with self._lock:
            removed_set = set(removed_indices)
            def new_idx(old):
                if old in removed_set:
                    return -1
                return old - sum(1 for r in removed_set if r < old)
            for path, records in list(self.file_history.items()):
                new_records = []
                for r in records:
                    ni = new_idx(r.message_index)
                    if ni >= 0:
                        r.message_index = ni
                        new_records.append(r)
                if new_records:
                    self.file_history[path] = new_records
                else:
                    del self.file_history[path]

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
        - Phase 1：持锁收集快照数据 + 清理记录
        - Phase 2：释放锁后执行文件 I/O
        - Phase 3：重新持锁（清理已在 Phase 1 完成）

        Returns:
            {file_path: success} 字典。
        """
        # Phase 1: 持锁收集快照 + 清理记录
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

            # 【TOCTOU 修复】在持锁状态下清理记录，防止 Phase 2→3 之间
            # 另一线程 record() 写入的新记录被错误移除
            self.remove_after_index(target_message_index)

        # Phase 2: 释放锁后执行文件 I/O
        results: Dict[str, bool] = {}

        # ── 路径安全校验（防止路径穿越 + 符号链接遍历） ──
        # 使用 _validate_sandbox_path() 解析符号链接并验证沙盒白名单
        validated_snapshots: list[FileSnapshot] = []
        for snap in snapshots:
            try:
                safe_path = _validate_sandbox_path(snap.file_path)
                if snap.file_path != safe_path:
                    _logger.warning("路径安全归一化: %s → %s", snap.file_path, safe_path)
                    snap.file_path = safe_path
                validated_snapshots.append(snap)
            except ValueError as e:
                _logger.warning("路径安全校验失败，已跳过: %s — %s", snap.file_path, e)
                results[snap.file_path] = False
        snapshots = validated_snapshots

        for snap in snapshots:
            backup_path = None
            tmp_path = None
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
                        tmp_path = None  # 标记已成功替换，阻止 finally 中清理
                    finally:
                        if tmp_path is not None:
                            try:
                                os.unlink(tmp_path)
                            except OSError:
                                pass

                    # 清理备份（仅当原子写入成功后清理）
                    if backup_path is not None:
                        try:
                            os.unlink(backup_path)
                            backup_path = None  # 标记已清理
                        except OSError:
                            pass

                    results[snap.file_path] = True
            except Exception as e:
                _logger.warning(
                    "恢复文件失败: %s — %s", snap.file_path, e,
                )
                results[snap.file_path] = False
                # 异常路径：尝试从备份恢复
                if backup_path is not None and os.path.exists(backup_path):
                    try:
                        if os.path.isfile(backup_path):
                            shutil.copy2(backup_path, snap.file_path)
                    except Exception:
                        _logger.debug("沙盒还原 shutil.copy2 失败: %s", snap.file_path)
                # 清理残留的备份文件
                if backup_path is not None and os.path.exists(backup_path):
                    try:
                        os.unlink(backup_path)
                    except OSError:
                        pass

        # Phase 3: 重新持锁（清理已在 Phase 1 持锁时完成）
        with self._lock:
            pass  # 保持锁层次一致性，清理已在 Phase 1 完成

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
