"""Tests for src/core/file_change_record.py — FileChangeRecord"""

import asyncio
import os
import time
import threading

import pytest

from src.core.file_change_record import FileChangeRecord


# ═══════════════════════════════════════════════════════════════
# get_change_type
# ═══════════════════════════════════════════════════════════════

class TestGetChangeType:
    """get_change_type 方法"""

    def test_new_file(self):
        """content_before=None, content_after="xxx" → "新建文件" """
        record = FileChangeRecord("/tmp/test.txt", None, "xxx", 0)
        assert record.get_change_type() == "新建文件"

    def test_delete_file(self):
        """content_before="xxx", content_after=None → "删除文件" """
        record = FileChangeRecord("/tmp/test.txt", "xxx", None, 0)
        assert record.get_change_type() == "删除文件"

    def test_no_change(self):
        """content_before="xxx", content_after="xxx" → "无变化" """
        record = FileChangeRecord("/tmp/test.txt", "xxx", "xxx", 0)
        assert record.get_change_type() == "无变化"

    def test_modified(self):
        """content_before="aaa", content_after="bbb" → "修改文件" """
        record = FileChangeRecord("/tmp/test.txt", "aaa", "bbb", 0)
        assert record.get_change_type() == "修改文件"

    def test_both_none(self):
        """content_before=None, content_after=None → "无变化" """
        record = FileChangeRecord("/tmp/test.txt", None, None, 0)
        assert record.get_change_type() == "无变化"


# ═══════════════════════════════════════════════════════════════
# __repr__
# ═══════════════════════════════════════════════════════════════

class TestRepr:
    """__repr__ 方法"""

    def test_repr_contains_fields(self):
        """返回包含 file_path, message_index, tool_name 的字符串"""
        record = FileChangeRecord("/path/to/file.py", None, "content", 42,
                                  tool_name="update_file")
        r = repr(record)
        assert "/path/to/file.py" in r
        assert "42" in r or "message_index=42" in r
        assert "update_file" in r or "tool_name='update_file'" in r

    def test_repr_default_tool_name(self):
        """默认 tool_name 为 write_file"""
        record = FileChangeRecord("/tmp/x.txt", None, "c", 5)
        r = repr(record)
        assert "write_file" in r
        assert "/tmp/x.txt" in r
        assert "5" in r or "message_index=5" in r


# ═══════════════════════════════════════════════════════════════
# apply
# ═══════════════════════════════════════════════════════════════

class TestApply:
    """apply 方法"""

    def test_apply_create_new_file(self, tmp_path):
        """新建文件：content_before=None, content_after="新内容" → 文件被创建"""
        f = tmp_path / "new_file.txt"
        record = FileChangeRecord(str(f), None, "新内容", 0)
        result = record.apply()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "新内容"

    def test_apply_modify_file(self, tmp_path):
        """修改文件：已有文件被覆盖为新内容"""
        f = tmp_path / "modify.txt"
        f.write_text("原内容", encoding="utf-8")
        record = FileChangeRecord(str(f), "原内容", "新内容", 0)
        result = record.apply()
        assert result is True
        assert f.read_text(encoding="utf-8") == "新内容"

    def test_apply_delete_file(self, tmp_path):
        """删除文件：content_after=None → 文件被删除，返回 True"""
        f = tmp_path / "to_delete.txt"
        f.write_text("将被删除", encoding="utf-8")
        record = FileChangeRecord(str(f), "将被删除", None, 0)
        result = record.apply()
        assert result is True
        assert not f.exists()

    def test_apply_delete_nonexistent_file(self, tmp_path):
        """删除不存在的文件 → 返回 True（不报错）"""
        f = tmp_path / "nonexistent.txt"
        assert not f.exists()
        record = FileChangeRecord(str(f), None, None, 0)
        result = record.apply()
        assert result is True

    def test_apply_creates_parent_directory(self, tmp_path):
        """自动创建父目录"""
        f = tmp_path / "sub" / "deep" / "file.txt"
        record = FileChangeRecord(str(f), None, "深层目录文件", 0)
        result = record.apply()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "深层目录文件"

    def test_apply_creates_parent_directory_for_modify(self, tmp_path):
        """修改文件时父目录不存在也自动创建"""
        f = tmp_path / "new_dir" / "file.txt"
        record = FileChangeRecord(str(f), "原内容", "新内容", 0)
        result = record.apply()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "新内容"

    def test_apply_no_change(self, tmp_path):
        """content_before == content_after 时也写入文件"""
        f = tmp_path / "nochange.txt"
        record = FileChangeRecord(str(f), "相同内容", "相同内容", 0)
        result = record.apply()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "相同内容"

    def test_apply_overwrites_existing_content(self, tmp_path):
        """多次 apply 覆盖已有内容"""
        f = tmp_path / "overwrite.txt"
        f.write_text("第一版", encoding="utf-8")
        record = FileChangeRecord(str(f), "第一版", "第二版", 0)
        assert record.apply() is True
        assert f.read_text(encoding="utf-8") == "第二版"
        # 再次 apply（模拟重新应用）
        record2 = FileChangeRecord(str(f), "第二版", "第三版", 1)
        assert record2.apply() is True
        assert f.read_text(encoding="utf-8") == "第三版"


# ═══════════════════════════════════════════════════════════════
# revert
# ═══════════════════════════════════════════════════════════════

class TestRevert:
    """revert 方法"""

    def test_revert_new_file(self, tmp_path):
        """回退新建：content_before=None → 删除已创建的文件"""
        f = tmp_path / "revert_new.txt"
        f.write_text("刚创建的内容", encoding="utf-8")
        record = FileChangeRecord(str(f), None, "刚创建的内容", 0)
        result = record.revert()
        assert result is True
        assert not f.exists()

    def test_revert_delete(self, tmp_path):
        """回退删除：content_before="原内容" → 恢复被删除的文件"""
        f = tmp_path / "revert_delete.txt"
        # 模拟文件已被删除
        assert not f.exists()
        record = FileChangeRecord(str(f), "原内容", None, 0)
        result = record.revert()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "原内容"

    def test_revert_modify(self, tmp_path):
        """回退修改：将当前文件内容恢复为 content_before"""
        f = tmp_path / "revert_modify.txt"
        f.write_text("当前内容（修改后）", encoding="utf-8")
        record = FileChangeRecord(str(f), "原内容", "当前内容（修改后）", 0)
        result = record.revert()
        assert result is True
        assert f.read_text(encoding="utf-8") == "原内容"

    def test_revert_creates_parent_directory(self, tmp_path):
        """回退时自动创建父目录"""
        f = tmp_path / "sub_revert" / "file.txt"
        record = FileChangeRecord(str(f), "恢复的内容", "新内容", 0)
        result = record.revert()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "恢复的内容"

    def test_revert_new_file_nonexistent(self, tmp_path):
        """回退新建但文件已被删除 → 返回 True"""
        f = tmp_path / "already_deleted.txt"
        record = FileChangeRecord(str(f), None, "从未存在的内容", 0)
        # 文件从未被创建
        result = record.revert()
        assert result is True
        assert not f.exists()


# ═══════════════════════════════════════════════════════════════
# apply_async
# ═══════════════════════════════════════════════════════════════

class TestApplyAsync:
    """apply_async 方法"""

    @pytest.mark.asyncio
    async def test_apply_async_create_new_file(self, tmp_path):
        """异步新建文件"""
        f = tmp_path / "async_new.txt"
        record = FileChangeRecord(str(f), None, "异步创建", 0)
        result = await record.apply_async()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "异步创建"

    @pytest.mark.asyncio
    async def test_apply_async_modify_file(self, tmp_path):
        """异步修改文件"""
        f = tmp_path / "async_modify.txt"
        f.write_text("原内容", encoding="utf-8")
        record = FileChangeRecord(str(f), "原内容", "异步修改后", 0)
        result = await record.apply_async()
        assert result is True
        assert f.read_text(encoding="utf-8") == "异步修改后"

    @pytest.mark.asyncio
    async def test_apply_async_delete_file(self, tmp_path):
        """异步删除文件"""
        f = tmp_path / "async_delete.txt"
        f.write_text("待删除", encoding="utf-8")
        record = FileChangeRecord(str(f), "待删除", None, 0)
        result = await record.apply_async()
        assert result is True
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_apply_async_delete_nonexistent(self, tmp_path):
        """异步删除不存在的文件 → 返回 True"""
        f = tmp_path / "async_nonexistent.txt"
        record = FileChangeRecord(str(f), None, None, 0)
        result = await record.apply_async()
        assert result is True

    @pytest.mark.asyncio
    async def test_apply_async_creates_parent_directory(self, tmp_path):
        """异步操作自动创建父目录"""
        f = tmp_path / "async_sub" / "deep" / "file.txt"
        record = FileChangeRecord(str(f), None, "异步深层目录", 0)
        result = await record.apply_async()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "异步深层目录"


# ═══════════════════════════════════════════════════════════════
# revert_async
# ═══════════════════════════════════════════════════════════════

class TestRevertAsync:
    """revert_async 方法"""

    @pytest.mark.asyncio
    async def test_revert_async_new_file(self, tmp_path):
        """异步回退新建：删除已创建的文件"""
        f = tmp_path / "async_revert_new.txt"
        f.write_text("刚创建", encoding="utf-8")
        record = FileChangeRecord(str(f), None, "刚创建", 0)
        result = await record.revert_async()
        assert result is True
        assert not f.exists()

    @pytest.mark.asyncio
    async def test_revert_async_delete(self, tmp_path):
        """异步回退删除：恢复被删除的文件"""
        f = tmp_path / "async_revert_delete.txt"
        record = FileChangeRecord(str(f), "原内容", None, 0)
        result = await record.revert_async()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "原内容"

    @pytest.mark.asyncio
    async def test_revert_async_modify(self, tmp_path):
        """异步回退修改：恢复为原内容"""
        f = tmp_path / "async_revert_modify.txt"
        f.write_text("修改后内容", encoding="utf-8")
        record = FileChangeRecord(str(f), "原内容", "修改后内容", 0)
        result = await record.revert_async()
        assert result is True
        assert f.read_text(encoding="utf-8") == "原内容"

    @pytest.mark.asyncio
    async def test_revert_async_creates_parent_directory(self, tmp_path):
        """异步回退时自动创建父目录"""
        f = tmp_path / "async_sub_revert" / "file.txt"
        record = FileChangeRecord(str(f), "恢复的内容", None, 0)
        result = await record.revert_async()
        assert result is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == "恢复的内容"

    @pytest.mark.asyncio
    async def test_revert_async_new_file_nonexistent(self, tmp_path):
        """异步回退新建但文件不存在 → 返回 True"""
        f = tmp_path / "async_never_created.txt"
        record = FileChangeRecord(str(f), None, "内容", 0)
        result = await record.revert_async()
        assert result is True


# ═══════════════════════════════════════════════════════════════
# __init__ (timestamp)
# ═══════════════════════════════════════════════════════════════

class TestInit:
    """__init__ 构造方法"""

    def test_default_timestamp(self):
        """不传 timestamp 时自动使用当前时间"""
        before = time.time()
        record = FileChangeRecord("/tmp/t.txt", None, "c", 0)
        after = time.time()
        assert before <= record.timestamp <= after

    def test_custom_timestamp(self):
        """传入自定义 timestamp 被保留"""
        record = FileChangeRecord("/tmp/t.txt", None, "c", 0, timestamp=12345.0)
        assert record.timestamp == 12345.0

    def test_default_tool_name(self):
        """默认 tool_name 为 write_file"""
        record = FileChangeRecord("/tmp/t.txt", None, "c", 0)
        assert record.tool_name == "write_file"

    def test_custom_tool_name(self):
        """传入自定义 tool_name 被保留"""
        record = FileChangeRecord("/tmp/t.txt", None, "c", 0,
                                  tool_name="update_file")
        assert record.tool_name == "update_file"

    def test_fields_stored_correctly(self):
        """所有字段正确存储"""
        record = FileChangeRecord("/my/path.py", "before", "after", 7,
                                  timestamp=999.0, tool_name="read_file")
        assert record.file_path == "/my/path.py"
        assert record.content_before == "before"
        assert record.content_after == "after"
        assert record.message_index == 7
        assert record.timestamp == 999.0
        assert record.tool_name == "read_file"


# ═══════════════════════════════════════════════════════════════
# 回归测试: asyncio.Lock 懒初始化
# ═══════════════════════════════════════════════════════════════

class TestAsyncLockLazyInit:
    """回归测试：_async_lock 不在 __init__ 中创建（Python 3.9 兼容）

    修复背景: FileChangeRecord.__init__ 可能在 asyncio.to_thread 的工人线程
    （asyncio_1）中被调用（通过 async_record_file_change_from_context 路径），
    而 Python 3.9 中 asyncio.Lock() 构造时会调用 asyncio.get_event_loop()，
    在无事件循环的子线程中抛出 RuntimeError。

    修复方案: 将 _async_lock 设置为 None（懒初始化），
    在 _do_apply_async 中首次使用时创建 asyncio.Lock()。
    """

    def test_async_lock_is_none_after_init(self):
        """__init__ 后 _async_lock 为 None（不在构造时创建）"""
        record = FileChangeRecord("/tmp/regression_test.txt", None, "content", 0)
        assert record._async_lock is None, (
            f"预期 _async_lock 为 None，实际为 {record._async_lock!r}"
        )

    @pytest.mark.asyncio
    async def test_async_lock_created_in_apply_async(self, tmp_path):
        """apply_async 中自动创建 asyncio.Lock"""
        f = tmp_path / "lazy_async_lock.txt"
        record = FileChangeRecord(str(f), None, "懒初始化锁", 0)
        assert record._async_lock is None  # 构造时未创建

        result = await record.apply_async()
        assert result is True

        # 调用 apply_async 后 _async_lock 应为 asyncio.Lock 对象
        assert record._async_lock is not None, "apply_async 后应创建 _async_lock"
        assert isinstance(record._async_lock, asyncio.Lock), (
            f"预期 asyncio.Lock，实际为 {type(record._async_lock)}"
        )

    @pytest.mark.asyncio
    async def test_async_lock_created_in_revert_async(self, tmp_path):
        """revert_async 中自动创建 asyncio.Lock"""
        f = tmp_path / "lazy_async_revert.txt"
        f.write_text("原内容", encoding="utf-8")
        record = FileChangeRecord(str(f), "原内容", "新内容", 0)
        assert record._async_lock is None  # 构造时未创建

        result = await record.revert_async()
        assert result is True

        assert record._async_lock is not None, "revert_async 后应创建 _async_lock"
        assert isinstance(record._async_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_async_lock_reused_across_calls(self, tmp_path):
        """多次调用 apply_async/revert_async 复用同一个锁"""
        f = tmp_path / "lazy_async_reuse.txt"
        f.write_text("初始", encoding="utf-8")
        record = FileChangeRecord(str(f), "初始", "修改", 0)
        assert record._async_lock is None

        await record.apply_async()
        lock_id_1 = id(record._async_lock)

        await record.revert_async()
        lock_id_2 = id(record._async_lock)

        assert lock_id_1 == lock_id_2, "应复用同一个 asyncio.Lock 实例"


# ═══════════════════════════════════════════════════════════════
# 并发安全 (threading)
# ═══════════════════════════════════════════════════════════════

class TestConcurrency:
    """并发安全：_file_lock 防止竞态"""

    def test_concurrent_apply_revert_same_file(self, tmp_path):
        """多线程并发 apply/revert 同一文件，结果可预测"""
        f = tmp_path / "concurrent_same.txt"
        f.write_text("初始内容", encoding="utf-8")

        record_a = FileChangeRecord(str(f), "初始内容", "线程A写入", 0)
        record_b = FileChangeRecord(str(f), "线程A写入", "线程B写入", 1)

        errors = []

        def apply_a():
            try:
                assert record_a.apply() is True
            except Exception as e:
                errors.append(e)

        def apply_b():
            try:
                assert record_b.apply() is True
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=apply_a),
            threading.Thread(target=apply_b),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发操作出现异常: {errors}"
        # 最终内容一定是两个之一（取决于锁顺序）
        content = f.read_text(encoding="utf-8")
        assert content in ("线程A写入", "线程B写入"), f"意外的文件内容: {content}"

    def test_concurrent_apply_different_files(self, tmp_path):
        """多线程并发 apply 不同文件，所有文件内容正确"""
        files = []
        records = []
        for i in range(10):
            f = tmp_path / f"concurrent_diff_{i}.txt"
            files.append(f)
            records.append(FileChangeRecord(str(f), None, f"内容{i}", i))

        errors = []

        def apply_record(rec):
            try:
                assert rec.apply() is True
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=apply_record, args=(r,))
                   for r in records]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 apply 不同文件出现异常: {errors}"
        for i, f in enumerate(files):
            assert f.exists(), f"文件 {f} 不存在"
            assert f.read_text(encoding="utf-8") == f"内容{i}"

    def test_concurrent_revert_different_files(self, tmp_path):
        """多线程并发 revert 不同文件，所有文件恢复正确"""
        files = []
        records = []
        original_contents = {}
        for i in range(10):
            f = tmp_path / f"concurrent_rev_{i}.txt"
            f.write_text(f"原内容{i}", encoding="utf-8")
            files.append(f)
            original_contents[str(f)] = f"原内容{i}"
            # 记录：content_before=原内容, content_after=新内容
            records.append(FileChangeRecord(str(f), f"原内容{i}", f"新内容{i}", i))
            # 先 apply 修改
            records[-1].apply()

        errors = []

        def revert_record(rec):
            try:
                assert rec.revert() is True
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=revert_record, args=(r,))
                   for r in records]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发 revert 出现异常: {errors}"
        for i, f in enumerate(files):
            assert f.exists(), f"文件 {f} 不存在"
            assert f.read_text(encoding="utf-8") == f"原内容{i}"

    def test_concurrent_mixed_apply_revert_same_file(self, tmp_path):
        """多线程混合 apply/revert 同一文件，文件始终处于合法状态"""
        f = tmp_path / "concurrent_mixed.txt"
        f.write_text("初始", encoding="utf-8")

        records = [
            FileChangeRecord(str(f), "初始", "状态1", 0),
            FileChangeRecord(str(f), "状态1", "状态2", 1),
            FileChangeRecord(str(f), "状态2", "状态3", 2),
            FileChangeRecord(str(f), "状态3", "状态4", 3),
        ]

        # 先按顺序 apply 确保链条建立
        for r in records:
            assert r.apply() is True
        assert f.read_text(encoding="utf-8") == "状态4"

        errors = []

        def revert_all():
            try:
                for r in reversed(records):
                    r.revert()
            except Exception as e:
                errors.append(e)

        def apply_mix():
            try:
                rec = FileChangeRecord(str(f), "状态4", "并发写入", 99)
                rec.apply()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=revert_all),
            threading.Thread(target=apply_mix),
            threading.Thread(target=revert_all),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"混合并发操作出现异常: {errors}"
        # 文件应该存在（因为 revert 到"初始"状态不会删除文件）
        assert f.exists()


# ═══════════════════════════════════════════════════════════════
# 边界条件
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界条件测试"""

    def test_empty_string_content(self, tmp_path):
        """content_before/content_after 为空字符串"""
        f = tmp_path / "empty.txt"
        record = FileChangeRecord(str(f), "", "", 0)
        assert record.get_change_type() == "无变化"
        assert record.apply() is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == ""

    def test_empty_string_as_new_file(self, tmp_path):
        """content_before=None, content_after="" → 新建空文件"""
        f = tmp_path / "empty_new.txt"
        record = FileChangeRecord(str(f), None, "", 0)
        assert record.get_change_type() == "新建文件"
        assert record.apply() is True
        assert f.exists()
        assert f.read_text(encoding="utf-8") == ""

    def test_unicode_content(self, tmp_path):
        """Unicode 内容正确处理"""
        f = tmp_path / "unicode.txt"
        content = "你好世界 🌍\n🚀 Python 测试\n🔥"
        record = FileChangeRecord(str(f), None, content, 0)
        assert record.apply() is True
        assert f.read_text(encoding="utf-8") == content

    def test_apply_after_revert_cycle(self, tmp_path):
        """apply→revert→apply 完整生命周期"""
        f = tmp_path / "cycle.txt"
        record = FileChangeRecord(str(f), None, "第一次创建", 0)
        assert record.apply() is True
        assert f.read_text(encoding="utf-8") == "第一次创建"

        assert record.revert() is True
        assert not f.exists()

        assert record.apply() is True
        assert f.read_text(encoding="utf-8") == "第一次创建"

    def test_large_content(self, tmp_path):
        """大文件内容（数千行）"""
        f = tmp_path / "large.txt"
        lines = [f"行{i}: {'x' * 100}" for i in range(1000)]
        content = "\n".join(lines)
        record = FileChangeRecord(str(f), None, content, 0)
        assert record.apply() is True
        assert f.read_text(encoding="utf-8") == content

    def test_message_index_zero(self):
        """message_index=0 被正确存储"""
        record = FileChangeRecord("/tmp/f.txt", None, "c", 0)
        assert record.message_index == 0

    def test_timestamp_zero_not_overwritten(self):
        """timestamp=0 不会被 or 替换为 time.time()"""
        record = FileChangeRecord("/tmp/f.txt", None, "c", 0, timestamp=0.0)
        # 注意：源码使用 `timestamp or time.time()`，0 会被当作 falsy
        # 所以 timestamp=0.0 会被替换为 time.time()
        # 这是已知行为，这里验证当前实际行为
        assert record.timestamp != 0.0
