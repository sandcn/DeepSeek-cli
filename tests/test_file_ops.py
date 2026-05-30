"""测试 src.tools.file_ops：文件操作工具模块中的核心纯函数。

测试策略
--------
- 每个测试类对应一个函数，每个测试方法覆盖一个边界/分支场景
- 纯函数直接传参验证输入输出
- 文件操作用 tmp_path 隔离
- 路径校验用 monkeypatch 控制 Android/非 Android 行为
- confirm 用 monkeypatch 模拟 input
- 遵循 Arrange/Act/Assert 模式
"""

import os

import pytest

from src.tools.file_ops import (
    _sync_collect_files,
    check_file_size,
    get_last_user_message_preview,
    validate_path_security,
)

# ═══════════════════════════════════════════════════════════════════════════
# TestValidatePathSecurity
# ═══════════════════════════════════════════════════════════════════════════

class TestValidatePathSecurity:
    """validate_path_security 路径安全性验证"""

    # ── Windows 设备前缀 ─────────────────────────────────────────────

    def test_windows_device_prefix_double_dot(self):
        """原始设备路径 backslash-dot-dot 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入原始设备路径"):
            validate_path_security("\\\\.\\PhysicalDrive0")

    def test_windows_device_prefix_question(self):
        """原始设备路径 backslash-question 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入原始设备路径"):
            validate_path_security("\\\\?\\C:\\boot.ini")

    # ── 危险设备文件 ─────────────────────────────────────────────────

    def test_dangerous_device_null(self):
        """特殊设备文件 /dev/null 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/null")

    def test_dangerous_device_zero(self):
        """特殊设备文件 /dev/zero 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/zero")

    def test_dangerous_device_random(self):
        """特殊设备文件 /dev/random 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/random")

    def test_dangerous_device_urandom(self):
        """特殊设备文件 /dev/urandom 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/urandom")

    def test_dangerous_device_stdin(self):
        """特殊设备文件 /dev/stdin 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/stdin")

    def test_dangerous_device_stdout(self, monkeypatch):
        """特殊设备文件 /dev/stdout 应拒绝（monkeypatch realpath 避免 symlink 解析）"""
        monkeypatch.setattr(os.path, "realpath", lambda p: p)
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/stdout")

    def test_dangerous_device_stderr(self, monkeypatch):
        """特殊设备文件 /dev/stderr 应拒绝（monkeypatch realpath 避免 symlink 解析）"""
        monkeypatch.setattr(os.path, "realpath", lambda p: p)
        with pytest.raises(ValueError, match="不允许写入特殊设备文件"):
            validate_path_security("/dev/stderr")

    # ── 系统关键路径 ─────────────────────────────────────────────────

    def test_system_critical_etc_passwd(self):
        """系统关键文件 /etc/passwd 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/etc/passwd")

    def test_system_critical_etc_shadow(self):
        """系统关键文件 /etc/shadow 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/etc/shadow")

    def test_system_critical_etc_sudoers(self):
        """系统关键文件 /etc/sudoers 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/etc/sudoers")

    def test_system_critical_bin(self):
        """系统关键目录 /bin 下的文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/bin/ls")

    def test_system_critical_sbin(self):
        """系统关键目录 /sbin 下的文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/sbin/init")

    def test_system_critical_usr_bin(self):
        """系统关键目录 /usr/bin 下的文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/usr/bin/python")

    def test_system_critical_usr_sbin(self):
        """系统关键目录 /usr/sbin 下的文件应拒绝"""
        with pytest.raises(ValueError, match="不允许写入系统关键文件"):
            validate_path_security("/usr/sbin/sshd")

    # ── DOS 设备名 ───────────────────────────────────────────────────

    def test_dos_device_con(self):
        """DOS 设备名 CON 应拒绝（无扩展名）"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/CON")

    def test_dos_device_con_with_ext(self):
        """DOS 设备名 CON.txt 应拒绝（有扩展名）"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/CON.txt")

    def test_dos_device_con_lowercase(self):
        """DOS 设备名 con.txt 应拒绝（小写）"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/con.txt")

    def test_dos_device_prn(self):
        """DOS 设备名 PRN 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/PRN")

    def test_dos_device_aux(self):
        """DOS 设备名 AUX 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/AUX")

    def test_dos_device_nul(self):
        """DOS 设备名 NUL 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/NUL")

    def test_dos_device_conin(self):
        """DOS 设备名 CONIN$ 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/CONIN$")

    def test_dos_device_conout(self):
        """DOS 设备名 CONOUT$ 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/CONOUT$")

    def test_dos_device_com1(self):
        """DOS 设备名 COM1 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/COM1.txt")

    def test_dos_device_com9(self):
        """DOS 设备名 COM9 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/COM9.log")

    def test_dos_device_lpt1(self):
        """DOS 设备名 LPT1 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/LPT1.txt")

    def test_dos_device_lpt9(self):
        """DOS 设备名 LPT9 应拒绝"""
        with pytest.raises(ValueError, match="不允许写入 DOS 设备名"):
            validate_path_security("/tmp/LPT9")

    # ── NTFS 流 ───────────────────────────────────────────────────────

    def test_ntfs_stream_ads(self):
        """NTFS 备用数据流（文件名中多个冒号）应拒绝"""
        with pytest.raises(ValueError, match="路径包含 NTFS 流或非法冒号"):
            validate_path_security("/tmp/file.txt:hidden:ads")

    def test_ntfs_stream_dollar(self):
        """NTFS 流包含 :$ 应拒绝"""
        with pytest.raises(ValueError, match="路径包含 NTFS 流或非法冒号"):
            validate_path_security("/tmp/file:$DATA")

    def test_ntfs_stream_multi_colon(self):
        """文件名中多个冒号应拒绝"""
        with pytest.raises(ValueError, match="路径包含 NTFS 流或非法冒号"):
            validate_path_security("/tmp/file:stream:more")

    # ── 安全路径应通过（Android 环境 ~ 为绝对路径，跳过目录检查） ──

    def test_safe_tmp_path(self):
        """安全路径 /tmp/test.txt 应通过"""
        validate_path_security("/tmp/test.txt")

    def test_safe_tmp_subdir(self):
        """安全路径 /tmp/subdir/file.txt 应通过"""
        validate_path_security("/tmp/subdir/file.txt")

    def test_safe_var_tmp(self):
        """安全路径 /var/tmp/file.txt 应通过"""
        validate_path_security("/var/tmp/test.txt")

    def test_safe_home_path(self):
        """home 目录下路径应通过"""
        home = os.path.expanduser("~")
        validate_path_security(os.path.join(home, "test.txt"))

    def test_safe_relative_path(self):
        """相对路径应通过"""
        validate_path_security("relative/path/file.txt")

    def test_safe_dot_path(self):
        """以点开头的路径应通过"""
        validate_path_security("./local/file.txt")


# ═══════════════════════════════════════════════════════════════════════════
# TestCheckFileSize
# ═══════════════════════════════════════════════════════════════════════════

class TestCheckFileSize:
    """check_file_size 文件大小检查"""

    def test_file_within_limit(self, tmp_path):
        """文件小于限制时应静默通过"""
        f = tmp_path / "small.txt"
        f.write_text("hello" * 100)
        check_file_size(str(f), max_mb=100)  # 不应抛异常

    def test_file_exceeds_limit(self, tmp_path):
        """文件超过限制时应抛 ValueError"""
        f = tmp_path / "large.txt"
        # 创建超过 10MB 的文件
        f.write_text("x" * (11 * 1024 * 1024))
        with pytest.raises(ValueError, match="超过最大限制"):
            check_file_size(str(f), max_mb=10)

    def test_file_exceeds_custom_limit(self, tmp_path):
        """文件超过自定义小限制时应抛 ValueError"""
        f = tmp_path / "medium.txt"
        f.write_text("x" * (2 * 1024 * 1024))
        with pytest.raises(ValueError, match="超过最大限制"):
            check_file_size(str(f), max_mb=1)

    def test_non_existent_file(self, tmp_path):
        """文件不存在时应静默通过（不抛异常）"""
        check_file_size(str(tmp_path / "nonexistent.txt"), max_mb=100)

    def test_empty_file(self, tmp_path):
        """空文件应静默通过"""
        f = tmp_path / "empty.txt"
        f.write_text("")
        check_file_size(str(tmp_path / "empty.txt"), max_mb=100)

    def test_just_under_limit(self, tmp_path):
        """文件刚好在限制内应通过"""
        f = tmp_path / "edge.txt"
        f.write_text("x" * (100 * 1024))
        # 100KB < 1MB，应通过
        check_file_size(str(f), max_mb=1)

    def test_directory_path_silent_pass(self, tmp_path):
        """目录路径传入时应静默通过（os.stat 会失败但被 except 捕获）"""
        check_file_size(str(tmp_path), max_mb=100)




# ═══════════════════════════════════════════════════════════════════════════
# TestLastUserMessagePreview
# ═══════════════════════════════════════════════════════════════════════════

class TestLastUserMessagePreview:
    """get_last_user_message_preview 最后用户消息预览"""

    def test_last_user_message_returned(self):
        """从多条消息中提取最后一条 user 消息"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "第一条用户消息"},
            {"role": "assistant", "content": "回复"},
            {"role": "user", "content": "第二条用户消息"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "第二条用户消息"

    def test_single_user_message(self):
        """只有一条 user 消息"""
        messages = [
            {"role": "user", "content": "你好"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "你好"

    def test_no_user_message(self):
        """没有 user 消息时返回默认值"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "assistant", "content": "回复"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "聊天已完成"

    def test_empty_messages(self):
        """空消息列表返回默认值"""
        result = get_last_user_message_preview([])
        assert result == "聊天已完成"

    def test_user_message_empty_content(self):
        """user 消息 content 为空时跳过"""
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "实际内容"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "实际内容"

    def test_user_message_only_whitespace(self):
        """user 消息 content 全空白时视为有效内容"""
        messages = [
            {"role": "user", "content": "   "},
        ]
        result = get_last_user_message_preview(messages)
        # strip() 后是空字符串，len <= max_chars 成立，返回空字符串
        assert result == ""

    def test_long_message_truncated(self):
        """消息超过 max_chars 时截断加 ..."""
        messages = [
            {"role": "user", "content": "A" * 200},
        ]
        result = get_last_user_message_preview(messages, max_chars=10)
        assert result == "A" * 10 + "..."

    def test_exact_max_chars(self):
        """消息刚好等于 max_chars 时不截断"""
        messages = [
            {"role": "user", "content": "A" * 100},
        ]
        result = get_last_user_message_preview(messages, max_chars=100)
        assert result == "A" * 100

    def test_custom_max_chars(self):
        """自定义 max_chars 参数"""
        messages = [
            {"role": "user", "content": "自定义截断长度测试消息"},
        ]
        result = get_last_user_message_preview(messages, max_chars=4)
        assert result == "自定义截断长度测试消息"[:4] + "..."

    def test_strip_whitespace_in_content(self):
        """content 前后空白应被 strip"""
        messages = [
            {"role": "user", "content": "  你好世界  "},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "你好世界"

    def test_multiple_user_messages_last_selected(self):
        """多条 user 消息取最后一条"""
        messages = [
            {"role": "user", "content": "消息A"},
            {"role": "user", "content": "消息B"},
            {"role": "user", "content": "消息C"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "消息C"

    def test_user_message_without_content_key(self):
        """user 消息缺少 content key 应跳过"""
        messages = [
            {"role": "system", "content": "助手"},
            {"role": "user", "name": "张三"},  # 没有 content
            {"role": "assistant", "content": "回复"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "聊天已完成"

    def test_user_message_content_none(self):
        """user 消息 content 为 None 应跳过"""
        messages = [
            {"role": "user", "content": None},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "聊天已完成"

    def test_mixed_roles(self):
        """多种 role 混合时正确找到最后 user 消息"""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user1"},
            {"role": "assistant", "content": "assistant1"},
            {"role": "user", "content": "user2"},
            {"role": "tool", "content": "tool_result"},
            {"role": "assistant", "content": "assistant2"},
        ]
        result = get_last_user_message_preview(messages)
        assert result == "user2"


# ═══════════════════════════════════════════════════════════════════════════
# TestSyncCollectFiles
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncCollectFiles:
    """_sync_collect_files 同步收集文件列表"""

    def test_single_file(self, tmp_path):
        """传入文件路径应返回包含该文件的单元素列表"""
        f = tmp_path / "file.txt"
        f.write_text("hello")
        result = _sync_collect_files(str(f))
        assert result == [str(f)]

    def test_empty_directory(self, tmp_path):
        """空目录应返回空列表"""
        d = tmp_path / "empty_dir"
        d.mkdir()
        result = _sync_collect_files(str(d))
        assert result == []

    def test_directory_with_files(self, tmp_path):
        """目录下文件应被递归收集"""
        d = tmp_path / "data"
        d.mkdir()
        f1 = d / "a.txt"
        f1.write_text("a")
        f2 = d / "b.txt"
        f2.write_text("b")
        result = _sync_collect_files(str(d))
        assert sorted(result) == sorted([str(f1), str(f2)])

    def test_nested_directories(self, tmp_path):
        """嵌套目录应递归收集所有文件"""
        root = tmp_path / "root"
        root.mkdir()
        (root / "level1").mkdir()
        (root / "level1" / "level2").mkdir()

        f1 = root / "a.txt"
        f1.write_text("a")
        f2 = root / "level1" / "b.txt"
        f2.write_text("b")
        f3 = root / "level1" / "level2" / "c.txt"
        f3.write_text("c")

        result = _sync_collect_files(str(root))
        assert sorted(result) == sorted([str(f1), str(f2), str(f3)])

    def test_symlink_file(self, tmp_path):
        """符号链接文件应被收集"""
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = tmp_path / "link.txt"
        link.symlink_to(target)
        result = _sync_collect_files(str(link))
        assert str(link) in result

    def test_symlink_in_directory(self, tmp_path):
        """目录中的符号链接应被收集（followlinks=False）"""
        d = tmp_path / "dir"
        d.mkdir()
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = d / "link.txt"
        link.symlink_to(target)

        result = _sync_collect_files(str(d))
        assert str(link) in result

    def test_non_existent_path(self, tmp_path):
        """不存在的路径应返回空列表"""
        result = _sync_collect_files(str(tmp_path / "nonexistent"))
        assert result == []

    def test_directory_with_subdirs_only(self, tmp_path):
        """只有子目录没有文件的目录应返回空列表"""
        d = tmp_path / "parent"
        d.mkdir()
        (d / "child").mkdir()
        result = _sync_collect_files(str(d))
        assert result == []

    def test_mixed_file_and_dir(self, tmp_path):
        """文件与目录混合（传入文件路径只返回文件本身）"""
        f = tmp_path / "file.txt"
        f.write_text("file")
        d = tmp_path / "subdir"
        d.mkdir()
        # 传入文件路径
        result = _sync_collect_files(str(f))
        assert result == [str(f)]
        # 传入目录路径
        result_dir = _sync_collect_files(str(tmp_path))
        assert str(f) in result_dir

    def test_multiple_files_in_flat_dir(self, tmp_path):
        """平铺目录中的多个文件应全部收集"""
        d = tmp_path / "flat"
        d.mkdir()
        expected = []
        for name in ["f1.txt", "f2.py", "f3.md", "f4.json"]:
            f = d / name
            f.write_text(name)
            expected.append(str(f))
        result = _sync_collect_files(str(d))
        assert sorted(result) == sorted(expected)



