"""测试 sandbox_manager.py — remap_indices 线程安全修复。

测试 remap_indices 方法在访问 _fh.file_history 时正确获取 _fh._lock。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.sandbox_manager import SandboxManager


class TestRemapIndicesLock:
    """测试 remap_indices 锁正确性。"""

    def test_remap_indices_acquires_fh_lock_regression(self):
        """remap_indices 中 _fh._lock 被获取 → __enter__ 被调用。"""
        sm = SandboxManager()
        mock_fh_lock = MagicMock()
        sm._fh._lock = mock_fh_lock

        # 调用 remap_indices（有实际索引参数，确保进入 with self._fh._lock 块）
        sm.remap_indices([1, 2])

        # 验证 _fh._lock.__enter__ 被调用
        mock_fh_lock.__enter__.assert_called()
        mock_fh_lock.__exit__.assert_called()

    def test_remap_indices_functional_regression(self):
        """remap_indices 正确重映射 message_index。"""
        sm = SandboxManager()

        # 预先写入 file_history 记录（通过 _fh.record 直接写入，绕过 record_file_change）
        r_a1 = sm._fh.record("/tmp/test_a.txt", "before_a", "after_a", 1, "write_file", "file")
        r_b = sm._fh.record("/tmp/test_b.txt", "before_b", "after_b", 2, "write_file", "file")
        r_a2 = sm._fh.record("/tmp/test_a.txt", "after_a", "after_a2", 3, "write_file", "file")

        # 手动同步 message_history（模拟 record_file_change 的副作用）
        sm.message_history = {}
        for r in [r_a1, r_b, r_a2]:
            sm.message_history.setdefault(r.message_index, []).append(r)
        sm.current_message_index = 3

        # 模拟删除索引 1（即消息索引 1 被删除，>=1 的都应 -1）
        sm.remap_indices([1])

        # 验证所有剩余记录的 message_index 正确重映射
        all_records = sm._fh.get_all_records()
        message_indices = sorted(set(r.message_index for r in all_records))
        assert message_indices == [1, 2], (
            f"期望 [1, 2]，实际 {message_indices}"
        )

        # 验证 message_history 也被正确重建
        assert set(sm.message_history.keys()) == {1, 2}

        # 验证 current_message_index 从 3 更新为 2
        assert sm.current_message_index == 2
