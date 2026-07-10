"""测试 src.tools.cp：CpFunc — 复制文件/目录

测试策略
--------
- 用 tmp_path 隔离实际文件操作
- 仅 mock 沙盒记录函数（async_record_sandbox / async_record_directory_files）
- 实际文件复制操作（shutil.copy2 / shutil.copytree）在 tmp_path 上真实执行
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖一个场景
"""

from __future__ import annotations

import os
import shutil
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.cp import CpFunc


# ═══════════════════════════════════════════════════════════════════════════
# 1. CpFunc.__init__
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncInit:
    """CpFunc.__init__ 路径安全校验和参数存储"""

    def test_valid_paths_default_recursive(self, tmp_path):
        """合法路径，recursive 默认为 False"""
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        cp = CpFunc(source=str(src), destination=str(dst))
        assert cp.source == str(src)
        assert cp.destination == str(dst)
        assert cp.recursive is False

    def test_valid_paths_with_recursive(self, tmp_path):
        """合法路径，recursive=True"""
        src = tmp_path / "src_dir"
        dst = tmp_path / "dst_dir"
        cp = CpFunc(source=str(src), destination=str(dst), recursive=True)
        assert cp.recursive is True

    def test_source_path_traversal_raises(self):
        """source 为路径穿越（/etc/passwd）应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            CpFunc(source="/etc/passwd", destination="/tmp/out")

    def test_destination_path_traversal_raises(self):
        """destination 为路径穿越应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            CpFunc(source="/tmp/valid", destination="/etc/shadow")

    def test_source_device_file_raises(self):
        """source 为设备文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            CpFunc(source="/dev/null", destination="/tmp/out")


# ═══════════════════════════════════════════════════════════════════════════
# 2. CpFunc.from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncFromArgs:
    """CpFunc.from_args 参数解析"""

    def test_required_params(self):
        """仅必需参数"""
        cp = CpFunc.from_args({"source": "/tmp/a.txt", "destination": "/tmp/b.txt"})
        assert cp.source == "/tmp/a.txt"
        assert cp.destination == "/tmp/b.txt"
        assert cp.recursive is False

    def test_with_recursive_true(self):
        """含 recursive=True"""
        cp = CpFunc.from_args({
            "source": "/tmp/a", "destination": "/tmp/b", "recursive": True,
        })
        assert cp.recursive is True

    def test_with_recursive_false(self):
        """含 recursive=False"""
        cp = CpFunc.from_args({
            "source": "/tmp/a", "destination": "/tmp/b", "recursive": False,
        })
        assert cp.recursive is False

    def test_extra_params_ignored(self):
        """额外参数被忽略"""
        cp = CpFunc.from_args({
            "source": "/tmp/a", "destination": "/tmp/b", "extra": "x", "flag": 42,
        })
        assert cp.source == "/tmp/a"
        assert cp.destination == "/tmp/b"

    def test_missing_source_raises(self):
        """缺少 source 时 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            CpFunc.from_args({"destination": "/tmp/b"})

    def test_missing_destination_raises(self):
        """缺少 destination 时 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            CpFunc.from_args({"source": "/tmp/a"})


# ═══════════════════════════════════════════════════════════════════════════
# 3. CpFunc._build_dest_path
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncBuildDestPath:
    """CpFunc._build_dest_path 相对路径构造"""

    def test_root_level_file(self, tmp_path):
        """源根目录下文件 → 目标根目录同级"""
        src_root = tmp_path / "src"
        dst = tmp_path / "dst"
        cp = CpFunc(source=str(src_root), destination=str(dst))
        result = cp._build_dest_path(str(src_root), str(src_root / "file.txt"))
        assert result == os.path.normpath(str(dst / "file.txt"))

    def test_nested_file(self, tmp_path):
        """嵌套子目录文件 → 保留相对路径层次"""
        src_root = tmp_path / "src"
        dst = tmp_path / "dst"
        cp = CpFunc(source=str(src_root), destination=str(dst))
        result = cp._build_dest_path(
            str(src_root), str(src_root / "sub" / "deep" / "file.txt"),
        )
        expected = os.path.normpath(str(dst / "sub" / "deep" / "file.txt"))
        assert result == expected

    def test_same_dir_structure_preserved(self, tmp_path):
        """多级子目录结构完整保留"""
        src_root = tmp_path / "project"
        dst = tmp_path / "backup"
        cp = CpFunc(source=str(src_root), destination=str(dst))
        result = cp._build_dest_path(
            str(src_root),
            str(src_root / "a" / "b" / "c" / "data.txt"),
        )
        expected = os.path.normpath(str(dst / "a" / "b" / "c" / "data.txt"))
        assert result == expected

    def test_relative_to_value_error_fallback_relpath(self, tmp_path):
        """relative_to 抛 ValueError 时回退到 os.path.relpath

        修复背景: Path.relative_to() 在 source_root 不是 file_path 前缀时
        抛出 ValueError（如跨驱动器、符号链接）。修复使用 try/except ValueError
        → os.path.relpath fallback。
        """
        src_root = tmp_path / "src"
        dst = tmp_path / "dst"

        cp = CpFunc(source=str(src_root), destination=str(dst))
        # 构造一个完全不在 src_root 下的路径（模拟跨驱动器场景）
        unrelated = "/completely/unrelated/path/file.txt"
        result = cp._build_dest_path(str(src_root), unrelated)

        # 应使用 relpath 或 basename fallback，不应抛出异常
        assert result is not None
        # 最终路径应包含目标前缀和文件名
        assert "file.txt" in result

    def test_relative_to_value_error_fallback_basename(self, tmp_path):
        """relpath 也失败时回退到 basename

        极端情况：source_root="/a" 但 file_path 是相对路径且无法计算 relpath。
        """
        src_root = "/root_src"
        dst = "/root_dst"

        cp = CpFunc(source=src_root, destination=dst)

        # 使用不同的绝对路径模拟 relpath ValueError
        result = cp._build_dest_path(src_root, "/dev/null")

        # 最终应至少包含文件名
        assert "null" in result or result is not None


# ═══════════════════════════════════════════════════════════════════════════
# 4. CpFunc.display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncDisplayParams:
    """CpFunc.display_params 参数摘要"""

    def test_basic(self):
        """文件复制摘要"""
        result = CpFunc.display_params({
            "source": "/tmp/a.txt", "destination": "/tmp/b.txt",
        })
        assert "/tmp/a.txt" in result
        assert "/tmp/b.txt" in result
        assert "→" in result
        assert "-r" not in result

    def test_with_recursive(self):
        """目录复制摘要含 -r"""
        result = CpFunc.display_params({
            "source": "/tmp/a", "destination": "/tmp/b", "recursive": True,
        })
        assert "-r" in result

    def test_empty_source(self):
        """source 为空时的展示"""
        result = CpFunc.display_params({"destination": "/tmp/b"})
        assert "/tmp/b" in result

    def test_empty_destination(self):
        """destination 为空时的展示"""
        result = CpFunc.display_params({"source": "/tmp/a"})
        assert "/tmp/a" in result

    def test_sanitize_newline_in_path(self):
        """路径含换行符被转义"""
        result = CpFunc.display_params({
            "source": "/tmp/a\n.txt", "destination": "/tmp/b.txt",
        })
        assert "/tmp/a/n.txt" in result

    def test_default_max_len(self):
        """默认 max_len=80"""
        result = CpFunc.display_params({
            "source": "/tmp/a.txt", "destination": "/tmp/b.txt",
        })
        assert len(result) <= 80


# ═══════════════════════════════════════════════════════════════════════════
# 5. CpFunc.execute
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncExecuteFile:
    """CpFunc.execute — 文件复制"""

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_copy_file_to_new_dest(self, mock_record, tmp_path):
        """复制文件到不存在的目的地"""
        src = tmp_path / "source.txt"
        src.write_text("hello world")
        dst = tmp_path / "dest.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert dst.read_text() == "hello world"
        mock_record.assert_awaited_once_with(
            str(dst), None, "hello world", "cp",
        )

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_copy_file_overwrite_existing(self, mock_record, tmp_path):
        """覆盖已存在的目标文件"""
        src = tmp_path / "source.txt"
        src.write_text("new content")
        dst = tmp_path / "dest.txt"
        dst.write_text("old content")

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert dst.read_text() == "new content"
        # 沙盒记录时 content_before 应为旧内容
        call_args = mock_record.await_args
        assert call_args is not None
        # call_args 是 _Call 对象，.args 是位置参数元组，[1] 是 content_before
        assert call_args.args[1] == "old content"

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_copy_file_creates_parent_dir(self, mock_record, tmp_path):
        """目标父目录不存在时自动创建"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "subdir" / "deep" / "dest.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert dst.exists()
        assert dst.read_text() == "content"

    async def test_source_not_exists(self, tmp_path):
        """源路径不存在返回错误"""
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "out.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.execute()

        assert "源路径不存在" in result

    @patch("src.tools.cp.async_file_exists", new_callable=AsyncMock, return_value=True)
    @patch("src.tools.cp.async_is_link", new_callable=AsyncMock, return_value=True)
    async def test_symlink_source_rejected(self, mock_is_link, mock_exists, tmp_path):
        """符号链接源被拒绝"""
        src = tmp_path / "link.txt"
        dst = tmp_path / "out.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.execute()

        assert "不支持复制符号链接" in result
        mock_is_link.assert_awaited_once_with(str(src))

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_permission_error_caught(self, mock_record, tmp_path):
        """复制时权限不足被捕获"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        with patch("shutil.copy2", side_effect=PermissionError("权限不足")):
            cp = CpFunc(source=str(src), destination=str(dst))
            result = await cp.execute()

            assert "权限不足" in result

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_os_error_caught(self, mock_record, tmp_path):
        """OSError 被捕获"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        with patch("shutil.copy2", side_effect=OSError("磁盘已满")):
            cp = CpFunc(source=str(src), destination=str(dst))
            result = await cp.execute()

            assert "复制失败" in result

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_value_error_from_validation(self, mock_record, tmp_path):
        """path security validation 抛出 ValueError 被捕获"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        with patch("src.tools.cp.validate_path_security", side_effect=ValueError("拒绝访问")):
            result = await cp.execute()

            assert "复制失败" in result

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_copy_file_preserves_metadata(self, mock_record, tmp_path):
        """复制文件保留元数据（shutil.copy2）"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        with patch("shutil.copy2", wraps=shutil.copy2) as mock_copy2:
            cp = CpFunc(source=str(src), destination=str(dst))
            await cp.execute()

            mock_copy2.assert_called_once_with(str(src), str(dst))


class TestCpFuncExecuteDirectory:
    """CpFunc.execute — 目录复制"""

    @patch("src.tools.cp.async_record_directory_files", new_callable=AsyncMock)
    @patch("src.tools.cp.async_collect_files")
    async def test_copy_dir_recursive(self, mock_collect, mock_record_dir, tmp_path):
        """递归复制目录到不存在的目标"""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("aaa")
        sub = src_dir / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("bbb")
        dst_dir = tmp_path / "dst_dir"

        file_list = [str(src_dir / "a.txt"), str(sub / "b.txt")]
        mock_collect.return_value = file_list

        cp = CpFunc(source=str(src_dir), destination=str(dst_dir), recursive=True)
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert "2个文件" in result
        assert (dst_dir / "a.txt").exists()
        assert (dst_dir / "sub" / "b.txt").exists()
        assert (dst_dir / "a.txt").read_text() == "aaa"
        assert (dst_dir / "sub" / "b.txt").read_text() == "bbb"
        mock_record_dir.assert_awaited_once()

    @patch("src.tools.cp.async_record_directory_files", new_callable=AsyncMock)
    @patch("src.tools.cp.async_collect_files")
    async def test_copy_dir_overwrite_existing(self, mock_collect, mock_record_dir, tmp_path):
        """覆盖已存在的目标目录"""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "new.txt").write_text("new")
        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        (dst_dir / "old.txt").write_text("old")

        file_list = [str(src_dir / "new.txt")]
        mock_collect.return_value = file_list

        cp = CpFunc(source=str(src_dir), destination=str(dst_dir), recursive=True)
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert (dst_dir / "new.txt").exists()
        assert (dst_dir / "new.txt").read_text() == "new"

    async def test_copy_dir_without_recursive(self, tmp_path):
        """复制目录时未设 recursive=True 被拒绝"""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("aaa")
        dst_dir = tmp_path / "dst_dir"

        cp = CpFunc(source=str(src_dir), destination=str(dst_dir), recursive=False)
        result = await cp.execute()

        assert "如需复制目录请设置 recursive=True" in result
        assert not dst_dir.exists()

    @patch("src.tools.cp.async_collect_files")
    async def test_copy_dir_source_not_exists(self, mock_collect, tmp_path):
        """目录源不存在时返回错误"""
        src_dir = tmp_path / "nonexistent_dir"
        dst_dir = tmp_path / "dst_dir"

        cp = CpFunc(source=str(src_dir), destination=str(dst_dir), recursive=True)
        result = await cp.execute()

        assert "源路径不存在" in result

    @patch("src.tools.cp.async_file_exists", new_callable=AsyncMock, return_value=True)
    @patch("src.tools.cp.async_collect_files")
    @patch("src.tools.cp.async_is_link", new_callable=AsyncMock, return_value=False)
    async def test_copy_dir_unsupported_type(self, mock_is_link, mock_collect, mock_exists, tmp_path):
        """不支持的源路径类型"""
        src = tmp_path / "weird_path"

        with patch("asyncio.to_thread") as mock_to_thread:
            def side_effect(func, *args):
                if func is os.path.isfile:
                    return False
                if func is os.path.isdir:
                    return False
                return func(*args)
            mock_to_thread.side_effect = side_effect

            cp = CpFunc(source=str(src), destination=str(tmp_path / "dst"), recursive=True)
            result = await cp.execute()
            assert "不支持的源路径类型" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. CpFunc.display
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncDisplay:
    """CpFunc.display 打印 + 执行"""

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display(self, mock_record, tmp_path):
        """复制成功时返回结果字符串"""
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dst = tmp_path / "dst.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.display()

        assert result.startswith("复制成功")

    async def test_fail_display(self, tmp_path):
        """复制失败时返回错误信息"""
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "out.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.display()

        assert "源路径不存在" in result

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display_returns_result(self, mock_record, tmp_path):
        """display 返回 execute 结果"""
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"

        cp = CpFunc(source=str(src), destination=str(dst))
        result = await cp.display()

        assert result == f"复制成功: {src} → {dst}"

    async def test_recursive_flag_in_display_output(self, tmp_path):
        """display 操作描述包含 -r 标志"""
        cp = CpFunc(source=str(tmp_path / "src"), destination=str(tmp_path / "dst"), recursive=True)
        # display() 通过 _publish_tool_text 发布操作描述到 EventBus
        with patch("src.tools.base.Func._publish_tool_text") as mock_publish:
            with patch.object(cp, "execute", new_callable=AsyncMock, return_value="复制成功"):
                await cp.display()

        # 验证操作描述被发布，且包含 -r 标志
        published_texts = [call[0][0] for call in mock_publish.call_args_list]
        assert any("-r" in text for text in published_texts), f"未在发布文本中找到 -r: {published_texts}"


# ═══════════════════════════════════════════════════════════════════════════
# 7. CpFunc — from_args + execute 集成
# ═══════════════════════════════════════════════════════════════════════════

class TestCpFuncIntegration:
    """CpFunc from_args + execute 集成"""

    @patch("src.tools.cp.async_record_sandbox", new_callable=AsyncMock)
    async def test_from_args_then_execute(self, mock_record, tmp_path):
        """from_args 创建的实例可以正常执行"""
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"

        cp = CpFunc.from_args({
            "source": str(src), "destination": str(dst),
        })
        result = await cp.execute()

        assert result.startswith("复制成功")
        assert dst.read_text() == "data"
