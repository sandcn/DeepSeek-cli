"""Tests for src/core/sandbox_manager.py — FileChangeRecord, SandboxManager, 全局函数"""

import os
import time
import threading

import pytest

from src.core.sandbox_manager import (
    FileChangeRecord,
    SandboxManager,
    get_sandbox_manager,
    set_sandbox_manager,
    create_sandbox_manager,
    SandboxContext,
    get_current_message_index,
    set_current_message_index,
    clear_current_message_index,
    record_file_change_from_context,
)


# ═══════════════════════════════════════════════════════════════
# FileChangeRecord 创建与属性
# ═══════════════════════════════════════════════════════════════

class TestFileChangeRecordCreation:
    """FileChangeRecord 构造与基本属性"""

    def test_new_file_record(self):
        """新建文件: content_before=None, content_after=内容"""
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before=None,
            content_after="hello world",
            message_index=0,
        )
        assert record.file_path == "/tmp/test.txt"
        assert record.content_before is None
        assert record.content_after == "hello world"
        assert record.message_index == 0

    def test_delete_file_record(self):
        """删除文件: content_before=内容, content_after=None"""
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before="old content",
            content_after=None,
            message_index=1,
        )
        assert record.content_before == "old content"
        assert record.content_after is None

    def test_modify_file_record(self):
        """修改文件: content_before 和 content_after 均有值"""
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before="old",
            content_after="new",
            message_index=2,
        )
        assert record.content_before == "old"
        assert record.content_after == "new"

    def test_default_timestamp(self):
        """未指定 timestamp 时自动使用当前时间"""
        before = time.time()
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before=None,
            content_after="data",
            message_index=0,
        )
        after = time.time()
        assert before <= record.timestamp <= after

    def test_custom_timestamp(self):
        """传入自定义 timestamp"""
        ts = 12345.678
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before=None,
            content_after="data",
            message_index=0,
            timestamp=ts,
        )
        assert record.timestamp == ts

    def test_default_tool_name(self):
        """默认 tool_name 为 'write_file'"""
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before=None,
            content_after="data",
            message_index=0,
        )
        assert record.tool_name == "write_file"

    def test_custom_tool_name(self):
        """传入自定义 tool_name"""
        record = FileChangeRecord(
            file_path="/tmp/test.txt",
            content_before=None,
            content_after="data",
            message_index=0,
            tool_name="read_file",
        )
        assert record.tool_name == "read_file"


# ═══════════════════════════════════════════════════════════════
# FileChangeRecord get_change_type
# ═══════════════════════════════════════════════════════════════

class TestChangeType:
    """get_change_type() 返回正确的修改类型"""

    def test_new_file(self):
        """新建文件 → '新建文件'"""
        record = FileChangeRecord("f", None, "content", 0)
        assert record.get_change_type() == "新建文件"

    def test_delete_file(self):
        """删除文件 → '删除文件'"""
        record = FileChangeRecord("f", "content", None, 0)
        assert record.get_change_type() == "删除文件"

    def test_no_change(self):
        """无变化 → '无变化'"""
        record = FileChangeRecord("f", "same", "same", 0)
        assert record.get_change_type() == "无变化"

    def test_modify_file(self):
        """修改文件 → '修改文件'"""
        record = FileChangeRecord("f", "old", "new", 0)
        assert record.get_change_type() == "修改文件"


# ═══════════════════════════════════════════════════════════════
# FileChangeRecord apply / revert
# ═══════════════════════════════════════════════════════════════

class TestApplyRevert:
    """apply() 和 revert() 的文件 I/O 操作（使用 tmp_path 隔离）"""

    def test_apply_new_file(self, tmp_path):
        """apply 新建文件 — 将 content_after 写入新文件"""
        file_path = str(tmp_path / "new.txt")
        record = FileChangeRecord(file_path, None, "hello", 0)
        success = record.apply()
        assert success is True
        assert os.path.exists(file_path)
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "hello"

    def test_apply_update_file(self, tmp_path):
        """apply 更新文件 — 用 content_after 覆盖已有文件"""
        file_path = str(tmp_path / "update.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("old content")
        record = FileChangeRecord(file_path, "old content", "new content", 0)
        success = record.apply()
        assert success is True
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "new content"

    def test_apply_delete_file(self, tmp_path):
        """apply 删除文件 — content_after=None 时删除文件"""
        file_path = str(tmp_path / "delete_me.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("to be deleted")
        record = FileChangeRecord(file_path, "to be deleted", None, 0)
        success = record.apply()
        assert success is True
        assert not os.path.exists(file_path)

    def test_revert_restore_file(self, tmp_path):
        """revert 恢复文件 — 将文件恢复到 content_before 状态"""
        file_path = str(tmp_path / "revert.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("original")
        record = FileChangeRecord(file_path, "original", "modified", 0)
        # 先 apply 修改
        record.apply()
        assert open(file_path, encoding="utf-8").read() == "modified"
        # 再 revert 恢复
        success = record.revert()
        assert success is True
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "original"

    def test_revert_delete_created_file(self, tmp_path):
        """revert 删除新建的文件 — content_before=None 时删除文件"""
        file_path = str(tmp_path / "created.txt")
        record = FileChangeRecord(file_path, None, "new file", 0)
        record.apply()
        assert os.path.exists(file_path)
        success = record.revert()
        assert success is True
        assert not os.path.exists(file_path)

    def test_apply_revert_multiple_times(self, tmp_path):
        """多次来回 apply/revert 保持一致"""
        file_path = str(tmp_path / "pingpong.txt")
        record = FileChangeRecord(file_path, "state_a", "state_b", 0)

        # 第一次：apply → state_b
        record.apply()
        assert open(file_path, encoding="utf-8").read() == "state_b"

        # 第一次：revert → state_a
        record.revert()
        assert open(file_path, encoding="utf-8").read() == "state_a"

        # 第二次：apply → state_b
        record.apply()
        assert open(file_path, encoding="utf-8").read() == "state_b"

        # 第二次：revert → state_a
        record.revert()
        assert open(file_path, encoding="utf-8").read() == "state_a"

    def test_apply_creates_parent_directory(self, tmp_path):
        """apply 自动创建父目录"""
        file_path = str(tmp_path / "nested" / "sub" / "deep.txt")
        record = FileChangeRecord(file_path, None, "nested content", 0)
        success = record.apply()
        assert success is True
        assert os.path.exists(file_path)

    def test_revert_creates_parent_directory(self, tmp_path):
        """revert 自动创建父目录"""
        file_path = str(tmp_path / "nested" / "sub" / "restore.txt")
        record = FileChangeRecord(file_path, "restored", "current", 0)
        # apply 创建文件
        record.apply()
        # 删除后 revert（需要重建目录）
        os.remove(file_path)
        os.rmdir(os.path.dirname(file_path))
        success = record.revert()
        assert success is True
        assert os.path.exists(file_path)
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "restored"

    def test_revert_nonexistent_file_to_delete(self, tmp_path):
        """revert 时 content_before=None 且文件已不存在 — 不报错"""
        file_path = str(tmp_path / "already_gone.txt")
        record = FileChangeRecord(file_path, None, "was_here", 0)
        record.apply()  # 创建
        os.remove(file_path)  # 手动删除
        success = record.revert()  # 再次删除（文件已不存在）
        assert success is True


# ═══════════════════════════════════════════════════════════════
# SandboxManager 记录功能
# ═══════════════════════════════════════════════════════════════

class TestSandboxManagerRecord:
    """SandboxManager.record_file_change 及相关记录功能"""

    def test_record_file_change_returns_record(self):
        """record_file_change 返回 FileChangeRecord 实例"""
        sm = SandboxManager()
        record = sm.record_file_change(
            file_path="/tmp/a.txt",
            content_before=None,
            content_after="hello",
            message_index=0,
        )
        assert isinstance(record, FileChangeRecord)
        assert record.file_path == "/tmp/a.txt"

    def test_file_history_records(self):
        """file_history 正确记录文件修改历史"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)
        sm.record_file_change("/tmp/b.txt", None, "data", 1)

        assert "/tmp/a.txt" in sm.file_history
        assert "/tmp/b.txt" in sm.file_history
        assert len(sm.file_history["/tmp/a.txt"]) == 2
        assert len(sm.file_history["/tmp/b.txt"]) == 1

    def test_message_history_records(self):
        """message_history 按消息索引组织记录"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/b.txt", None, "data", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)

        assert 0 in sm.message_history
        assert 1 in sm.message_history
        assert len(sm.message_history[0]) == 2
        assert len(sm.message_history[1]) == 1

    def test_same_file_same_message_appends_multiple_records(self):
        """相同文件相同 message_index 时追加多条记录，保留完整历史"""
        sm = SandboxManager()
        r1 = sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        r2 = sm.record_file_change("/tmp/a.txt", "v1", "v2", 0)

        # file_history 中应有 2 条记录（r1 和 r2 独立追加上）
        assert len(sm.file_history["/tmp/a.txt"]) == 2
        assert sm.file_history["/tmp/a.txt"][0] is r1
        assert sm.file_history["/tmp/a.txt"][1] is r2
        # message_history[0] 中也应有 2 条
        assert len(sm.message_history[0]) == 2
        assert r1 in sm.message_history[0]
        assert r2 in sm.message_history[0]

    def test_current_message_index_updated(self):
        """current_message_index 更新为最大的 message_index"""
        sm = SandboxManager()
        assert sm.current_message_index == 0

        sm.record_file_change("/tmp/a.txt", None, "v1", 5)
        assert sm.current_message_index == 5

        sm.record_file_change("/tmp/b.txt", None, "data", 3)
        assert sm.current_message_index == 5  # 最大值不变

        sm.record_file_change("/tmp/c.txt", None, "data", 10)
        assert sm.current_message_index == 10

    def test_auto_cleanup_old_records(self):
        """自动清理旧记录（max_history_per_file 限制）"""
        sm = SandboxManager(max_history_per_file=4)
        for i in range(10):
            sm.record_file_change("/tmp/a.txt", f"v{i}", f"v{i+1}", i)
        # max_history_per_file=4，超过限制时保留最近的 2 条（max_history_per_file//2）
        # 第5条时清理到2条，继续添加2条到4，第9条时再清理到2条，继续加1条到3
        # 最终：记录9添加后 len=4（不超过max_history_per_file，不清除）
        assert len(sm.file_history["/tmp/a.txt"]) <= 4
        # 验证清理确实发生过——总记录数小于10
        assert len(sm.file_history["/tmp/a.txt"]) < 10

    def test_async_record_file_change(self):
        """异步版本 async_record_file_change"""
        sm = SandboxManager()
        import asyncio

        async def do_record():
            record = await sm.async_record_file_change(
                "/tmp/a.txt", None, "async_data", 42
            )
            return record

        record = asyncio.run(do_record())
        assert record.message_index == 42
        assert record.content_after == "async_data"
        assert len(sm.file_history["/tmp/a.txt"]) == 1

    def test_record_keeps_tool_name(self):
        """记录的工具名称被保留"""
        sm = SandboxManager()
        record = sm.record_file_change(
            "/tmp/a.txt", None, "data", 0, tool_name="read_file"
        )
        assert record.tool_name == "read_file"


# ═══════════════════════════════════════════════════════════════
# get_file_state_at_message
# ═══════════════════════════════════════════════════════════════

class TestGetFileState:
    """SandboxManager.get_file_state_at_message"""

    def test_no_records_returns_current_file_content(self, tmp_path):
        """无记录时返回当前文件状态"""
        file_path = str(tmp_path / "existing.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("current content")

        sm = SandboxManager()
        state = sm.get_file_state_at_message(file_path, 0)
        assert state == "current content"

    def test_no_records_file_not_exists_returns_none(self):
        """无记录且文件不存在时返回 None"""
        sm = SandboxManager()
        state = sm.get_file_state_at_message("/tmp/nonexistent.txt", 0)
        assert state is None

    def test_with_records_returns_state_at_index(self, tmp_path):
        """有记录时返回指定索引时的状态"""
        file_path = str(tmp_path / "tracked.txt")
        sm = SandboxManager()
        sm.record_file_change(file_path, None, "version_1", 1)
        sm.record_file_change(file_path, "version_1", "version_2", 2)
        sm.record_file_change(file_path, "version_2", "version_3", 3)

        # 索引 0 时无记录，应返回 records[0].content_before（None）
        assert sm.get_file_state_at_message(file_path, 0) is None
        # 索引 1 时应该是 version_1
        assert sm.get_file_state_at_message(file_path, 1) == "version_1"
        # 索引 2 时应该是 version_2
        assert sm.get_file_state_at_message(file_path, 2) == "version_2"
        # 索引 3 时应该是 version_3
        assert sm.get_file_state_at_message(file_path, 3) == "version_3"

    def test_records_after_target_returns_previous_state(self, tmp_path):
        """记录在目标索引之后时返回之前的状态"""
        file_path = str(tmp_path / "later.txt")
        sm = SandboxManager()
        sm.record_file_change(file_path, None, "initial", 2)
        sm.record_file_change(file_path, "initial", "later", 5)

        # 索引 3 时（在记录 2 之后、记录 5 之前），应该返回 records[0].content_after
        state = sm.get_file_state_at_message(file_path, 3)
        assert state == "initial"

        # 索引 1 时（在所有记录之前），应返回 records[0].content_before
        state = sm.get_file_state_at_message(file_path, 1)
        assert state is None

    def test_multiple_files_independent(self, tmp_path):
        """多次修改不同文件互不干扰"""
        file_a = str(tmp_path / "a.txt")
        file_b = str(tmp_path / "b.txt")
        sm = SandboxManager()
        sm.record_file_change(file_a, None, "a1", 1)
        sm.record_file_change(file_b, None, "b1", 1)
        sm.record_file_change(file_a, "a1", "a2", 2)

        assert sm.get_file_state_at_message(file_a, 1) == "a1"
        assert sm.get_file_state_at_message(file_a, 2) == "a2"
        assert sm.get_file_state_at_message(file_b, 1) == "b1"


# ═══════════════════════════════════════════════════════════════
# restore_to_message
# ═══════════════════════════════════════════════════════════════

class TestRestoreToMessage:
    """SandboxManager.restore_to_message"""

    def test_restore_to_specific_index(self, tmp_path):
        """恢复到指定消息索引的文件状态"""
        file_path = str(tmp_path / "restore_target.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("original")

        sm = SandboxManager()
        sm.record_file_change(file_path, "original", "v1", 1)
        sm.record_file_change(file_path, "v1", "v2", 2)

        # 恢复到索引 1 的状态 → 文件应回到 v1
        results = sm.restore_to_message(1)
        assert results[file_path] is True
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "v1"

    def test_restore_cleans_records_after_index(self, tmp_path):
        """恢复后，目标索引之后的记录被清理"""
        file_path = str(tmp_path / "clean_after.txt")
        sm = SandboxManager()
        sm.record_file_change(file_path, None, "v1", 1)
        sm.record_file_change(file_path, "v1", "v2", 2)
        sm.record_file_change(file_path, "v2", "v3", 3)

        sm.restore_to_message(1)

        # file_history 中只有索引 <= 1 的记录
        assert len(sm.file_history[file_path]) == 1
        assert sm.file_history[file_path][0].message_index == 1
        # message_history 中不应有索引 > 1 的记录
        assert 2 not in sm.message_history
        assert 3 not in sm.message_history

    def test_restore_updates_current_message_index(self, tmp_path):
        """恢复后 current_message_index 更新为目标索引"""
        file_path = str(tmp_path / "update_index.txt")
        sm = SandboxManager()
        sm.record_file_change(file_path, None, "v1", 1)
        sm.record_file_change(file_path, "v1", "v2", 2)

        sm.restore_to_message(1)
        assert sm.current_message_index == 1

    def test_restore_multiple_files(self, tmp_path):
        """恢复涉及多个文件时全部正确处理"""
        file_a = str(tmp_path / "multi_a.txt")
        file_b = str(tmp_path / "multi_b.txt")
        with open(file_a, "w", encoding="utf-8") as f:
            f.write("a_orig")
        with open(file_b, "w", encoding="utf-8") as f:
            f.write("b_orig")

        sm = SandboxManager()
        sm.record_file_change(file_a, "a_orig", "a_v1", 1)
        sm.record_file_change(file_b, "b_orig", "b_v1", 1)
        sm.record_file_change(file_a, "a_v1", "a_v2", 2)
        sm.record_file_change(file_b, "b_v1", "b_v2", 2)

        # 恢复到索引 1
        results = sm.restore_to_message(1)
        assert results[file_a] is True
        assert results[file_b] is True
        with open(file_a, encoding="utf-8") as f:
            assert f.read() == "a_v1"
        with open(file_b, encoding="utf-8") as f:
            assert f.read() == "b_v1"

    def test_restore_to_index_zero(self, tmp_path):
        """恢复到索引 0（无记录时）"""
        file_path = str(tmp_path / "to_zero.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("original")

        sm = SandboxManager()
        sm.record_file_change(file_path, "original", "v1", 1)
        sm.record_file_change(file_path, "v1", "v2", 2)

        results = sm.restore_to_message(0)
        assert results[file_path] is True
        # 索引 0 时文件还是 original
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "original"

    def test_async_restore_to_message(self, tmp_path):
        """异步版本 async_restore_to_message"""
        file_path = str(tmp_path / "async_restore.txt")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("original")

        sm = SandboxManager()
        sm.record_file_change(file_path, "original", "changed", 1)
        import asyncio

        async def do_restore():
            return await sm.async_restore_to_message(0)

        results = asyncio.run(do_restore())
        assert results[file_path] is True
        with open(file_path, encoding="utf-8") as f:
            assert f.read() == "original"


# ═══════════════════════════════════════════════════════════════
# shift_indices
# ═══════════════════════════════════════════════════════════════

class TestShiftIndices:
    """SandboxManager.shift_indices"""

    def test_shift_indices_forward(self):
        """在索引处插入后，>= 该索引的记录 +1"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 2)

        sm.shift_indices(1)

        # 记录索引 >= 1 的应该 +1
        records = sm.file_history["/tmp/a.txt"]
        # 索引 0 保持不变
        assert records[0].message_index == 0
        # 索引 2 → 3
        assert records[1].message_index == 3

    def test_shift_indices_at_zero(self):
        """在索引 0 处插入，所有记录 +1"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)

        sm.shift_indices(0)

        records = sm.file_history["/tmp/a.txt"]
        assert records[0].message_index == 1
        assert records[1].message_index == 2

    def test_shift_current_message_index(self):
        """current_message_index 也更新"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 3)
        assert sm.current_message_index == 3

        sm.shift_indices(2)
        assert sm.current_message_index == 4

    def test_shift_message_history_keys(self):
        """message_history 键也更新"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/b.txt", None, "data", 2)

        sm.shift_indices(1)

        # 原索引 2 → 3
        assert 0 in sm.message_history
        assert 1 not in sm.message_history
        assert 3 in sm.message_history

    def test_shift_no_effect_when_insert_at_end(self):
        """在最大索引之后插入，不影响已有记录"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 2)

        sm.shift_indices(5)

        records = sm.file_history["/tmp/a.txt"]
        assert records[0].message_index == 0
        assert records[1].message_index == 2


# ═══════════════════════════════════════════════════════════════
# remap_indices
# ═══════════════════════════════════════════════════════════════

class TestRemapIndices:
    """SandboxManager.remap_indices"""

    def test_remap_removes_indices(self):
        """删除某些索引后，大于被删索引的记录前移"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 2)
        sm.record_file_change("/tmp/a.txt", "v2", "v3", 5)

        sm.remap_indices([2])

        records = sm.file_history["/tmp/a.txt"]
        # 索引 0 保持不变
        assert records[0].message_index == 0
        # 索引 2 被删除（记录被移除）
        # 索引 5 → 4（因为删除 2，前移 1）
        assert records[1].message_index == 4

    def test_remap_multiple_indices(self):
        """删除多个索引后正确重映射"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 1)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 3)
        sm.record_file_change("/tmp/a.txt", "v2", "v3", 5)

        sm.remap_indices([1, 3])

        records = sm.file_history["/tmp/a.txt"]
        # 索引 1 被删除
        # 索引 3 被删除（移除两条记录后只剩一个）
        assert len(records) == 1
        assert records[0].message_index == 3  # 5 - 2 = 3

    def test_remapped_records_removed_from_message_history(self):
        """被删除索引对应的记录从 message_history 移除"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 2)
        sm.record_file_change("/tmp/b.txt", None, "data", 2)

        sm.remap_indices([2])

        assert 2 not in sm.message_history
        assert 0 in sm.message_history

    def test_remap_current_message_index(self):
        """current_message_index 更新"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 3)
        assert sm.current_message_index == 3

        sm.remap_indices([1, 2])
        # 3 - 2 = 1
        assert sm.current_message_index == 1

    def test_remap_removes_all_returns_zero(self):
        """所有记录都被删除时 current_message_index 归零"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)

        sm.remap_indices([0, 1])

        assert sm.current_message_index == 0

    def test_remap_empty_list_no_change(self):
        """remap_indices([]) 无变化"""
        sm = SandboxManager()
        r1 = sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        r2 = sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)

        sm.remap_indices([])

        assert sm.file_history["/tmp/a.txt"] == [r1, r2]
        assert sm.current_message_index == 1


# ═══════════════════════════════════════════════════════════════
# clear
# ═══════════════════════════════════════════════════════════════

class TestClear:
    """SandboxManager.clear"""

    def test_clear_empties_file_history(self):
        """clear 清空所有 file_history"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/b.txt", None, "data", 1)
        assert len(sm.file_history) == 2

        sm.clear()
        assert sm.file_history == {}

    def test_clear_empties_message_history(self):
        """clear 清空所有 message_history"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/b.txt", None, "data", 1)
        assert len(sm.message_history) == 2

        sm.clear()
        assert sm.message_history == {}

    def test_clear_resets_current_message_index(self):
        """clear 将 current_message_index 归零"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 5)
        assert sm.current_message_index == 5

        sm.clear()
        assert sm.current_message_index == 0


# ═══════════════════════════════════════════════════════════════
# get_stats
# ═══════════════════════════════════════════════════════════════

class TestStats:
    """SandboxManager.get_stats"""

    def test_stats_empty(self):
        """空管理器返回正确统计"""
        sm = SandboxManager()
        stats = sm.get_stats()
        assert stats["total_files"] == 0
        assert stats["total_records"] == 0
        assert stats["current_message_index"] == 0
        assert stats["max_history_per_file"] == 100

    def test_stats_after_records(self):
        """有记录时返回正确统计"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "v1", 0)
        sm.record_file_change("/tmp/a.txt", "v1", "v2", 1)
        sm.record_file_change("/tmp/b.txt", None, "data", 1)

        stats = sm.get_stats()
        assert stats["total_files"] == 2
        assert stats["total_records"] == 3
        assert stats["current_message_index"] == 1

    def test_stats_custom_max_history(self):
        """自定义 max_history_per_file 反映在统计中"""
        sm = SandboxManager(max_history_per_file=50)
        stats = sm.get_stats()
        assert stats["max_history_per_file"] == 50


# ═══════════════════════════════════════════════════════════════
# get_sandbox_info
# ═══════════════════════════════════════════════════════════════

class TestSandboxInfo:
    """SandboxManager.get_sandbox_info"""

    def test_sandbox_info_with_records(self):
        """有记录时返回正确信息"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "new content", 0, tool_name="write_file")
        sm.record_file_change("/tmp/b.txt", "old", None, 0, tool_name="delete_file")

        info = sm.get_sandbox_info(0)
        assert info["count"] == 2
        assert len(info["file_changes"]) == 2

        # 第一个文件：新建文件
        assert info["file_changes"][0]["file_path"] == "/tmp/a.txt"
        assert info["file_changes"][0]["change_type"] == "新建文件"

        # 第二个文件：删除文件
        assert info["file_changes"][1]["file_path"] == "/tmp/b.txt"
        assert info["file_changes"][1]["change_type"] == "删除文件"

    def test_sandbox_info_no_records(self):
        """无记录时返回空列表"""
        sm = SandboxManager()
        info = sm.get_sandbox_info(99)
        assert info == {"file_changes": [], "count": 0}

    def test_sandbox_info_contains_tool_name(self):
        """沙盒信息包含 tool_name"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "data", 0, tool_name="read_file")
        info = sm.get_sandbox_info(0)
        assert info["file_changes"][0]["tool_name"] == "read_file"

    def test_sandbox_info_contains_timestamp(self):
        """沙盒信息包含 timestamp"""
        sm = SandboxManager()
        sm.record_file_change("/tmp/a.txt", None, "data", 0)
        info = sm.get_sandbox_info(0)
        assert isinstance(info["file_changes"][0]["timestamp"], float)


# ═══════════════════════════════════════════════════════════════
# 全局函数
# ═══════════════════════════════════════════════════════════════

class TestGlobalFunctions:
    """get_sandbox_manager / set_sandbox_manager / create_sandbox_manager"""

    def setup_method(self):
        """每个测试前清理全局状态"""
        set_sandbox_manager(None)

    def test_get_returns_none_initially(self):
        """未设置时 get_sandbox_manager 返回 None"""
        assert get_sandbox_manager() is None

    def test_set_and_get(self):
        """set 后 get 返回同一实例"""
        sm = SandboxManager()
        set_sandbox_manager(sm)
        assert get_sandbox_manager() is sm

    def test_create_sandbox_manager(self):
        """create_sandbox_manager 创建并设置新实例"""
        sm = create_sandbox_manager(max_history_per_file=50)
        assert isinstance(sm, SandboxManager)
        assert sm.max_history_per_file == 50
        # 验证已设置为全局实例
        assert get_sandbox_manager() is sm

    def test_create_sandbox_manager_default(self):
        """create_sandbox_manager 使用默认参数"""
        sm = create_sandbox_manager()
        assert sm.max_history_per_file == 100

    def test_set_none_clears_global(self):
        """设置为 None 后 get 返回 None"""
        sm = SandboxManager()
        set_sandbox_manager(sm)
        assert get_sandbox_manager() is sm
        set_sandbox_manager(None)
        assert get_sandbox_manager() is None


# ═══════════════════════════════════════════════════════════════
# SandboxContext
# ═══════════════════════════════════════════════════════════════

class TestSandboxContext:
    """SandboxContext 上下文管理器"""

    def setup_method(self):
        """每个测试前清理线程局部状态"""
        clear_current_message_index()

    def test_context_sets_and_restores_index(self):
        """上下文管理器设置消息索引，退出时恢复"""
        set_current_message_index(10)
        with SandboxContext(5):
            assert get_current_message_index() == 5
        assert get_current_message_index() == 10

    def test_context_with_no_previous_index(self):
        """没有之前索引时，退出后清除"""
        with SandboxContext(3):
            assert get_current_message_index() == 3
        assert get_current_message_index() is None

    def test_context_nested(self):
        """嵌套上下文正确恢复"""
        set_current_message_index(0)
        with SandboxContext(1):
            assert get_current_message_index() == 1
            with SandboxContext(2):
                assert get_current_message_index() == 2
            assert get_current_message_index() == 1
        assert get_current_message_index() == 0

    def test_get_current_message_index_default(self):
        """未设置时 get_current_message_index 返回 None"""
        clear_current_message_index()
        assert get_current_message_index() is None

    def test_set_current_message_index(self):
        """set_current_message_index 正确设置"""
        set_current_message_index(42)
        assert get_current_message_index() == 42

    def test_clear_current_message_index(self):
        """clear_current_message_index 清除索引"""
        set_current_message_index(7)
        clear_current_message_index()
        assert get_current_message_index() is None

    def test_context_exit_with_exception_still_restores(self):
        """退出上下文时即使有异常也恢复索引"""
        set_current_message_index(99)
        try:
            with SandboxContext(50):
                assert get_current_message_index() == 50
                raise ValueError("test error")
        except ValueError:
            pass
        assert get_current_message_index() == 99


# ═══════════════════════════════════════════════════════════════
# record_file_change_from_context
# ═══════════════════════════════════════════════════════════════

class TestRecordFromContext:
    """record_file_change_from_context"""

    def setup_method(self):
        """每个测试前清理全局状态"""
        set_sandbox_manager(None)
        clear_current_message_index()

    def test_record_from_context(self, tmp_path):
        """使用上下文记录文件修改"""
        sm = SandboxManager()
        set_sandbox_manager(sm)
        file_path = str(tmp_path / "context_test.txt")

        with SandboxContext(0):
            record = record_file_change_from_context(
                file_path, None, "context_data"
            )

        assert record is not None
        assert record.file_path == file_path
        assert record.content_after == "context_data"
        assert record.message_index == 0
        # 验证已记录到管理器
        assert file_path in sm.file_history

    def test_record_from_context_no_manager(self, tmp_path):
        """没有全局管理器时返回 None"""
        clear_current_message_index()
        # 确保没有设置全局管理器
        set_sandbox_manager(None)
        record = record_file_change_from_context(
            str(tmp_path / "no_mgr.txt"), None, "data"
        )
        assert record is None

    def test_record_from_context_no_message_index(self, tmp_path):
        """没有消息索引时使用管理器的 current_message_index"""
        sm = SandboxManager()
        sm.current_message_index = 7
        set_sandbox_manager(sm)
        file_path = str(tmp_path / "fallback_index.txt")

        # 不在 SandboxContext 中，但管理器有 current_message_index
        record = record_file_change_from_context(
            file_path, None, "fallback_data"
        )
        assert record is not None
        assert record.message_index == 7

    def test_record_from_context_with_explicit_tool_name(self, tmp_path):
        """记录时传入自定义 tool_name"""
        sm = SandboxManager()
        set_sandbox_manager(sm)
        file_path = str(tmp_path / "tool_name_test.txt")

        with SandboxContext(0):
            record = record_file_change_from_context(
                file_path, None, "data", tool_name="custom_tool"
            )

        assert record is not None
        assert record.tool_name == "custom_tool"

    def test_record_from_context_async(self, tmp_path):
        """异步版本 async_record_file_change_from_context

        注意：async_record_file_change_from_context 通过 asyncio.to_thread
        在独立线程中运行，SandboxContext 的线程局部变量不会传递到该线程。
        因此依赖管理器的 current_message_index 作为回退。
        """
        sm = SandboxManager()
        sm.current_message_index = 5
        set_sandbox_manager(sm)
        file_path = str(tmp_path / "async_context.txt")

        import asyncio
        from src.core.sandbox_manager import async_record_file_change_from_context

        async def do_record():
            return await async_record_file_change_from_context(
                file_path, None, "async_context_data"
            )

        record = asyncio.run(do_record())
        assert record is not None
        assert record.content_after == "async_context_data"
        # 在线程中获取不到 SandboxContext 的索引，回退到管理器的 current_message_index
        assert record.message_index == 5


# ═══════════════════════════════════════════════════════════════
# 目录操作测试
# ═══════════════════════════════════════════════════════════════

class TestDirectoryRecord:
    """FileChangeRecord 目录类型"""

    def test_directory_record_type(self):
        """record_type="directory" 的记录"""
        record = FileChangeRecord(
            "/tmp/mydir", None, "", 0, record_type="directory"
        )
        assert record.record_type == "directory"
        assert record.get_change_type() == "新建目录"

    def test_directory_delete_type(self):
        """删除目录的记录类型"""
        record = FileChangeRecord(
            "/tmp/mydir", "", None, 0, record_type="directory"
        )
        assert record.record_type == "directory"
        assert record.get_change_type() == "删除目录"

    def test_default_record_type_is_file(self):
        """默认 record_type 为 'file'"""
        record = FileChangeRecord("/tmp/f.txt", None, "data", 0)
        assert record.record_type == "file"
        assert record.get_change_type() == "新建文件"


class TestDirectoryApplyRevert:
    """目录的 apply / revert"""

    def test_apply_create_directory(self, tmp_path):
        """apply 创建目录"""
        dir_path = str(tmp_path / "newdir")
        record = FileChangeRecord(dir_path, None, "", 0, record_type="directory")
        success = record.apply()
        assert success is True
        assert os.path.isdir(dir_path)

    def test_apply_delete_directory(self, tmp_path):
        """apply 删除目录（content_after=None）"""
        dir_path = str(tmp_path / "deleteme")
        os.makedirs(dir_path)
        record = FileChangeRecord(dir_path, "", None, 0, record_type="directory")
        success = record.apply()
        assert success is True
        assert not os.path.exists(dir_path)

    def test_revert_create_directory(self, tmp_path):
        """revert 回滚目录创建（恢复为不存在）"""
        dir_path = str(tmp_path / "revertdir")
        record = FileChangeRecord(dir_path, None, "", 0, record_type="directory")
        record.apply()
        assert os.path.isdir(dir_path)
        success = record.revert()
        assert success is True
        assert not os.path.exists(dir_path)

    def test_revert_delete_directory(self, tmp_path):
        """revert 回滚目录删除（恢复目录）"""
        dir_path = str(tmp_path / "restoredir")
        os.makedirs(dir_path)
        record = FileChangeRecord(dir_path, "", None, 0, record_type="directory")
        record.apply()  # 删除目录
        assert not os.path.exists(dir_path)
        success = record.revert()  # 恢复目录
        assert success is True
        assert os.path.isdir(dir_path)

    def test_directory_with_files_rmtree(self, tmp_path):
        """删除包含文件的目录时使用 rmtree"""
        dir_path = str(tmp_path / "full_dir")
        os.makedirs(dir_path)
        file_path = os.path.join(dir_path, "inner.txt")
        with open(file_path, "w") as f:
            f.write("inside")
        record = FileChangeRecord(dir_path, "", None, 0, record_type="directory")
        success = record.apply()
        assert success is True
        assert not os.path.exists(dir_path)


class TestDirectoryRestore:
    """SandboxManager 目录恢复"""

    def test_mk_restore_forward(self, tmp_path):
        """mkdir(idx=1) + file_write(idx=2)，恢复到 idx=1：目录应存在且文件为原始状态"""
        dir_path = str(tmp_path / "mk_forward")
        file_path = os.path.join(dir_path, "data.txt")
        sm = SandboxManager()
        sm.record_file_change(dir_path, None, "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(file_path, "old", "new", 2)

        # 创建目录和文件以模拟已发生的操作
        os.makedirs(dir_path)
        with open(file_path, "w") as f:
            f.write("new")

        results = sm.restore_to_message(1)
        assert results[file_path] is True  # 文件回滚到 "old"
        assert os.path.isdir(dir_path)
        with open(file_path) as f:
            assert f.read() == "old"

    def test_mk_restore_backward(self, tmp_path):
        """mkdir(idx=1)，恢复到 idx=0：目录应被删除"""
        dir_path = str(tmp_path / "mk_backward")
        os.makedirs(dir_path)  # 目录已存在（模拟 mk 已执行）
        sm = SandboxManager()
        sm.record_file_change(dir_path, None, "", 1, tool_name="mk", record_type="directory")

        results = sm.restore_to_message(0)
        assert results[dir_path] is True
        assert not os.path.exists(dir_path)

    def test_rm_dir_restore_backward(self, tmp_path):
        """rm -r(idx=2) 在 mk(idx=1) 之后，恢复到 idx=1：目录应被恢复"""
        dir_path = str(tmp_path / "rm_backward")
        sm = SandboxManager()
        sm.record_file_change(dir_path, None, "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(dir_path, "", None, 2, tool_name="rm", record_type="directory")

        results = sm.restore_to_message(1)
        assert results[dir_path] is True
        assert os.path.isdir(dir_path)

    def test_rm_dir_restore_to_zero(self, tmp_path):
        """rm -r(idx=1)，恢复到 idx=0：目录应被恢复（rm 前目录存在）"""
        dir_path = str(tmp_path / "rm_to_zero")
        sm = SandboxManager()
        sm.record_file_change(dir_path, "", None, 1, tool_name="rm", record_type="directory")

        results = sm.restore_to_message(0)
        assert results[dir_path] is True
        assert os.path.isdir(dir_path)

    def test_mk_then_rm_restore_to_zero(self, tmp_path):
        """mkdir(idx=1) → rm -r(idx=2)，恢复到 idx=0：目录应不存在"""
        dir_path = str(tmp_path / "mk_rm_zero")
        sm = SandboxManager()
        sm.record_file_change(dir_path, None, "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(dir_path, "", None, 2, tool_name="rm", record_type="directory")

        results = sm.restore_to_message(0)
        assert results[dir_path] is True
        assert not os.path.exists(dir_path)

    def test_file_and_directory_independent(self, tmp_path):
        """文件和目录记录互不干扰"""
        file_path = str(tmp_path / "data.txt")
        dir_path = str(tmp_path / "data_dir")
        sm = SandboxManager()
        sm.record_file_change(file_path, None, "hello", 1, record_type="file")
        sm.record_file_change(dir_path, None, "", 1, tool_name="mk", record_type="directory")
        # 添加 idx=2 的修改使 idx=1 的状态成为恢复目标
        sm.record_file_change(file_path, "hello", "world", 2)
        sm.record_file_change(dir_path, "", None, 2, tool_name="rm", record_type="directory")

        # 创建并准备当前状态
        os.makedirs(dir_path)
        with open(file_path, "w") as f:
            f.write("world")
        os.rmdir(dir_path)  # 模拟 rm 后目录不存在

        results = sm.restore_to_message(1)
        assert results[file_path] is True
        assert results[dir_path] is True
        assert os.path.isfile(file_path)
        assert os.path.isdir(dir_path)
        with open(file_path) as f:
            assert f.read() == "hello"

    def test_record_type_preserved(self, tmp_path):
        """沙盒记录的类型被保留"""
        dir_path = str(tmp_path / "type_test")
        sm = SandboxManager()
        record = sm.record_file_change(
            dir_path, None, "", 1, tool_name="mk", record_type="directory"
        )
        assert record.record_type == "directory"
        assert sm._get_record_type_at_message(dir_path, 1) == "directory"

    # ═══════════════════════════════════════════════════════════════
    # Bug 回归测试：restore_to_message 多文件还原
    # ═══════════════════════════════════════════════════════════════

    def test_restore_dir_to_file_type_change(self, tmp_path):
        """Bug1 回归：路径从目录变为文件时 restore 不因 EISDIR 失败

        场景：
          idx=0: mk /tmp/x         → 目录
          idx=1: rm /tmp/x + mk /tmp/x → 目录改为文件 + 重新建目录（含 data.txt）
        恢复到 idx=0：/tmp/x 应从目录变回文件 "file_content"
        """
        path = str(tmp_path / "x")
        inner = os.path.join(path, "data.txt")

        sm = SandboxManager()
        # idx=0：文件创建
        sm.record_file_change(path, None, "file_content", 0, tool_name="write_file", record_type="file")
        # idx=1：目录创建（含内部文件）
        sm.record_file_change(path, "file_content", "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(inner, None, "inner_data", 1, tool_name="write_file")

        # 模拟 idx=1 操作后的磁盘状态：/tmp/x 是目录，里面有 data.txt
        os.makedirs(path, exist_ok=True)
        with open(inner, "w") as f:
            f.write("inner_data")

        # 恢复到 idx=0 — /tmp/x 应为文件，data.txt 应不存在
        results = sm.restore_to_message(0)
        assert results.get(path, False) is True, f"恢复文件失败: {path}"
        # /tmp/x 应该是文件
        assert os.path.isfile(path), f"预期 {path} 是文件，但当前不存在或是目录"
        with open(path) as f:
            assert f.read() == "file_content"
        # data.txt 应不存在（在 idx=0 时不存在）
        assert not os.path.exists(inner), f"预期 {inner} 不存在"

    def test_resotre_file_to_dir_type_change(self, tmp_path):
        """Bug1 回归：路径从文件变为目录时 restore 不失败

        场景：
          idx=0: write_file /tmp/y → 文件 "content"
          idx=1: rm /tmp/y + mk /tmp/y → 目录（含内部文件）
          idx=2: 修改内部文件
        恢复到 idx=1：/tmp/y 应为目录，含内部文件（idx=1 时的状态）

        注：/tmp/y 自身在 idx=1 后无记录，不受 affected_files_set 影响，
        但 inner.txt 在 idx=2 有记录，恢复时 implicit 依赖 /tmp/y 目录存在。
        """
        path = str(tmp_path / "y")
        inner = os.path.join(path, "inner.txt")

        sm = SandboxManager()
        # idx=0：文件
        sm.record_file_change(path, None, "file_content", 0, tool_name="write_file")
        # idx=1：目录
        sm.record_file_change(path, "file_content", "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(inner, None, "inner_data", 1, tool_name="write_file")
        # idx=2：修改内部文件（使 idx=1 成为恢复目标，inner.txt 进入 affected_files_set）
        sm.record_file_change(inner, "inner_data", "modified", 2, tool_name="write_file")

        # 模拟 idx=2 操作后的磁盘状态
        os.makedirs(path, exist_ok=True)
        with open(inner, "w") as f:
            f.write("modified")

        # 恢复到 idx=1 — inner.txt 应回到 "inner_data"，/tmp/y 仍是目录（未受影响）
        results = sm.restore_to_message(1)
        # inner.txt 在 affected_files_set 中，应被恢复
        assert results.get(inner, False) is True
        # /tmp/y 自身无 idx>1 的记录，不在 affected_files_set 中，但磁盘状态应保持不变
        assert os.path.isdir(path), f"预期 {path} 是目录"
        assert os.path.isfile(inner), f"预期 {inner} 是文件"
        with open(inner) as f:
            assert f.read() == "inner_data"

    def test_restore_multi_files_one_fails_not_block_others(self, tmp_path):
        """Bug 回归：多个文件恢复时，一个失败不影响其他

        场景：3 个独立文件，其中一个的 target 路径是目录（模拟异常），
        其他 2 个应正常恢复。
        """
        file_a = str(tmp_path / "a.txt")
        file_b = str(tmp_path / "b.txt")
        dir_c = str(tmp_path / "c_dir")

        sm = SandboxManager()
        sm.record_file_change(file_a, None, "content_a", 1)
        sm.record_file_change(file_b, None, "content_b", 1)
        sm.record_file_change(dir_c, None, "", 1, tool_name="mk", record_type="directory")
        # 在 idx=2 修改三者，使 idx=1 成为恢复目标
        sm.record_file_change(file_a, "content_a", "modified_a", 2)
        sm.record_file_change(file_b, "content_b", "modified_b", 2)
        sm.record_file_change(dir_c, "", None, 2, tool_name="rm", record_type="directory")

        # 磁盘状态：idx=2 后的状态
        with open(file_a, "w") as f:
            f.write("modified_a")
        with open(file_b, "w") as f:
            f.write("modified_b")
        # dir_c 已被删除（rmtree）

        results = sm.restore_to_message(1)
        assert results.get(file_a, False) is True, f"file_a 应恢复成功"
        assert results.get(file_b, False) is True, f"file_b 应恢复成功"
        assert results.get(dir_c, False) is True, f"dir_c 应恢复成功"

        # 验证实际状态
        with open(file_a) as f:
            assert f.read() == "content_a"
        with open(file_b) as f:
            assert f.read() == "content_b"
        assert os.path.isdir(dir_c)

    def test_restore_mixed_type_multi_files(self, tmp_path):
        """混合文件/目录类型的多文件批量恢复

        场景：
          idx=0: mk /tmp/sub/ + write_file /tmp/sub/f1.txt
          idx=1: write_file /tmp/sub/f2.txt + rm /tmp/sub/f1.txt
          idx=2: write_file /tmp/sub/f1.txt（重新创建）+ 改 f2
        恢复到 idx=1：
          /tmp/sub/f1.txt 不存在
          /tmp/sub/f2.txt 存在且内容为 "f2_v1"
        """
        sub = str(tmp_path / "sub")
        f1 = os.path.join(sub, "f1.txt")
        f2 = os.path.join(sub, "f2.txt")

        sm = SandboxManager()
        # idx=0：目录 + f1
        sm.record_file_change(sub, None, "", 0, tool_name="mk", record_type="directory")
        sm.record_file_change(f1, None, "f1_v1", 0)
        # idx=1：写 f2，删 f1
        sm.record_file_change(f2, None, "f2_v1", 1)
        sm.record_file_change(f1, "f1_v1", None, 1)
        # idx=2：重新创建 f1，改 f2（使 idx=1 成为恢复目标，两个文件都进入 affected_files_set）
        sm.record_file_change(f1, None, "f1_v2", 2)
        sm.record_file_change(f2, "f2_v1", "f2_modified", 2)

        # 磁盘状态：idx=2 后的状态
        os.makedirs(sub, exist_ok=True)
        with open(f1, "w") as f:
            f.write("f1_v2")
        with open(f2, "w") as f:
            f.write("f2_modified")

        # 恢复到 idx=1
        results = sm.restore_to_message(1)
        assert results.get(f1, False) is True, f"f1 应恢复（删除）"
        assert results.get(f2, False) is True, f"f2 应恢复"
        # sub 目录自身无 idx>1 的记录，不在 affected_files_set 中，磁盘状态应不变
        assert os.path.isdir(sub), "sub 目录应存在"

        assert not os.path.exists(f1), "f1 应在 idx=1 时不存在"
        assert os.path.isfile(f2), "f2 应在 idx=1 时存在"
        with open(f2) as f:
            assert f.read() == "f2_v1"
        assert os.path.isdir(sub), "sub 目录应存在"

    def test_restore_to_message_nonexistent_target_cleans_all(self, tmp_path):
        """恢复到所有记录之前的索引 → 清空所有文件变更"""
        file_a = str(tmp_path / "na.txt")
        file_b = str(tmp_path / "nb.txt")
        sub = str(tmp_path / "nsub")

        sm = SandboxManager()
        sm.record_file_change(file_a, None, "a", 0)
        sm.record_file_change(sub, None, "", 1, tool_name="mk", record_type="directory")
        sm.record_file_change(file_b, None, "b", 2)

        # 创建磁盘状态
        with open(file_a, "w") as f:
            f.write("a")
        os.makedirs(sub)
        with open(file_b, "w") as f:
            f.write("b")

        # 恢复到 -1（所有记录之前）
        results = sm.restore_to_message(-1)
        assert results.get(file_a, False) is True
        assert results.get(file_b, False) is True
        assert results.get(sub, False) is True

        assert not os.path.exists(file_a)
        assert not os.path.exists(file_b)
        assert not os.path.exists(sub)
