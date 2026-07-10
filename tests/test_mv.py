"""测试 src.tools.mv：MvFunc — 移动文件/目录

测试策略
--------
- 用 tmp_path 隔离实际文件操作
- 仅 mock 沙盒记录函数（async_record_sandbox / async_record_directory_files）
- 实际文件移动（shutil.move）在 tmp_path 上真实执行
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖一个场景
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.mv import MvFunc


# ═══════════════════════════════════════════════════════════════════════════
# 1. MvFunc.__init__
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncInit:
    """MvFunc.__init__ 路径安全校验"""

    def test_valid_paths(self, tmp_path):
        """合法路径"""
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        mv = MvFunc(source=str(src), destination=str(dst))
        assert mv.source == str(src)
        assert mv.destination == str(dst)

    def test_source_path_traversal_raises(self):
        """source 路径穿越（/etc/passwd）应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            MvFunc(source="/etc/passwd", destination="/tmp/out")

    def test_destination_path_traversal_raises(self):
        """destination 路径穿越应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            MvFunc(source="/tmp/valid", destination="/etc/shadow")

    def test_source_device_file_raises(self):
        """source 设备文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            MvFunc(source="/dev/null", destination="/tmp/out")


# ═══════════════════════════════════════════════════════════════════════════
# 2. MvFunc.from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncFromArgs:
    """MvFunc.from_args 参数解析"""

    def test_required_params(self):
        """仅必需参数"""
        mv = MvFunc.from_args({"source": "/tmp/a.txt", "destination": "/tmp/b.txt"})
        assert mv.source == "/tmp/a.txt"
        assert mv.destination == "/tmp/b.txt"

    def test_extra_params_ignored(self):
        """额外参数被忽略"""
        mv = MvFunc.from_args({
            "source": "/tmp/a", "destination": "/tmp/b", "extra": "x",
        })
        assert mv.source == "/tmp/a"
        assert mv.destination == "/tmp/b"

    def test_missing_source_raises(self):
        """缺少 source 抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            MvFunc.from_args({"destination": "/tmp/b"})

    def test_missing_destination_raises(self):
        """缺少 destination 抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            MvFunc.from_args({"source": "/tmp/a"})


# ═══════════════════════════════════════════════════════════════════════════
# 3. MvFunc.display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncDisplayParams:
    """MvFunc.display_params 参数摘要"""

    def test_basic(self):
        """基本参数摘要"""
        result = MvFunc.display_params({
            "source": "/tmp/a.txt", "destination": "/tmp/b.txt",
        })
        assert "/tmp/a.txt" in result
        assert "/tmp/b.txt" in result
        assert "->" in result

    def test_empty_source(self):
        """source 为空"""
        result = MvFunc.display_params({"destination": "/tmp/b"})
        assert "/tmp/b" in result

    def test_empty_destination(self):
        """destination 为空"""
        result = MvFunc.display_params({"source": "/tmp/a"})
        assert "/tmp/a" in result

    def test_sanitize_newline(self):
        """路径含换行符被转义"""
        result = MvFunc.display_params({
            "source": "/tmp/a\n.txt", "destination": "/tmp/b.txt",
        })
        assert "a/n.txt" in result

    def test_default_max_len(self):
        """默认 max_len=80"""
        result = MvFunc.display_params({
            "source": "/tmp/a.txt", "destination": "/tmp/b.txt",
        })
        assert len(result) <= 80


# ═══════════════════════════════════════════════════════════════════════════
# 4. MvFunc.execute
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncExecuteFile:
    """MvFunc.execute — 文件移动"""

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_move_file_to_new_dest(self, mock_record, tmp_path):
        """移动文件到不存在的目标"""
        src = tmp_path / "source.txt"
        src.write_text("hello world")
        dst = tmp_path / "dest.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.execute()

        assert result.startswith("移动成功")
        assert dst.read_text() == "hello world"
        assert not src.exists()  # 源文件已移除
        # 沙盒记录：源文件 + 目标文件
        assert mock_record.await_count == 2

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_move_file_overwrite_existing(self, mock_record, tmp_path):
        """覆盖已存在的目标文件"""
        src = tmp_path / "source.txt"
        src.write_text("new content")
        dst = tmp_path / "dest.txt"
        dst.write_text("old content")

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.execute()

        assert result.startswith("覆盖成功")
        assert dst.read_text() == "new content"
        assert not src.exists()
        # 沙盒记录 2 次：源删除 + 目标覆盖
        assert mock_record.await_count == 2

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_move_file_fail_parent_missing(self, mock_record, tmp_path):
        """目标父目录不存在时 shutil.move 失败并返回错误"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "subdir" / "deep" / "dest.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.execute()

        # MvFunc 不自动创建父目录，shutil.move 会失败
        assert "移动失败" in result
        assert not dst.exists()
        assert src.exists()  # 源文件未被移动

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_move_file_symlink(self, mock_record, tmp_path):
        """移动符号链接（移动链接本身）"""
        target = tmp_path / "target.txt"
        target.write_text("target content")
        link = tmp_path / "mylink.lnk"
        link.symlink_to(target)

        dst = tmp_path / "moved_link.lnk"

        mv = MvFunc(source=str(link), destination=str(dst))
        result = await mv.execute()

        assert result.startswith("移动成功")
        assert dst.is_symlink()
        assert not link.exists()
        # 符号链接指向原目标
        assert os.readlink(str(dst)) == str(target)

    async def test_source_not_exists(self, tmp_path):
        """源路径不存在返回错误"""
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "out.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.execute()

        assert "源路径不存在" in result

    @patch("src.tools.mv.async_file_exists", new_callable=AsyncMock)
    async def test_samefile_returns_message(self, mock_exists, tmp_path):
        """源和目标相同路径时返回提示"""
        src = tmp_path / "same.txt"
        dst = tmp_path / "same.txt"

        # 让 async_file_exists 对两者都返回 True
        mock_exists.return_value = True

        with patch("asyncio.to_thread") as mock_to_thread:
            def side_effect(func, *args):
                if func is os.path.samefile:
                    return True
                return func(*args)
            mock_to_thread.side_effect = side_effect

            mv = MvFunc(source=str(src), destination=str(dst))
            result = await mv.execute()

            assert "源和目标路径相同" in result

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_permission_error_caught(self, mock_record, tmp_path):
        """权限不足被捕获"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        with patch("shutil.move", side_effect=PermissionError("权限不足")):
            mv = MvFunc(source=str(src), destination=str(dst))
            result = await mv.execute()

            assert "权限不足" in result

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_os_error_caught(self, mock_record, tmp_path):
        """OSError 被捕获"""
        src = tmp_path / "source.txt"
        src.write_text("content")
        dst = tmp_path / "dest.txt"

        with patch("shutil.move", side_effect=OSError("设备繁忙")):
            mv = MvFunc(source=str(src), destination=str(dst))
            result = await mv.execute()

            assert "移动失败" in result


class TestMvFuncExecuteDirectory:
    """MvFunc.execute — 目录移动"""

    @patch("src.tools.mv.async_record_directory_files", new_callable=AsyncMock)
    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    @patch("src.tools.mv.async_collect_files")
    async def test_move_dir_to_new_dest(
        self, mock_collect, mock_record_sandbox, mock_record_dir, tmp_path,
    ):
        """移动目录到不存在的目标"""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("aaa")
        sub = src_dir / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("bbb")
        dst_dir = tmp_path / "dst_dir"

        file_list = [str(src_dir / "a.txt"), str(sub / "b.txt")]
        mock_collect.return_value = file_list

        mv = MvFunc(source=str(src_dir), destination=str(dst_dir))
        result = await mv.execute()

        assert result.startswith("移动成功")
        assert "2个文件" in result
        assert (dst_dir / "a.txt").exists()
        assert (dst_dir / "sub" / "b.txt").exists()
        assert not src_dir.exists()  # 源已移动
        mock_record_dir.assert_awaited_once()

    @patch("src.tools.mv.async_record_directory_files", new_callable=AsyncMock)
    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    @patch("src.tools.mv.async_collect_files")
    async def test_move_dir_overwrite_existing(
        self, mock_collect, mock_record_sandbox, mock_record_dir, tmp_path,
    ):
        """覆盖已存在的目标目录"""
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "new.txt").write_text("new")
        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        (dst_dir / "old.txt").write_text("old")

        file_list = [str(src_dir / "new.txt")]
        mock_collect.return_value = file_list

        mv = MvFunc(source=str(src_dir), destination=str(dst_dir))
        result = await mv.execute()

        assert result.startswith("移动成功")
        # shutil.move 将源目录移入已存在的目标目录内部
        # 所以 new.txt 位于 dst_dir/src_dir/new.txt
        assert (dst_dir / "src_dir" / "new.txt").exists()
        assert not src_dir.exists()

    async def test_move_dir_source_not_exists(self, tmp_path):
        """目录源不存在时返回错误"""
        src_dir = tmp_path / "nonexistent_dir"
        dst_dir = tmp_path / "dst_dir"

        mv = MvFunc(source=str(src_dir), destination=str(dst_dir))
        result = await mv.execute()

        assert "源路径不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. MvFunc.display
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncDisplay:
    """MvFunc.display 打印 + 执行"""

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display(self, mock_record, tmp_path):
        """移动成功时返回结果字符串"""
        src = tmp_path / "src.txt"
        src.write_text("hello")
        dst = tmp_path / "dst.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.display()

        assert result.startswith("移动成功")
        # 源文件应已被移动
        assert not src.exists()
        assert dst.exists()

    async def test_fail_display(self, tmp_path):
        """移动失败时返回错误信息"""
        src = tmp_path / "nonexistent.txt"
        dst = tmp_path / "out.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.display()

        assert "源路径不存在" in result

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_success_display_returns_result(self, mock_record, tmp_path):
        """display 返回 execute 结果"""
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"

        mv = MvFunc(source=str(src), destination=str(dst))
        result = await mv.display()

        assert "移动成功" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. MvFunc — from_args + execute 集成
# ═══════════════════════════════════════════════════════════════════════════

class TestMvFuncIntegration:
    """MvFunc from_args + execute 集成"""

    @patch("src.tools.mv.async_record_sandbox", new_callable=AsyncMock)
    async def test_from_args_then_execute(self, mock_record, tmp_path):
        """from_args 创建的实例可以正常执行"""
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "dst.txt"

        mv = MvFunc.from_args({
            "source": str(src), "destination": str(dst),
        })
        result = await mv.execute()

        assert result.startswith("移动成功")
        assert dst.read_text() == "data"
        assert not src.exists()


# ═══════════════════════════════════════════════════════════════════════════
# 7. MvFunc + SandboxManager 回归测试 — mv 目录后沙盒还原
# ═══════════════════════════════════════════════════════════════════════════

class TestMvDirSandboxRestore:
    """MvFunc 目录移动 → SandboxManager.restore_to_message 回归测试

    验证 Bug 修复：mv 目录时在 shutil.move 之后读取源文件内容导致
    content=None，沙盒记录为 (None, None)，undo 时无法还原源文件。
    """

    @pytest.fixture
    def sandbox(self):
        """创建沙盒管理器并设置为全局实例"""
        from src.core.sandbox_manager import (
            SandboxManager, set_sandbox_manager, get_sandbox_manager,
        )
        sm = SandboxManager(max_history_per_file=100)
        set_sandbox_manager(sm)
        yield sm
        set_sandbox_manager(None)

    @pytest.mark.asyncio
    async def test_mv_dir_undo_restores_source_files(
        self, sandbox, tmp_path,
    ):
        """mv 目录到新位置 → undo → 源目录及文件正确还原，目标路径恢复为空

        场景：
          msg_idx=0: （初始状态）src_dir/ 存在，dst_dir/ 不存在
          msg_idx=1: mv src_dir/ → dst_dir/
          undo 回 msg_idx=0 → src_dir/ 恢复，dst_dir/ 消失
        """
        # ── Arrange ──
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("content_a")
        sub = src_dir / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("content_b")

        sandbox._update_current_index(1)  # 模拟当前消息索引

        mv = MvFunc(
            source=str(src_dir),
            destination=str(tmp_path / "dst_dir"),
        )

        # ── Act ──
        result = await mv.execute()

        # ── Assert: 移动成功 ──
        assert result.startswith("移动成功")

        # ── Assert: 沙盒记录了正确的内容 ──
        fh = sandbox.file_history
        src_a_key = str(src_dir / "a.txt")
        src_b_key = str(sub / "b.txt")

        assert src_a_key in fh, "源文件 a.txt 应被沙盒记录"
        assert src_b_key in fh, "源文件 b.txt 应被沙盒记录"

        # 源文件记录：最后一条应为 (content, None)，表示文件存在后被删除
        a_last = fh[src_a_key][-1]
        assert a_last.content_before == "content_a", (
            f"a.txt content_before 应为 'content_a'，实际为 {a_last.content_before!r}"
        )
        assert a_last.content_after is None, "a.txt content_after 应为 None（已删除）"

        b_last = fh[src_b_key][-1]
        assert b_last.content_before == "content_b", (
            f"b.txt content_before 应为 'content_b'，实际为 {b_last.content_before!r}"
        )
        assert b_last.content_after is None, "b.txt content_after 应为 None（已删除）"

        # 源目录自身记录：content_before=""（目录存在），content_after=None（被删除）
        src_dir_key = str(src_dir)
        assert src_dir_key in fh, "源目录自身应被沙盒记录"
        dir_last = fh[src_dir_key][-1]
        assert dir_last.content_before == "", (
            f"源目录 content_before 应为 ''，实际为 {dir_last.content_before!r}"
        )
        assert dir_last.content_after is None
        assert dir_last.record_type == "directory"

        # 目标文件记录：content_after 应为实际内容
        dst_a_key = str(tmp_path / "dst_dir" / "a.txt")
        dst_b_key = str(tmp_path / "dst_dir" / "sub" / "b.txt")

        assert dst_a_key in fh, "目标文件 a.txt 应被沙盒记录"
        assert dst_b_key in fh, "目标文件 b.txt 应被沙盒记录"

        dst_a_last = fh[dst_a_key][-1]
        assert dst_a_last.content_after == "content_a", (
            f"目标 a.txt content_after 应为 'content_a'，实际为 {dst_a_last.content_after!r}"
        )

        dst_b_last = fh[dst_b_key][-1]
        assert dst_b_last.content_after == "content_b", (
            f"目标 b.txt content_after 应为 'content_b'，实际为 {dst_b_last.content_after!r}"
        )

        # ── Act: undo 回 msg_idx=0 ──
        results = sandbox.restore_to_message(0)

        # ── Assert: 源文件恢复 ──
        assert results.get(src_a_key, False) is True
        assert results.get(src_b_key, False) is True

        assert (src_dir / "a.txt").exists(), "源 a.txt 应被恢复"
        assert (src_dir / "a.txt").read_text() == "content_a"
        assert (sub / "b.txt").exists(), "源 b.txt 应被恢复"
        assert (sub / "b.txt").read_text() == "content_b"

        # ── Assert: 目标目录内的文件已清理 ──
        # 注：mv 操作未记录隐式创建的目标子目录（dst_dir/sub/），
        # 这些空目录在 undo 后可能残留。但所有文件内容应已正确清理。
        assert not (tmp_path / "dst_dir" / "a.txt").exists(), "目标 a.txt 应不存在"
        assert not (tmp_path / "dst_dir" / "sub" / "b.txt").exists(), "目标 b.txt 应不存在"

    @pytest.mark.asyncio
    async def test_mv_dir_sandbox_content_correct(
        self, sandbox, tmp_path,
    ):
        """mv 目录（覆盖已有目标）→ 沙盒记录内容正确

        关键回归点：修复前 mv 目录时在 shutil.move 之后读源文件，
        content 为 None，导致沙盒记录 (None, None)，undo 无法还原。
        修复后 content 应为实际文件内容。
        """
        # ── Arrange ──
        src_dir = tmp_path / "src_dir"
        src_dir.mkdir()
        (src_dir / "new.txt").write_text("new_content")

        dst_dir = tmp_path / "dst_dir"
        dst_dir.mkdir()
        (dst_dir / "old.txt").write_text("old_content")

        sandbox._update_current_index(1)

        mv = MvFunc(source=str(src_dir), destination=str(dst_dir))

        # ── Act ──
        result = await mv.execute()

        # ── Assert: 移动成功 ──
        assert result.startswith("移动成功")

        # ── Assert: 沙盒记录中源文件内容正确（关键回归点） ──
        fh = sandbox.file_history
        src_new_key = str(src_dir / "new.txt")

        assert src_new_key in fh, "源文件应被沙盒记录"
        src_last = fh[src_new_key][-1]
        assert src_last.content_before == "new_content", (
            f"源文件 content_before 应为 'new_content'，实际为 {src_last.content_before!r}"
        )
        assert src_last.content_after is None

        # 目标文件记录：content_after 应为实际内容（关键回归点）
        # 注：shutil.move(src_dir, dst_dir) 在 dst_dir 存在时会将 src_dir
        # 移入 dst_dir 内部（Linux 行为），沙盒按 relpath 记录目标路径。
        dst_new_key = str(dst_dir / "new.txt")
        assert dst_new_key in fh, "目标文件应被沙盒记录"
        dst_last = fh[dst_new_key][-1]
        assert dst_last.content_after == "new_content", (
            f"目标文件 content_after 应为 'new_content'，实际为 {dst_last.content_after!r}"
        )
