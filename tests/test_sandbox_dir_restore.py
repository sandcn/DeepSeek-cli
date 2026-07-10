"""测试沙盒管理器目录还原 — 回归测试

验证修复：
- write_file 隐式创建的父目录被记录到沙盒（方案A）
- restore_to_message 回滚后可删除这些空父目录
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from src.core.sandbox_manager import SandboxManager


class TestSandboxDirectoryRestore:
    """restore_to_message 目录还原回归测试"""

    @pytest.fixture
    def sandbox(self):
        """创建独立的 SandboxManager 实例"""
        return SandboxManager(max_history_per_file=100)

    @pytest.fixture
    def workdir(self):
        """临时工作目录"""
        with tempfile.TemporaryDirectory() as d:
            yield d

    def test_restore_removes_empty_parent_dir_for_implicitly_created_file(
        self, sandbox, workdir,
    ):
        """回滚后，write_file 隐式创建的父目录被清理

        场景：write_file 创建 /workdir/sub/file.txt
         — 沙盒记录 /workdir/sub（directory, 隐式创建）和 /workdir/sub/file.txt（file）
         — 回滚到操作前 → 两者都消失
        """
        subdir = os.path.join(workdir, "sub")
        filepath = os.path.join(subdir, "file.txt")

        # 初始状态：无目录无文件
        assert not os.path.exists(subdir)
        assert not os.path.exists(filepath)

        msg_idx_base = 0

        # 1. 模拟 mkdir 隐式创建父目录 sub/
        sandbox.record_file_change(
            subdir, content_before=None, content_after="",
            message_index=msg_idx_base + 1, tool_name="write_file",
            record_type="directory",
        )
        # 2. 模拟 write_file 创建文件
        sandbox.record_file_change(
            filepath, content_before=None, content_after="hello",
            message_index=msg_idx_base + 1, tool_name="write_file",
        )
        # 在磁盘上实际创建（模拟工具执行结果）
        os.makedirs(subdir, exist_ok=True)
        with open(filepath, "w") as f:
            f.write("hello")

        # 验证磁盘状态
        assert os.path.isdir(subdir)
        assert os.path.isfile(filepath)

        # 回滚到操作前（message_index <= 0）
        results = sandbox.restore_to_message(msg_idx_base)

        # 文件被删除
        assert results.get(filepath, False) is True
        assert not os.path.exists(filepath)
        # 目录也被删除（关键回归点）
        assert results.get(subdir, False) is True
        assert not os.path.exists(subdir)

    def test_restore_removes_nested_parent_dirs(
        self, sandbox, workdir,
    ):
        """回滚后，多层隐式创建的父目录全部清理

        场景：write_file 创建 /workdir/a/b/c/file.txt
         — 沙盒记录 a/、a/b/、a/b/c/、a/b/c/file.txt
         — 回滚 → 四者全部消失
        """
        a = os.path.join(workdir, "a")
        ab = os.path.join(workdir, "a", "b")
        abc = os.path.join(workdir, "a", "b", "c")
        filepath = os.path.join(abc, "file.txt")

        msg_idx_base = 0
        msg_idx = msg_idx_base + 1

        # 模拟 write_file 写入前 async_makedirs_and_record 依次记录
        sandbox.record_file_change(
            a, content_before=None, content_after="",
            message_index=msg_idx, tool_name="write_file",
            record_type="directory",
        )
        sandbox.record_file_change(
            ab, content_before=None, content_after="",
            message_index=msg_idx, tool_name="write_file",
            record_type="directory",
        )
        sandbox.record_file_change(
            abc, content_before=None, content_after="",
            message_index=msg_idx, tool_name="write_file",
            record_type="directory",
        )
        sandbox.record_file_change(
            filepath, content_before=None, content_after="content",
            message_index=msg_idx, tool_name="write_file",
        )
        # 磁盘创建
        os.makedirs(abc, exist_ok=True)
        with open(filepath, "w") as f:
            f.write("content")

        # 回滚
        results = sandbox.restore_to_message(msg_idx_base)

        assert results.get(filepath, False) is True
        assert not os.path.exists(filepath)
        assert not os.path.exists(abc)
        assert not os.path.exists(ab)
        assert not os.path.exists(a)

    def test_restore_keeps_existing_parent_when_only_file_is_new(
        self, sandbox, workdir,
    ):
        """回滚时保留原本就存在的父目录

        场景：目录 sub/ 原本存在（操作前就有），只新增 file.txt
         — 沙盒只记录 file.txt
         — 回滚 → file.txt 删除，sub/ 保留
        """
        subdir = os.path.join(workdir, "sub")
        filepath = os.path.join(subdir, "file.txt")

        # 目录原本就存在
        os.makedirs(subdir)
        assert os.path.isdir(subdir)

        msg_idx_base = 0

        # 仅记录文件（不记录目录，因为目录已存在，async_makedirs_and_record 不会重复记录）
        sandbox.record_file_change(
            filepath, content_before=None, content_after="hello",
            message_index=msg_idx_base + 1, tool_name="write_file",
        )
        with open(filepath, "w") as f:
            f.write("hello")

        results = sandbox.restore_to_message(msg_idx_base)

        # 文件被删除
        assert results.get(filepath, False) is True
        assert not os.path.exists(filepath)
        # 目录仍然存在（不受影响）
        assert os.path.isdir(subdir)

    def test_restore_does_not_remove_nonempty_dir(
        self, sandbox, workdir,
    ):
        """回滚时不清除包含其他文件的非空目录

        场景：sub/ 和 sub/old.txt 原本存在，操作只新增 sub/new.txt
         — 回滚 → 只删除 sub/new.txt，sub/ 和 sub/old.txt 保留
        """
        subdir = os.path.join(workdir, "sub")
        old_file = os.path.join(subdir, "old.txt")
        new_file = os.path.join(subdir, "new.txt")

        os.makedirs(subdir)
        with open(old_file, "w") as f:
            f.write("old")

        msg_idx_base = 0

        sandbox.record_file_change(
            new_file, content_before=None, content_after="new",
            message_index=msg_idx_base + 1, tool_name="write_file",
        )
        with open(new_file, "w") as f:
            f.write("new")

        results = sandbox.restore_to_message(msg_idx_base)

        # 新文件删除
        assert results.get(new_file, False) is True
        assert not os.path.exists(new_file)
        # 旧文件和目录保留
        assert os.path.isfile(old_file)
        assert os.path.isdir(subdir)

    def test_mk_with_parents_records_all_intermediate_dirs(
        self, sandbox, workdir,
    ):
        """mk -p 创建多层目录时，每层都被记录

        场景：mk -p /workdir/x/y/z
         — 沙盒记录 x/、x/y/、x/y/z/（directory 类型）
         — 回滚 → 全部消失
        """
        x = os.path.join(workdir, "x")
        xy = os.path.join(workdir, "x", "y")
        xyz = os.path.join(workdir, "x", "y", "z")

        msg_idx_base = 0
        msg_idx = msg_idx_base + 1

        # 模拟 async_makedirs_and_record 逐级记录
        sandbox.record_file_change(
            x, content_before=None, content_after="",
            message_index=msg_idx, tool_name="mk",
            record_type="directory",
        )
        sandbox.record_file_change(
            xy, content_before=None, content_after="",
            message_index=msg_idx, tool_name="mk",
            record_type="directory",
        )
        sandbox.record_file_change(
            xyz, content_before=None, content_after="",
            message_index=msg_idx, tool_name="mk",
            record_type="directory",
        )
        os.makedirs(xyz)

        results = sandbox.restore_to_message(msg_idx_base)

        assert not os.path.exists(xyz)
        assert not os.path.exists(xy)
        assert not os.path.exists(x)
