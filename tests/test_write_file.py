"""测试 WriteFileFunc

测试策略
--------
- 使用 tmp_path 隔离文件系统操作
- mock async_record_file_change_from_context 避免依赖沙盒
- 遵循 Arrange/Act/Assert 模式
- 每个测试类关注一个概念，每个方法覆盖单一场景
"""

import os
from unittest.mock import patch, AsyncMock

import pytest

from src.tools.write_file import WriteFileFunc
from src.tools.file_base import PathSecurityError, FileSizeError


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """__init__ 路径存储和内容大小检查。"""

    def test_valid_path(self, tmp_path):
        f = tmp_path / "test.txt"
        wf = WriteFileFunc(str(f), "hello")
        assert wf.path == str(f)
        assert wf.content == "hello"

    def test_content_too_large_raises(self):
        """内容超过 100MB 应拒绝（_check_content_size 纯计算，保留在 __init__ 中）"""
        large = "x" * (100 * 1024 * 1024 + 1)
        with pytest.raises(FileSizeError):
            WriteFileFunc("/tmp/test.txt", large)

    def test_content_on_boundary(self):
        """内容恰好 100MB 应通过"""
        # 100MB = 100 * 1024 * 1024 bytes
        large = "x" * (100 * 1024 * 1024)
        # 'x' 编码为 utf-8 是 1 字节
        wf = WriteFileFunc("/tmp/test_boundary.txt", large)
        assert wf.content == large


# ═══════════════════════════════════════════════════════════════════════════
# 2. _get_new_content
# ═══════════════════════════════════════════════════════════════════════════

class TestGetNewContent:
    """_get_new_content 返回构造函数传入的 content"""

    @pytest.mark.asyncio
    async def test_returns_content(self, tmp_path):
        f = tmp_path / "test.txt"
        wf = WriteFileFunc(str(f), "new content here")
        result = await wf._get_new_content()
        assert result == "new content here"

    @pytest.mark.asyncio
    async def test_empty_content(self, tmp_path):
        f = tmp_path / "test.txt"
        wf = WriteFileFunc(str(f), "")
        result = await wf._get_new_content()
        assert result == ""


# ═══════════════════════════════════════════════════════════════════════════
# 3. execute（含 _atomic_write / _read_original 集成）
# ═══════════════════════════════════════════════════════════════════════════

class TestExecute:
    """execute 写文件操作（沙盒已 mock）"""

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
        """危险路径在 execute() 时抛出 PathSecurityError"""
        wf = WriteFileFunc("/etc/passwd", "content")
        result = await wf.execute()
        assert "写入失败" in result
        assert "不允许写入系统关键文件" in result

    @pytest.mark.asyncio
    async def test_dos_device_name_raises_on_execute(self):
        """Windows DOS 设备名在 execute() 时抛出 PathSecurityError"""
        wf = WriteFileFunc("/tmp/CON.txt", "content")
        result = await wf.execute()
        assert "写入失败" in result
        assert "DOS" in result or "CON" in result

    # ── 正常写入 ──

    @pytest.mark.asyncio
    async def test_create_new_file(self, tmp_path, _mock_sandbox):
        """新建文件"""
        f = tmp_path / "new.txt"
        assert not os.path.exists(f)

        wf = WriteFileFunc(str(f), "hello world")
        result = await wf.execute()

        assert "写入成功" in result
        assert os.path.exists(f)
        assert f.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_overwrite_existing_file(self, tmp_path, _mock_sandbox):
        """覆盖已有文件"""
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        wf = WriteFileFunc(str(f), "new content")
        result = await wf.execute()

        assert "写入成功" in result
        assert f.read_text() == "new content"

    @pytest.mark.asyncio
    async def test_auto_create_parent_directory(self, tmp_path, _mock_sandbox):
        """父目录不存在时自动创建"""
        nested = tmp_path / "a" / "b" / "c" / "nested.txt"
        assert not nested.parent.exists()

        wf = WriteFileFunc(str(nested), "deep content")
        result = await wf.execute()

        assert "写入成功" in result
        assert nested.parent.exists()
        assert nested.read_text() == "deep content"

    @pytest.mark.asyncio
    async def test_write_empty_content(self, tmp_path, _mock_sandbox):
        """写入空内容"""
        f = tmp_path / "empty.txt"

        wf = WriteFileFunc(str(f), "")
        result = await wf.execute()

        assert "写入成功" in result
        assert f.read_text() == ""

    @pytest.mark.asyncio
    async def test_large_content(self, tmp_path, _mock_sandbox):
        """写入较大内容（~1MB）"""
        f = tmp_path / "large.txt"
        content = "x" * (1024 * 1024)  # 1MB

        wf = WriteFileFunc(str(f), content)
        result = await wf.execute()

        assert "写入成功" in result
        assert f.read_text() == content

    @pytest.mark.asyncio
    async def test_result_contains_lines_and_size(self, tmp_path, _mock_sandbox):
        """返回结果含行数和字节数"""
        f = tmp_path / "stats.txt"
        content = "line1\nline2\nline3\n"

        wf = WriteFileFunc(str(f), content)
        result = await wf.execute()

        # "写入成功 3L 15B" — 3 lines, 15 bytes
        assert "写入成功" in result
        assert "L" in result
        assert "B" in result


# ═══════════════════════════════════════════════════════════════════════════
# 4. display
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplay:
    """display 显示 diff 预览并执行写入"""

    @pytest.fixture
    def _mock_sandbox(self):
        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_display_new_file_returns_success(self, tmp_path, _mock_sandbox):
        f = tmp_path / "new.txt"
        wf = WriteFileFunc(str(f), "hello world")
        result = await wf.display()

        assert "写入成功" in result
        assert f.read_text() == "hello world"

    @pytest.mark.asyncio
    async def test_display_overwrite_file_returns_success(self, tmp_path, _mock_sandbox):
        f = tmp_path / "existing.txt"
        f.write_text("old content")

        wf = WriteFileFunc(str(f), "new content")
        result = await wf.display()

        assert "写入成功" in result
        assert f.read_text() == "new content"


# ═══════════════════════════════════════════════════════════════════════════
# 5. to_tool_schema
# ═══════════════════════════════════════════════════════════════════════════

class TestToToolSchema:
    """to_tool_schema 返回正确的 schema 格式"""

    def test_schema_structure(self):
        schema = WriteFileFunc.to_tool_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "write_file"
        props = schema["function"]["parameters"]["properties"]
        assert "path" in props
        assert "content" in props
        assert schema["function"]["parameters"]["required"] == ["path", "content"]
        assert props["path"]["type"] == "string"
        assert props["content"]["type"] == "string"


# ═══════════════════════════════════════════════════════════════════════════
# 6. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要显示"""

    def test_shows_path(self):
        result = WriteFileFunc.display_params({"path": "/tmp/test.txt"})
        assert "/tmp/test.txt" in result

    def test_empty_args(self):
        result = WriteFileFunc.display_params({})
        assert result == "''"


# ═══════════════════════════════════════════════════════════════════════════
# 7. _success_verb / _mode_desc
# ═══════════════════════════════════════════════════════════════════════════

class TestMeta:
    """工具元数据方法"""

    def test_success_verb(self, tmp_path):
        wf = WriteFileFunc(str(tmp_path / "x.txt"), "")
        assert wf._success_verb() == "写入成功"

    def test_mode_desc(self, tmp_path):
        wf = WriteFileFunc(str(tmp_path / "x.txt"), "")
        assert wf._mode_desc() == "覆盖写入整个文件"


# ═══════════════════════════════════════════════════════════════════════════
# 8. plan agent 路径白名单校验
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanAgentPathRestriction:
    """plan agent write_file/update_file 只能写入 .chat/plan/ 目录。"""

    @pytest.fixture
    def _mock_sandbox(self):
        with patch(
            "src.tools.file_base.async_record_file_change_from_context",
            AsyncMock(),
        ):
            yield

    @pytest.mark.asyncio
    async def test_plan_agent_writes_to_chat_plan_succeeds(self, tmp_path, _mock_sandbox, monkeypatch):
        """plan agent 写入 .chat/plan/ 下的文件应成功。"""
        # 将 cwd 临时设置为 tmp_path，并创建 .chat/plan/ 目录
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)

        plan_file = plan_dir / "test_plan.md"
        wf = WriteFileFunc(str(plan_file), "# Plan Content")
        wf.agent_type = "plan"
        result = await wf.execute()

        assert "写入成功" in result
        assert plan_file.read_text() == "# Plan Content"

    @pytest.mark.asyncio
    async def test_plan_agent_writes_outside_chat_plan_fails(self, tmp_path, monkeypatch):
        """plan agent 写入 .chat/plan/ 外的文件应失败。"""
        monkeypatch.chdir(tmp_path)
        # 确保 .chat/plan/ 存在
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)

        outside_file = tmp_path / "outside.md"
        wf = WriteFileFunc(str(outside_file), "content")
        wf.agent_type = "plan"
        result = await wf.execute()

        assert "写入失败" in result
        assert "plan agent" in result or ".chat/plan/" in result

    @pytest.mark.asyncio
    async def test_plan_agent_path_traversal_blocked(self, tmp_path, monkeypatch):
        """plan agent 路径穿越被阻止（../ 绕过）。"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)

        # 尝试用 ../ 穿越到外部目录
        traversal_path = str(plan_dir / ".." / ".." / "etc" / "passwd")
        wf = WriteFileFunc(traversal_path, "malicious")
        wf.agent_type = "plan"
        result = await wf.execute()

        assert "写入失败" in result

    @pytest.mark.asyncio
    async def test_plan_agent_writes_to_chat_plan_root_succeeds(self, tmp_path, _mock_sandbox, monkeypatch):
        """plan agent 直接写入 .chat/plan/ 目录路径本身（目录写入会被底层拒绝，但校验应通过）。"""
        monkeypatch.chdir(tmp_path)
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)

        # 写入 .chat/plan/ 根下的文件
        plan_root_file = plan_dir / "index.md"
        wf = WriteFileFunc(str(plan_root_file), "# Index")
        wf.agent_type = "plan"
        result = await wf.execute()

        assert "写入成功" in result
        assert plan_root_file.read_text() == "# Index"

    @pytest.mark.asyncio
    async def test_plan_agent_subdir_in_chat_plan_succeeds(self, tmp_path, _mock_sandbox, monkeypatch):
        """plan agent 写入 .chat/plan/subdir/ 下的文件应成功。"""
        monkeypatch.chdir(tmp_path)
        plan_subdir = tmp_path / ".chat" / "plan" / "archive"
        plan_subdir.mkdir(parents=True)

        plan_file = plan_subdir / "archived_plan.md"
        wf = WriteFileFunc(str(plan_file), "# Archived")
        wf.agent_type = "plan"
        result = await wf.execute()

        assert "写入成功" in result

    @pytest.mark.asyncio
    async def test_non_plan_agent_no_restriction(self, tmp_path, _mock_sandbox, monkeypatch):
        """非 plan agent（agent_type=None 或 ordinary）无路径限制。"""
        monkeypatch.chdir(tmp_path)

        # agent_type 为 None
        outside_file = tmp_path / "no_restrict.md"
        wf = WriteFileFunc(str(outside_file), "content")
        wf.agent_type = None
        result = await wf.execute()
        assert "写入成功" in result

        # agent_type 为 ordinary
        outside_file2 = tmp_path / "no_restrict2.md"
        wf2 = WriteFileFunc(str(outside_file2), "content")
        wf2.agent_type = "ordinary"
        result = await wf2.execute()
        assert "写入成功" in result

    @pytest.mark.asyncio
    async def test_plan_agent_chat_plan_not_exist_fails(self, tmp_path, monkeypatch):
        """.chat/plan/ 目录不存在时 plan agent 写入也应失败（路径不在白名单内）。"""
        monkeypatch.chdir(tmp_path)
        # 不创建 .chat/plan/ 目录

        outside_file = tmp_path / "some_file.md"
        wf = WriteFileFunc(str(outside_file), "content")
        wf.agent_type = "plan"
        result = await wf.execute()

        # 即使 .chat/plan/ 不存在，写入外部路径也应被拒绝
        assert "写入失败" in result
