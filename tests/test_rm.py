"""测试 src.tools.rm：RmFunc — 删除文件/目录

测试策略
--------
- 用 tmp_path 隔离实际文件操作
- 仅 mock 沙盒记录函数（async_record_sandbox）
- 实际删除操作（os.remove / shutil.rmtree）在 tmp_path 上真实执行
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖一个场景
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.rm import RmFunc


# ═══════════════════════════════════════════════════════════════════════════
# 1. RmFunc.__init__
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncInit:
    """RmFunc.__init__ 路径安全校验"""

    def test_valid_path_default_recursive(self, tmp_path):
        """合法路径，recursive 默认为 False"""
        p = tmp_path / "target.txt"
        rm = RmFunc(path=str(p))
        assert rm.path == str(p)
        assert rm.recursive is False

    def test_valid_path_with_recursive(self, tmp_path):
        """合法路径，recursive=True"""
        p = tmp_path / "target_dir"
        rm = RmFunc(path=str(p), recursive=True)
        assert rm.recursive is True

    def test_path_traversal_raises(self):
        """路径穿越（/etc/passwd）应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            RmFunc(path="/etc/passwd")

    def test_device_file_raises(self):
        """设备文件路径应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            RmFunc(path="/dev/null")


# ═══════════════════════════════════════════════════════════════════════════
# 2. RmFunc.from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncFromArgs:
    """RmFunc.from_args 参数解析"""

    def test_required_params(self):
        """仅必需参数 path"""
        rm = RmFunc.from_args({"path": "/tmp/target.txt"})
        assert rm.path == "/tmp/target.txt"
        assert rm.recursive is False

    def test_with_recursive_true(self):
        """含 recursive=True"""
        rm = RmFunc.from_args({"path": "/tmp/dir", "recursive": True})
        assert rm.recursive is True

    def test_with_recursive_false(self):
        """含 recursive=False"""
        rm = RmFunc.from_args({"path": "/tmp/f", "recursive": False})
        assert rm.recursive is False

    def test_extra_params_ignored(self):
        """额外参数被忽略"""
        rm = RmFunc.from_args({"path": "/tmp/t", "extra": "x"})
        assert rm.path == "/tmp/t"

    def test_missing_path_raises(self):
        """缺少 path 抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            RmFunc.from_args({})


# ═══════════════════════════════════════════════════════════════════════════
# 3. RmFunc.display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncDisplayParams:
    """RmFunc.display_params 参数摘要"""

    def test_basic(self):
        """基本参数摘要"""
        result = RmFunc.display_params({"path": "/tmp/target.txt"})
        assert "/tmp/target.txt" in result
        assert "-r" not in result

    def test_with_recursive(self):
        """含 -r 标志"""
        result = RmFunc.display_params({"path": "/tmp/dir", "recursive": True})
        assert "-r" in result

    def test_empty_path(self):
        """path 为空"""
        result = RmFunc.display_params({})
        # 不应崩溃
        assert isinstance(result, str)

    def test_sanitize_newline(self):
        """路径含换行符被转义"""
        result = RmFunc.display_params({"path": "/tmp/a\n.txt"})
        assert "a/n.txt" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. RmFunc.execute
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncExecuteFile:
    """RmFunc.execute — 文件删除"""

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_delete_file(self, mock_record, tmp_path):
        """删除一个存在的文件"""
        f = tmp_path / "target.txt"
        f.write_text("content to delete")

        rm = RmFunc(path=str(f))
        result = await rm.execute()

        assert result.startswith("删除成功")
        assert not f.exists()
        # 沙盒记录：content_before 为文件原内容
        mock_record.assert_awaited_once_with(
            str(f), "content to delete", None, "rm",
        )

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_delete_symlink(self, mock_record, tmp_path):
        """删除符号链接（只删链接本身）"""
        target = tmp_path / "target.txt"
        target.write_text("target content")
        link = tmp_path / "mylink.lnk"
        link.symlink_to(target)

        rm = RmFunc(path=str(link))
        result = await rm.execute()

        assert result.startswith("删除成功")
        assert not link.exists()
        assert target.exists()  # 目标文件仍在
        mock_record.assert_awaited_once()

    async def test_path_not_exists(self, tmp_path):
        """路径不存在返回提示"""
        f = tmp_path / "nonexistent.txt"

        rm = RmFunc(path=str(f))
        result = await rm.execute()

        assert "路径不存在" in result

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_permission_error_caught(self, mock_record, tmp_path):
        """删除时权限不足被捕获"""
        f = tmp_path / "target.txt"
        f.write_text("content")

        with patch("src.tools.rm.async_remove_file", side_effect=PermissionError("权限不足")):
            rm = RmFunc(path=str(f))
            result = await rm.execute()

            assert "权限不足" in result

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_os_error_caught(self, mock_record, tmp_path):
        """OSError 被捕获"""
        f = tmp_path / "target.txt"
        f.write_text("content")

        with patch("src.tools.rm.async_remove_file", side_effect=OSError("设备繁忙")):
            rm = RmFunc(path=str(f))
            result = await rm.execute()

            assert "删除失败" in result


class TestRmFuncExecuteDirectory:
    """RmFunc.execute — 目录删除"""

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_delete_dir_recursive(self, mock_record, tmp_path):
        """递归删除目录及其所有文件"""
        d = tmp_path / "target_dir"
        d.mkdir()
        (d / "a.txt").write_text("aaa")
        sub = d / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("bbb")

        rm = RmFunc(path=str(d), recursive=True)
        result = await rm.execute()

        assert result.startswith("删除成功")
        assert "2个文件" in result
        assert not d.exists()
        # 沙盒记录了每个文件 + 目录本身
        assert mock_record.await_count == 3  # a.txt + sub/b.txt + 目录

    async def test_non_empty_dir_without_recursive(self, tmp_path):
        """非空目录未设 recursive=True 被拒绝"""
        d = tmp_path / "target_dir"
        d.mkdir()
        (d / "file.txt").write_text("content")

        rm = RmFunc(path=str(d), recursive=False)
        result = await rm.execute()

        assert "目录非空，如需删除目录请设置 recursive=True" in result
        assert d.exists()
        assert (d / "file.txt").exists()

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_empty_dir_with_recursive(self, mock_record, tmp_path):
        """空目录 recursive=True 删除"""
        d = tmp_path / "empty_dir"
        d.mkdir()

        rm = RmFunc(path=str(d), recursive=True)
        result = await rm.execute()

        assert result.startswith("删除成功")
        assert "0个文件" in result
        assert not d.exists()

    async def test_dir_path_not_exists(self, tmp_path):
        """目录路径不存在返回提示"""
        d = tmp_path / "nonexistent_dir"

        rm = RmFunc(path=str(d), recursive=True)
        result = await rm.execute()

        assert "路径不存在" in result

    @patch("src.tools.rm.async_collect_files")
    @patch("src.tools.rm.async_file_exists", new_callable=AsyncMock, return_value=True)
    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_unsupported_path_type(self, mock_record, mock_collect, mock_exists, tmp_path):
        """不支持的路径类型"""
        d = tmp_path / "weird_path"

        with patch("asyncio.to_thread") as mock_to_thread:
            def side_effect(func, *args):
                if func is os.path.isfile:
                    return False
                if func is os.path.isdir:
                    return False
                return func(*args)
            mock_to_thread.side_effect = side_effect

            rm = RmFunc(path=str(d), recursive=True)
            result = await rm.execute()
            assert "不支持的路径类型" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. RmFunc.display
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncDisplay:
    """RmFunc.display 打印 + 执行"""

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display(self, mock_record, tmp_path):
        """删除成功时返回结果字符串"""
        f = tmp_path / "target.txt"
        f.write_text("content")

        rm = RmFunc(path=str(f))
        result = await rm.display()

        assert result.startswith("删除成功")
        assert not f.exists()

    async def test_fail_display(self, tmp_path):
        """删除失败时返回错误信息"""
        f = tmp_path / "nonexistent.txt"

        rm = RmFunc(path=str(f))
        result = await rm.display()

        assert "路径不存在" in result

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display_returns_result(self, mock_record, tmp_path):
        """display 返回 execute 结果"""
        f = tmp_path / "target.txt"
        f.write_text("data")

        rm = RmFunc(path=str(f))
        result = await rm.display()

        assert result.startswith("删除成功")

    async def test_recursive_flag_in_display_output(self, tmp_path):
        """display 操作描述包含 -r 标志"""
        rm = RmFunc(path=str(tmp_path / "dir"), recursive=True)
        # display() 通过 _publish_tool_text 发布操作描述到 EventBus
        with patch("src.tools.base.Func._publish_tool_text") as mock_publish:
            with patch.object(rm, "execute", new_callable=AsyncMock, return_value="删除成功"):
                await rm.display()

        published_texts = [call[0][0] for call in mock_publish.call_args_list]
        assert any("-r" in text for text in published_texts), f"未在发布文本中找到 -r: {published_texts}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. RmFunc — from_args + execute 集成
# ═══════════════════════════════════════════════════════════════════════════

class TestRmFuncIntegration:
    """RmFunc from_args + execute 集成"""

    @patch("src.tools.rm.async_record_sandbox", new_callable=AsyncMock)
    async def test_from_args_then_execute(self, mock_record, tmp_path):
        """from_args 创建的实例可以正常执行"""
        f = tmp_path / "target.txt"
        f.write_text("data")

        rm = RmFunc.from_args({"path": str(f)})
        result = await rm.execute()

        assert result.startswith("删除成功")
        assert not f.exists()
