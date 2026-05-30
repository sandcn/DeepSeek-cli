"""测试 UpdateFileFunc

测试策略
--------
- 使用 tmp_path 隔离文件系统操作
- mock async_record_file_change_from_context 避免依赖沙盒
- _get_new_content 读真实文件，测试边界条件时直接构造实例
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖单一场景
"""

import os
from unittest.mock import patch, AsyncMock

import pytest

from src.tools.update_file import (
    UpdateFileFunc,
    StringNotFoundError,
    AmbiguousMatchError,
)


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """__init__ 路径存储和默认参数"""

    def test_valid_path(self, tmp_path):
        f = tmp_path / "test.txt"
        uf = UpdateFileFunc(str(f), "old", "new")
        assert uf.path == str(f)
        assert uf.old_string == "old"
        assert uf.new_string == "new"
        assert uf.replace_all is False

    def test_replace_all_default_false(self, tmp_path):
        f = tmp_path / "test.txt"
        uf = UpdateFileFunc(str(f), "old", "new", replace_all=True)
        assert uf.replace_all is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. _get_new_content — 替换模式
# ═══════════════════════════════════════════════════════════════════════════

class TestGetNewContentReplace:
    """_get_new_content 精确替换"""

    @pytest.mark.asyncio
    async def test_simple_replacement(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        uf = UpdateFileFunc(str(f), "hello", "hi")
        result = await uf._get_new_content()
        assert result == "hi world"

    @pytest.mark.asyncio
    async def test_replace_maintains_formatting(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("    def foo():\n        pass\n")
        uf = UpdateFileFunc(str(f), "    def foo():", "    def bar():")
        result = await uf._get_new_content()
        assert result == "    def bar():\n        pass\n"

    @pytest.mark.asyncio
    async def test_replace_with_newlines(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("line1\nline2\nline3\n")
        uf = UpdateFileFunc(str(f), "line2\n", "middle\n")
        result = await uf._get_new_content()
        assert result == "line1\nmiddle\nline3\n"

    @pytest.mark.asyncio
    async def test_replace_empty_old_string_is_append(self, tmp_path):
        """old_string 为空 = 追加模式"""
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        uf = UpdateFileFunc(str(f), "", "world\n")
        result = await uf._get_new_content()
        assert result == "hello\nworld\n"

    @pytest.mark.asyncio
    async def test_delete_mode(self, tmp_path):
        """new_string 为空 = 删除"""
        f = tmp_path / "test.txt"
        f.write_text("keep\ndelete\nkeep\n")
        uf = UpdateFileFunc(str(f), "delete\n", "")
        result = await uf._get_new_content()
        assert result == "keep\nkeep\n"

    @pytest.mark.asyncio
    async def test_replace_identical_string(self, tmp_path):
        """old == new 时内容不变（唯一出现的字符串）"""
        f = tmp_path / "test.txt"
        f.write_text("the only match here")
        uf = UpdateFileFunc(str(f), "only match", "only match")
        result = await uf._get_new_content()
        assert result == "the only match here"


# ═══════════════════════════════════════════════════════════════════════════
# 3. _get_new_content — 追加模式（old_string=""）
# ═══════════════════════════════════════════════════════════════════════════

class TestGetNewContentAppend:
    """old_string="" 追加模式"""

    @pytest.mark.asyncio
    async def test_append_to_file_ending_with_newline(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("existing\n")
        uf = UpdateFileFunc(str(f), "", "new content\n")
        result = await uf._get_new_content()
        assert result == "existing\nnew content\n"

    @pytest.mark.asyncio
    async def test_append_to_file_not_ending_with_newline(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("existing")
        uf = UpdateFileFunc(str(f), "", "new content\n")
        result = await uf._get_new_content()
        # 自动补换行再追加
        assert result == "existing\nnew content\n"

    @pytest.mark.asyncio
    async def test_append_to_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        uf = UpdateFileFunc(str(f), "", "new content")
        result = await uf._get_new_content()
        assert result == "new content"

    @pytest.mark.asyncio
    async def test_append_to_new_file(self, tmp_path):
        """文件不存在时追加模式也能正常工作（_read_original 返回空）"""
        f = tmp_path / "nonexistent.txt"
        uf = UpdateFileFunc(str(f), "", "new content")
        result = await uf._get_new_content()
        assert result == "new content"


# ═══════════════════════════════════════════════════════════════════════════
# 4. _get_new_content — 错误边界
# ═══════════════════════════════════════════════════════════════════════════

class TestGetNewContentErrors:
    """_get_new_content 各种错误边界"""

    @pytest.mark.asyncio
    async def test_file_not_found_with_nonempty_old_string(self, tmp_path):
        """文件不存在且 old_string 非空 → StringNotFoundError"""
        f = tmp_path / "nonexistent.txt"
        uf = UpdateFileFunc(str(f), "something", "new")
        with pytest.raises(StringNotFoundError) as exc:
            await uf._get_new_content()
        assert "文件不存在或为空" in str(exc.value)

    @pytest.mark.asyncio
    async def test_empty_file_with_nonempty_old_string(self, tmp_path):
        """文件为空且 old_string 非空 → StringNotFoundError"""
        f = tmp_path / "empty.txt"
        f.write_text("")
        uf = UpdateFileFunc(str(f), "something", "new")
        with pytest.raises(StringNotFoundError) as exc:
            await uf._get_new_content()
        assert "文件不存在或为空" in str(exc.value)

    @pytest.mark.asyncio
    async def test_old_string_not_found(self, tmp_path):
        """old_string 出现 0 次 → StringNotFoundError"""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        uf = UpdateFileFunc(str(f), "nonexistent", "new")
        with pytest.raises(StringNotFoundError) as exc:
            await uf._get_new_content()
        assert "未找到匹配内容" in str(exc.value)

    @pytest.mark.asyncio
    async def test_old_string_ambiguous(self, tmp_path):
        """old_string 出现多次 → AmbiguousMatchError（replace_all=False 时）"""
        f = tmp_path / "test.txt"
        f.write_text("repeat\nother\nrepeat\nend")
        uf = UpdateFileFunc(str(f), "repeat", "changed")
        with pytest.raises(AmbiguousMatchError) as exc:
            await uf._get_new_content()
        assert "出现了" in str(exc.value)
        assert "2次" in str(exc.value)
        assert "replace_all=True" in str(exc.value)

    @pytest.mark.asyncio
    async def test_old_string_truncated_in_error(self, tmp_path):
        """错误信息中 old_string 超过 60 字符时截断"""
        f = tmp_path / "test.txt"
        f.write_text("a")
        long_str = "x" * 100
        uf = UpdateFileFunc(str(f), long_str, "new")
        with pytest.raises(StringNotFoundError) as exc:
            await uf._get_new_content()
        msg = str(exc.value)
        assert "..." in msg
        assert len(msg.split('"')[1]) <= 63  # 60 + "..."

    @pytest.mark.asyncio
    async def test_sandbox_still_called_on_error_path(self, tmp_path):
        """错误路径上 execute 不抛异常，返回失败消息"""
        f = tmp_path / "test.txt"
        f.write_text("hello")

        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ):
            uf = UpdateFileFunc(str(f), "nonexistent", "new")
            result = await uf.execute()
            assert "更新失败" in result
            assert "未找到匹配内容" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. _get_new_content — 全局替换（replace_all=True）
# ═══════════════════════════════════════════════════════════════════════════

class TestGetNewContentReplaceAll:
    """replace_all=True 全局替换"""

    @pytest.mark.asyncio
    async def test_replace_all_matches(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("x = 1\ny = 2\nx = 3\n")
        uf = UpdateFileFunc(str(f), "x", "z", replace_all=True)
        result = await uf._get_new_content()
        assert result == "z = 1\ny = 2\nz = 3\n"

    @pytest.mark.asyncio
    async def test_replace_all_delete(self, tmp_path):
        """全局删除所有匹配"""
        f = tmp_path / "test.txt"
        f.write_text("a\nb\na\nc\n")
        uf = UpdateFileFunc(str(f), "a\n", "", replace_all=True)
        result = await uf._get_new_content()
        assert result == "b\nc\n"

    @pytest.mark.asyncio
    async def test_replace_all_single_match(self, tmp_path):
        """只出现 1 次时，replace_all 与普通替换行为一致"""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        uf = UpdateFileFunc(str(f), "hello", "hi", replace_all=True)
        result = await uf._get_new_content()
        assert result == "hi world"

    @pytest.mark.asyncio
    async def test_replace_all_zero_match_raises(self, tmp_path):
        """0 匹配仍报 StringNotFoundError"""
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        uf = UpdateFileFunc(str(f), "nonexistent", "new", replace_all=True)
        with pytest.raises(StringNotFoundError):
            await uf._get_new_content()

    @pytest.mark.asyncio
    async def test_replace_all_empty_file_raises(self, tmp_path):
        """空文件报 StringNotFoundError"""
        f = tmp_path / "empty.txt"
        f.write_text("")
        uf = UpdateFileFunc(str(f), "anything", "new", replace_all=True)
        with pytest.raises(StringNotFoundError):
            await uf._get_new_content()

    @pytest.mark.asyncio
    async def test_replace_all_append_ignores_replace_all(self, tmp_path):
        """追加模式忽略 replace_all"""
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        uf = UpdateFileFunc(str(f), "", "world\n", replace_all=True)
        result = await uf._get_new_content()
        assert result == "hello\nworld\n"

    @pytest.mark.asyncio
    async def test_replace_all_with_newlines(self, tmp_path):
        """多行文本全局替换"""
        f = tmp_path / "test.txt"
        f.write_text("foo\nbar\nfoo\nbaz\nfoo\n")
        uf = UpdateFileFunc(str(f), "foo\n", "qux\n", replace_all=True)
        result = await uf._get_new_content()
        assert result == "qux\nbar\nqux\nbaz\nqux\n"

    @pytest.mark.asyncio
    async def test_replace_all_same_old_new(self, tmp_path):
        """old==new 时全局替换不改变内容"""
        f = tmp_path / "test.txt"
        f.write_text("x y x y x")
        uf = UpdateFileFunc(str(f), "x", "x", replace_all=True)
        result = await uf._get_new_content()
        assert result == "x y x y x"

    @pytest.mark.asyncio
    async def test_replace_all_execute_success(self, tmp_path):
        """execute 完整流程：全局替换写入"""
        from unittest.mock import patch, AsyncMock

        f = tmp_path / "test.txt"
        f.write_text("a\nb\na\n")

        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ):
            uf = UpdateFileFunc(str(f), "a", "z", replace_all=True)
            result = await uf.execute()

        assert "更新成功" in result
        assert f.read_text() == "z\nb\nz\n"


# ═══════════════════════════════════════════════════════════════════════════
# 6. execute
# ═══════════════════════════════════════════════════════════════════════════

class TestExecute:
    """execute 完整写文件操作"""

    @pytest.fixture
    def _mock_sandbox(self):
        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ) as m:
            yield m

    # ── 路径安全校验（原在 __init__ 中，现已移至 execute） ──

    @pytest.mark.asyncio
    async def test_dangerous_path_raises_on_execute(self):
        """危险路径在 execute() 时返回失败消息而非抛出异常"""
        uf = UpdateFileFunc("/etc/passwd", "old", "new")
        result = await uf.execute()
        assert "更新失败" in result
        assert "不允许写入系统关键文件" in result

    @pytest.mark.asyncio
    async def test_normal_replacement(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("hello world")

        uf = UpdateFileFunc(str(f), "hello", "hi")
        result = await uf.execute()

        assert "更新成功" in result
        assert f.read_text() == "hi world"

    @pytest.mark.asyncio
    async def test_append_mode(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")

        uf = UpdateFileFunc(str(f), "", "world\n")
        result = await uf.execute()

        assert "更新成功" in result
        assert f.read_text() == "hello\nworld\n"

    @pytest.mark.asyncio
    async def test_delete_mode(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("keep\ndelete\nkeep\n")

        uf = UpdateFileFunc(str(f), "delete\n", "")
        result = await uf.execute()

        assert "更新成功" in result
        assert f.read_text() == "keep\nkeep\n"

    @pytest.mark.asyncio
    async def test_append_to_new_file(self, tmp_path, _mock_sandbox):
        """追加模式创建一个新文件"""
        f = tmp_path / "new_file.txt"

        uf = UpdateFileFunc(str(f), "", "initial content\n")
        result = await uf.execute()

        assert "更新成功" in result
        assert f.read_text() == "initial content\n"

    @pytest.mark.asyncio
    async def test_result_contains_lines_and_size(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("hello world")

        uf = UpdateFileFunc(str(f), "hello", "hi")
        result = await uf.execute()

        assert "更新成功" in result
        assert "L" in result
        assert "B" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. display
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplay:
    """display 显示 diff 预览并执行更新"""

    @pytest.fixture
    def _mock_sandbox(self):
        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_display_replacement_returns_success(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("hello world")

        uf = UpdateFileFunc(str(f), "hello", "hi")
        result = await uf.display()

        assert "更新成功" in result
        assert f.read_text() == "hi world"

    @pytest.mark.asyncio
    async def test_display_append_returns_success(self, tmp_path, _mock_sandbox):
        f = tmp_path / "test.txt"
        f.write_text("hello\n")

        uf = UpdateFileFunc(str(f), "", "world\n")
        result = await uf.display()

        assert "更新成功" in result
        assert f.read_text() == "hello\nworld\n"


# ═══════════════════════════════════════════════════════════════════════════
# 7. to_tool_schema & display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestToToolSchema:
    """to_tool_schema 返回正确的 schema 格式"""

    def test_schema_structure(self):
        schema = UpdateFileFunc.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "update_file"
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "old_string" in props
        assert "new_string" in props
        assert "replace_all" in props
        assert props["replace_all"]["type"] == "boolean"
        assert schema["function"]["parameters"]["required"] == ["path", "old_string", "new_string"]

    def test_display_params_show_replace_all(self):
        result = UpdateFileFunc.display_params({"path": "/tmp/test.txt", "replace_all": True})
        assert "[全局替换]" in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要显示"""

    def test_shows_path(self):
        result = UpdateFileFunc.display_params({"path": "/tmp/test.txt"})
        assert "/tmp/test.txt" in result

    def test_empty_args(self):
        result = UpdateFileFunc.display_params({})
        assert result == "''"

    def test_newline_sanitized(self):
        result = UpdateFileFunc.display_params({"path": "file\nname.txt"})
        assert "\n" not in result
        assert "/n" in result


# ═══════════════════════════════════════════════════════════════════════════
# 10. _success_verb / _mode_desc
# ═══════════════════════════════════════════════════════════════════════════

class TestMeta:
    """工具元数据方法"""

    def test_success_verb(self, tmp_path):
        uf = UpdateFileFunc(str(tmp_path / "x.txt"), "", "")
        assert uf._success_verb() == "更新成功"

    def test_mode_desc_replace(self, tmp_path):
        uf = UpdateFileFunc(str(tmp_path / "x.txt"), "old", "new")
        assert uf._mode_desc() == "字符串替换"

    def test_mode_desc_replace_all(self, tmp_path):
        uf = UpdateFileFunc(str(tmp_path / "x.txt"), "old", "new", replace_all=True)
        assert uf._mode_desc() == "全局替换"

    def test_mode_desc_append(self, tmp_path):
        uf = UpdateFileFunc(str(tmp_path / "x.txt"), "", "new")
        assert uf._mode_desc() == "追加内容"
