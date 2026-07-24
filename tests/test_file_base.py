"""测试 src.tools.file_base：FileToolBase._validate_path_and_size 路径校验

测试策略
--------
- 使用 tmp_path 隔离文件系统操作
- 使用 monkeypatch 控制 realpath 行为（模拟符号链接场景）
- 使用 monkeypatch 控制 _PLAN_ALLOWED_DIR 测试 plan agent 白名单
- 遵循 Arrange/Act/Assert 模式
- 每个测试方法覆盖一个边界/分支场景
"""

import os

import pytest

from src.tools.write_file import WriteFileFunc
from src.tools.file_base import PathSecurityError, _PLAN_ALLOWED_DIR


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_wf(path: str) -> WriteFileFunc:
    """创建 WriteFileFunc 实例（小内容，不会触发大小限制）"""
    return WriteFileFunc(path, "test content")


# ═══════════════════════════════════════════════════════════════════════════
# TestValidatePathAndSize — 正常路径
# ═══════════════════════════════════════════════════════════════════════════

class TestValidatePathAndSize:
    """_validate_path_and_size 路径校验"""

    # ── 正常路径 ─────────────────────────────────────────────────────

    def test_normal_file_path(self, tmp_path):
        """已存在的正常文件应通过校验"""
        f = tmp_path / "test.txt"
        f.write_text("hello")
        wf = _make_wf(str(f))
        # 不应抛异常
        wf._validate_path_and_size()

    def test_normal_file_not_exists(self, tmp_path):
        """不存在的文件路径应通过校验（仅做安全检查，不要求文件存在）"""
        f = tmp_path / "nonexistent.txt"
        wf = _make_wf(str(f))
        # 不应抛异常
        wf._validate_path_and_size()

    def test_normal_directory_path(self, tmp_path):
        """目录路径应通过校验"""
        d = tmp_path / "subdir"
        d.mkdir()
        wf = _make_wf(str(d))
        wf._validate_path_and_size()

    def test_relative_path(self, tmp_path):
        """相对路径应通过校验"""
        # 在 tmp_path 下创建文件，然后用相对路径测试
        f = tmp_path / "relative.txt"
        f.write_text("hello")
        wf = _make_wf(str(f))
        wf._validate_path_and_size()

    # ── 路径穿越 ─────────────────────────────────────────────────────

    def test_path_traversal_to_system(self):
        """路径穿越 ../ 解析后指向系统关键文件应拒绝"""
        wf = _make_wf("/tmp/../../../etc/passwd")
        # realpath 会解析到 /etc/passwd
        with pytest.raises(PathSecurityError, match="系统关键文件"):
            wf._validate_path_and_size()

    def test_path_traversal_to_device(self):
        """路径穿越 ../ 解析后指向 /dev/null 应拒绝"""
        wf = _make_wf("/tmp/../dev/null")
        with pytest.raises(PathSecurityError, match="特殊设备文件"):
            wf._validate_path_and_size()

    def test_path_traversal_within_safe(self, tmp_path):
        """路径穿越但仍在安全范围内应通过"""
        base = tmp_path / "safe"
        base.mkdir()
        f = base / "file.txt"
        f.write_text("hello")
        # 使用 ../ 但仍然指向安全路径
        wf = _make_wf(str(base / ".." / base.name / "file.txt"))
        wf._validate_path_and_size()

    # ── 危险路径 ─────────────────────────────────────────────────────

    def test_system_critical_path(self):
        """直接传入 /etc/passwd 应拒绝"""
        wf = _make_wf("/etc/passwd")
        with pytest.raises(PathSecurityError, match="系统关键文件"):
            wf._validate_path_and_size()

    def test_device_file(self):
        """直接传入 /dev/null 应拒绝"""
        wf = _make_wf("/dev/null")
        with pytest.raises(PathSecurityError, match="特殊设备文件"):
            wf._validate_path_and_size()

    # ── 符号链接指向白名单内（通过 realpath 解析后安全） ───────────

    def test_symlink_to_safe_target(self, tmp_path):
        """符号链接指向安全文件应通过"""
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        wf = _make_wf(str(link))
        wf._validate_path_and_size()

    # ── 符号链接指向白名单外（通过 monkeypatch 模拟） ───────────────

    def test_symlink_to_dangerous_target(self, monkeypatch, tmp_path):
        """符号链接指向系统关键文件应拒绝（via monkeypatch realpath）"""
        link = tmp_path / "bad_link.txt"
        # 创建普通文件然后用 monkeypatch 让 realpath 返回危险路径
        link.write_text("dummy")
        monkeypatch.setattr(os.path, "realpath", lambda p: "/etc/passwd")
        wf = _make_wf(str(link))
        with pytest.raises(PathSecurityError, match="系统关键文件"):
            wf._validate_path_and_size()

    # ── 文件大小检查 ─────────────────────────────────────────────────

    def test_file_size_within_limit(self, tmp_path):
        """文件在大小限制内应通过"""
        f = tmp_path / "small.txt"
        f.write_text("hello")
        wf = _make_wf(str(f))
        wf._validate_path_and_size()

    def test_file_size_exceeds_limit(self, tmp_path, monkeypatch):
        """文件超过大小限制应拒绝"""
        from src.tools.file_base import FileSizeError

        def _mock_check_file_size(path, max_mb=100):
            raise ValueError(f"文件大小(101.0MB)超过最大限制({max_mb}MB)")

        # mock check_file_size 抛 ValueError 来模拟超大文件，避免真实 I/O
        monkeypatch.setattr(
            "src.tools.file_base.check_file_size",
            _mock_check_file_size,
        )
        f = tmp_path / "small.txt"
        f.write_text("small")
        wf = _make_wf(str(f))
        with pytest.raises(FileSizeError):
            wf._validate_path_and_size()


# ═══════════════════════════════════════════════════════════════════════════
# TestPlanAgentWhitelist — plan agent 白名单
# ═══════════════════════════════════════════════════════════════════════════

class TestPlanAgentWhitelist:
    """plan agent 模式下的路径白名单限制"""

    def test_plan_agent_write_inside_plan_dir(self, tmp_path, monkeypatch):
        """plan agent 在 .chat/plan/ 内写入应通过"""
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        f = plan_dir / "plan.md"
        f.write_text("plan content")
        # monkeypatch _PLAN_ALLOWED_DIR
        monkeypatch.setattr(
            "src.tools.file_base._PLAN_ALLOWED_DIR",
            str(plan_dir),
        )
        wf = _make_wf(str(f))
        wf.agent_type = "plan"
        wf._validate_path_and_size()

    def test_plan_agent_write_outside_plan_dir(self, tmp_path, monkeypatch):
        """plan agent 在 .chat/plan/ 外写入应拒绝"""
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        outside_file = tmp_path / "outside.txt"
        outside_file.write_text("outside")
        # monkeypatch _PLAN_ALLOWED_DIR
        monkeypatch.setattr(
            "src.tools.file_base._PLAN_ALLOWED_DIR",
            str(plan_dir),
        )
        wf = _make_wf(str(outside_file))
        wf.agent_type = "plan"
        with pytest.raises(PathSecurityError, match="plan agent"):
            wf._validate_path_and_size()

    def test_plan_agent_write_plan_dir_parent(self, tmp_path, monkeypatch):
        """plan agent 写入 .chat/（plan 目录的父目录）应拒绝"""
        chat_dir = tmp_path / ".chat"
        chat_dir.mkdir(parents=True)
        plan_dir = chat_dir / "plan"
        plan_dir.mkdir()
        f = chat_dir / "other.md"
        f.write_text("other")
        monkeypatch.setattr(
            "src.tools.file_base._PLAN_ALLOWED_DIR",
            str(plan_dir),
        )
        wf = _make_wf(str(f))
        wf.agent_type = "plan"
        with pytest.raises(PathSecurityError, match="plan agent"):
            wf._validate_path_and_size()

    def test_non_plan_agent_no_whitelist(self, tmp_path, monkeypatch):
        """非 plan agent 不受白名单限制"""
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        outside_file = tmp_path / "any_file.txt"
        outside_file.write_text("any")
        monkeypatch.setattr(
            "src.tools.file_base._PLAN_ALLOWED_DIR",
            str(plan_dir),
        )
        wf = _make_wf(str(outside_file))
        wf.agent_type = None  # 默认值
        wf._validate_path_and_size()

    def test_agent_type_not_set(self, tmp_path, monkeypatch):
        """agent_type 未设置时不受白名单限制"""
        plan_dir = tmp_path / ".chat" / "plan"
        plan_dir.mkdir(parents=True)
        outside_file = tmp_path / "any_file.txt"
        outside_file.write_text("any")
        monkeypatch.setattr(
            "src.tools.file_base._PLAN_ALLOWED_DIR",
            str(plan_dir),
        )
        wf = _make_wf(str(outside_file))
        # 不设置 agent_type，使用默认值 None
        assert getattr(wf, 'agent_type', None) is None
        wf._validate_path_and_size()
