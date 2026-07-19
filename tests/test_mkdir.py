"""测试 src.tools.mkdir：MkdirFunc — 创建目录

测试策略
--------
- 用 tmp_path 隔离实际文件操作
- 沙盒记录在无 SandboxManager 时静默返回 None（无需 mock）
- 需验证沙盒调用时 patch async_makedirs_and_record
- 实际目录创建（os.makedirs）在 tmp_path 上真实执行
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖一个场景
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.tools.mkdir import MkdirFunc


# ═══════════════════════════════════════════════════════════════════════════
# 1. MkdirFunc.__init__
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncInit:
    """MkdirFunc.__init__ 路径安全校验"""

    def test_valid_path_default_parents(self, tmp_path):
        """合法路径，parents 默认为 False"""
        p = tmp_path / "new_dir"
        mk = MkdirFunc(path=str(p))
        assert mk.path == str(p)
        assert mk.parents is False

    def test_valid_path_with_parents(self, tmp_path):
        """合法路径，parents=True"""
        p = tmp_path / "a" / "b" / "c"
        mk = MkdirFunc(path=str(p), parents=True)
        assert mk.parents is True

    def test_path_traversal_raises(self):
        """路径穿越（/etc/passwd）应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            MkdirFunc(path="/etc/passwd")

    def test_device_file_raises(self):
        """设备文件路径应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            MkdirFunc(path="/dev/null")


# ═══════════════════════════════════════════════════════════════════════════
# 2. MkdirFunc.from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncFromArgs:
    """MkdirFunc.from_args 参数解析"""

    def test_required_params(self):
        """仅必需参数 path"""
        mk = MkdirFunc.from_args({"path": "/tmp/new_dir"})
        assert mk.path == "/tmp/new_dir"
        assert mk.parents is False

    def test_with_parents_true(self):
        """含 parents=True"""
        mk = MkdirFunc.from_args({"path": "/tmp/a/b", "parents": True})
        assert mk.parents is True

    def test_with_parents_false(self):
        """含 parents=False"""
        mk = MkdirFunc.from_args({"path": "/tmp/d", "parents": False})
        assert mk.parents is False

    def test_extra_params_ignored(self):
        """额外参数被忽略"""
        mk = MkdirFunc.from_args({"path": "/tmp/d", "extra": "x"})
        assert mk.path == "/tmp/d"

    def test_missing_path_raises(self):
        """缺少 path 抛出 ValueError"""
        with pytest.raises(ValueError, match="缺少必需参数"):
            MkdirFunc.from_args({})


# ═══════════════════════════════════════════════════════════════════════════
# 3. MkdirFunc.display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncDisplayParams:
    """MkdirFunc.display_params 参数摘要"""

    def test_basic(self):
        """基本参数摘要"""
        result = MkdirFunc.display_params({"path": "/tmp/new_dir"})
        assert "/tmp/new_dir" in result
        assert "-p" not in result

    def test_with_parents(self):
        """含 -p 标志"""
        result = MkdirFunc.display_params({"path": "/tmp/a/b", "parents": True})
        assert "-p" in result

    def test_empty_path(self):
        """path 为空"""
        result = MkdirFunc.display_params({})
        assert isinstance(result, str)

    def test_sanitize_newline(self):
        """路径含换行符被转义"""
        result = MkdirFunc.display_params({"path": "/tmp/a\n_dir"})
        assert "a/n_dir" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. MkdirFunc.execute
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncExecuteCreate:
    """MkdirFunc.execute — 创建新目录

    注：execute() 现在通过 async_makedirs_and_record 统一处理目录创建和沙盒记录。
    在没有 SandboxManager 的测试环境中，沙盒记录静默返回 None，不会报错。
    """

    async def test_create_single_dir(self, tmp_path):
        """创建单层目录"""
        d = tmp_path / "new_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()

    @patch("src.tools.file_ops.async_makedirs_and_record", new_callable=AsyncMock)
    async def test_create_single_dir_calls_makedirs_and_record(self, mock_mkrec, tmp_path):
        """创建单层目录时调用 async_makedirs_and_record(path, 'mkdir')"""
        d = tmp_path / "new_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        mock_mkrec.assert_awaited_once_with(str(d), "mkdir")

    async def test_create_nested_dir_with_parents_true(self, tmp_path):
        """parents=True 递归创建多层目录"""
        d = tmp_path / "a" / "b" / "c"

        mk = MkdirFunc(path=str(d), parents=True)
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()
        assert (tmp_path / "a").is_dir()
        assert (tmp_path / "a" / "b").is_dir()

    async def test_create_nested_dir_with_parents_false(self, tmp_path):
        """parents=False 父目录不存在时返回提示"""
        d = tmp_path / "a" / "b" / "c"

        mk = MkdirFunc(path=str(d), parents=False)
        result = await mk.execute()

        assert "父目录不存在，如需递归创建请设置 parents=True" in result
        assert not d.exists()

    async def test_create_dir_parent_exists(self, tmp_path):
        """父目录已存在时正常创建"""
        parent = tmp_path / "parent"
        parent.mkdir()
        d = parent / "child"

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()

    async def test_create_dir_root_level(self, tmp_path):
        """在 tmp_path 根目录创建（父目录为空字符串）"""
        d = tmp_path / "root_level_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()


class TestMkdirFuncExecuteExists:
    """MkdirFunc.execute — 目录已存在/路径冲突"""

    async def test_dir_already_exists(self, tmp_path):
        """目录已存在时返回'目录已存在'"""
        d = tmp_path / "existing_dir"
        d.mkdir()

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        assert result == f"目录已存在: {d}"
        # 目录仍存在
        assert d.is_dir()

    async def test_path_is_existing_file(self, tmp_path):
        """路径是已存在的文件时返回提示"""
        f = tmp_path / "existing_file.txt"
        f.write_text("I am a file")

        mk = MkdirFunc(path=str(f))
        result = await mk.execute()

        assert "已存在且不是目录" in result
        assert f.is_file()  # 文件未被修改

    async def test_dir_already_exists_empty_string(self, tmp_path):
        """已存在返回不带前缀的准确消息"""
        d = tmp_path / "existing"
        d.mkdir()

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        assert result == f"目录已存在: {d}"


class TestMkdirFuncExecuteErrors:
    """MkdirFunc.execute — 异常处理"""

    async def test_permission_error_caught(self, tmp_path):
        """权限不足被捕获"""
        d = tmp_path / "no_permission_dir"

        with patch("os.makedirs", side_effect=PermissionError("权限不足")):
            mk = MkdirFunc(path=str(d))
            result = await mk.execute()

            assert "权限不足" in result

    async def test_os_error_caught(self, tmp_path):
        """OSError 被捕获"""
        d = tmp_path / "error_dir"

        with patch("os.makedirs", side_effect=OSError("磁盘已满")):
            mk = MkdirFunc(path=str(d))
            result = await mk.execute()

            assert "创建失败" in result

    async def test_value_error_from_validation(self, tmp_path):
        """path security validation 抛出 ValueError 被捕获"""
        d = tmp_path / "valid_path"

        mk = MkdirFunc(path=str(d))
        with patch("src.tools.mkdir.validate_path_security", side_effect=ValueError("拒绝访问")):
            result = await mk.execute()

            assert "创建失败" in result

    async def test_generic_exception_caught(self, tmp_path):
        """通用异常被捕获并记录日志"""
        d = tmp_path / "error_dir"

        with patch("os.makedirs", side_effect=RuntimeError("未知错误")):
            mk = MkdirFunc(path=str(d))
            result = await mk.execute()

            assert "创建失败" in result

    @patch("src.tools.file_ops.async_makedirs_and_record",
           side_effect=RuntimeError("沙盒异常"))
    async def test_sandbox_failure_does_not_block(self, mock_mkrec, tmp_path):
        """沙盒记录失败仍会返回错误（异常被上层 except Exception 捕获）"""
        d = tmp_path / "sandbox_fail_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.execute()

        # 异常被 except Exception 捕获，返回错误消息
        assert "创建失败" in result
        # async_makedirs_and_record 失败后目录未被创建
        mock_mkrec.assert_awaited_once_with(str(d), "mkdir")


# ═══════════════════════════════════════════════════════════════════════════
# 5. MkdirFunc.display
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncDisplay:
    """MkdirFunc.display 打印 + 执行"""

    async def test_create_success_display(self, tmp_path):
        """创建成功时返回结果字符串"""
        d = tmp_path / "new_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.display()

        assert result.startswith("创建成功")
        assert d.is_dir()

    async def test_fail_display(self, tmp_path):
        """创建失败时返回错误信息"""
        d = tmp_path / "a" / "b"  # 父目录不存在，parents=False

        mk = MkdirFunc(path=str(d))
        result = await mk.display()

        assert "父目录不存在" in result

    async def test_dir_exists_display(self, tmp_path):
        """目录已存在时返回结果字符串"""
        d = tmp_path / "existing"
        d.mkdir()

        mk = MkdirFunc(path=str(d))
        result = await mk.display()

        assert result.startswith("目录已存在")

    async def test_success_display_returns_result(self, tmp_path):
        """display 返回 execute 结果"""
        d = tmp_path / "new_dir"

        mk = MkdirFunc(path=str(d))
        result = await mk.display()

        assert result.startswith("创建成功")

    async def test_parents_flag_in_display_output(self, tmp_path):
        """display 操作描述包含 -p 标志"""
        d = tmp_path / "a" / "b"
        mk = MkdirFunc(path=str(d), parents=True)
        # display() 通过 _publish_tool_text 发布操作描述到 EventBus
        with patch("src.tools.base.Func._publish_tool_text") as mock_publish:
            with patch.object(mk, "execute", new_callable=AsyncMock, return_value="创建成功"):
                await mk.display()

        published_texts = [call[0][0] for call in mock_publish.call_args_list]
        assert any("-p" in text for text in published_texts), f"未在发布文本中找到 -p: {published_texts}"


# ═══════════════════════════════════════════════════════════════════════════
# 6. MkdirFunc — from_args + execute 集成
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncIntegration:
    """MkdirFunc from_args + execute 集成"""

    async def test_from_args_then_execute(self, tmp_path):
        """from_args 创建的实例可以正常执行"""
        d = tmp_path / "integ_dir"

        mk = MkdirFunc.from_args({"path": str(d)})
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()

    async def test_from_args_with_parents(self, tmp_path):
        """from_args 含 parents=True"""
        d = tmp_path / "nested" / "dir" / "deep"

        mk = MkdirFunc.from_args({"path": str(d), "parents": True})
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert d.is_dir()


# ═══════════════════════════════════════════════════════════════════════════
# 7. plan agent 路径白名单校验
# ═══════════════════════════════════════════════════════════════════════════

class TestMkdirFuncPlanAgentPathWhitelist:
    """plan agent 使用 mkdir 时限制在 .chat/plan/ 目录下"""

    async def test_plan_agent_mkdir_chat_plan_succeeds(self, tmp_path, monkeypatch):
        """plan agent 在 .chat/plan/ 下创建目录 → 允许"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"

        mk = MkdirFunc(path=str(plan_dir), parents=True)
        mk.agent_type = "plan"
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert plan_dir.is_dir()

    async def test_plan_agent_mkdir_subdir_succeeds(self, tmp_path, monkeypatch):
        """plan agent 在 .chat/plan/subdir/ 下创建目录 → 允许"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        subdir = plan_dir / "archive"

        mk = MkdirFunc(path=str(subdir))
        mk.agent_type = "plan"
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert subdir.is_dir()

    async def test_plan_agent_mkdir_outside_fails(self, tmp_path, monkeypatch):
        """plan agent 在 .chat/plan/ 外创建目录 → 拒绝"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        outside = tmp_path / "outside_dir"

        mk = MkdirFunc(path=str(outside))
        mk.agent_type = "plan"
        result = await mk.execute()

        assert "创建失败" in result
        assert "plan agent" in result
        assert not outside.exists()

    async def test_plan_agent_mkdir_traversal_blocked(self, tmp_path, monkeypatch):
        """plan agent mkdir 路径穿越 → 拒绝"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)

        traversal_path = str(plan_dir / ".." / ".." / "outside")
        mk = MkdirFunc(path=traversal_path)
        mk.agent_type = "plan"
        result = await mk.execute()

        assert "创建失败" in result

    async def test_non_plan_agent_no_restriction(self, tmp_path, monkeypatch):
        """非 plan agent 使用 mkdir 无路径限制"""
        monkeypatch.chdir(tmp_path)

        outside = tmp_path / "outside_dir"
        mk = MkdirFunc(path=str(outside))
        mk.agent_type = "execute"
        result = await mk.execute()

        assert result.startswith("创建成功")
        assert outside.is_dir()
