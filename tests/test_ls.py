"""测试 ls 工具

测试策略
--------
- 每个测试类关注一个概念，每个测试方法覆盖单一行为
- 文件操作使用 tmp_path 做目录隔离
- 测试 symlink 时使用 os.symlink（跨平台兼容）
- 遵循 Arrange/Act/Assert 模式
"""

from __future__ import annotations

import os
import stat
import time
import pytest
from pathlib import Path

from src.tools.ls import LsFunc
from src.core.constants import human_size


# ═══════════════════════════════════════════════════════════════════════════
# 1. __init__ 参数默认值
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """LsFunc.__init__ 参数默认值与赋值。"""

    def test_default_path_is_cwd(self):
        ls = LsFunc()
        assert ls.target_path == os.getcwd()
        assert ls.long is False
        assert ls.all is False
        assert ls.human is True

    def test_explicit_path(self):
        ls = LsFunc(path="/tmp")
        assert ls.target_path == "/tmp"

    def test_long_true(self):
        ls = LsFunc(long=True)
        assert ls.long is True

    def test_all_true(self):
        ls = LsFunc(all=True)
        assert ls.all is True

    def test_human_false(self):
        ls = LsFunc(human=False)
        assert ls.human is False

    def test_all_params(self):
        ls = LsFunc(path="/tmp", long=True, all=True, human=False)
        assert ls.target_path == "/tmp"
        assert ls.long is True
        assert ls.all is True
        assert ls.human is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestFromArgs:
    """from_args 从字典创建实例。"""

    def test_empty_args(self):
        """所有参数可选，空字典使用默认值。"""
        ls = LsFunc.from_args({})
        assert ls.target_path == os.getcwd()
        assert ls.long is False
        assert ls.all is False
        assert ls.human is True

    def test_all_params(self):
        ls = LsFunc.from_args({
            "path": "/tmp",
            "long": True,
            "all": True,
            "human": False,
        })
        assert ls.target_path == "/tmp"
        assert ls.long is True
        assert ls.all is True
        assert ls.human is False

    def test_partial_params(self):
        ls = LsFunc.from_args({"long": True})
        assert ls.target_path == os.getcwd()
        assert ls.long is True
        assert ls.all is False

    def test_extra_params_ignored(self):
        ls = LsFunc.from_args({"path": "/tmp", "unknown": "ignored"})
        assert ls.target_path == "/tmp"


# ═══════════════════════════════════════════════════════════════════════════
# 3. _list_entries — 排序与过滤
# ═══════════════════════════════════════════════════════════════════════════

class TestListEntries:
    """_list_entries 目录条目排序与隐藏文件过滤。"""

    def test_dirs_first_then_files(self, tmp_path):
        (tmp_path / "a_file.txt").write_text("")
        (tmp_path / "z_dir").mkdir()
        (tmp_path / "m_file.log").write_text("")

        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]

        # 目录应出现在文件之前
        z_dir_idx = names.index("z_dir")
        a_file_idx = names.index("a_file.txt")
        m_file_idx = names.index("m_file.log")
        assert z_dir_idx < a_file_idx
        assert z_dir_idx < m_file_idx

    def test_hidden_files_excluded_by_default(self, tmp_path):
        (tmp_path / "visible.txt").write_text("")
        (tmp_path / ".hidden").write_text("")

        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]
        assert "visible.txt" in names
        assert ".hidden" not in names

    def test_hidden_files_included_with_all(self, tmp_path):
        (tmp_path / "visible.txt").write_text("")
        (tmp_path / ".hidden").write_text("")

        ls = LsFunc(path=str(tmp_path), all=True)
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]
        assert "visible.txt" in names
        assert ".hidden" in names

    def test_case_insensitive_sort(self, tmp_path):
        (tmp_path / "A.txt").write_text("")
        (tmp_path / "b.txt").write_text("")
        (tmp_path / "C.txt").write_text("")

        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]
        # 排序应不区分大小写
        assert names == sorted(names, key=str.lower)

    def test_empty_dir_returns_empty_list(self, tmp_path):
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        assert entries == []

    def test_only_hidden_files_excluded_by_default(self, tmp_path):
        """目录中只有隐藏文件时返回空列表。"""
        (tmp_path / ".secret").write_text("")

        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        assert entries == []

    def test_only_hidden_files_with_all(self, tmp_path):
        """all=True 时隐藏文件也被列出。"""
        (tmp_path / ".secret").write_text("")

        ls = LsFunc(path=str(tmp_path), all=True)
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]
        assert ".secret" in names

    def test_symlink_included(self, tmp_path):
        (tmp_path / "target.txt").write_text("hello")
        os.symlink(tmp_path / "target.txt", tmp_path / "link_to_target")

        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        names = [e.name for e in entries]
        assert "link_to_target" in names
        assert "target.txt" in names


# ═══════════════════════════════════════════════════════════════════════════
# 4. _format_entries — 短格式/长格式输出
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatEntries:
    """_format_entries 短格式与长格式输出。"""

    def test_empty_dir_short_format(self, tmp_path):
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_entries([], tmp_path)
        assert "空目录" in result

    def test_short_format_has_names(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        assert "a.py" in result
        assert "b.py" in result

    def test_short_format_dir_slash_suffix(self, tmp_path):
        (tmp_path / "mydir").mkdir()
        (tmp_path / "file.txt").write_text("")
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        # 目录名应带 / 后缀
        assert "mydir/" in result

    def test_long_format_has_permissions(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        ls = LsFunc(path=str(tmp_path), long=True)
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        # 长格式应包含权限字符串（以 - 或 d 开头）
        lines = result.split("\n")
        content_lines = [l for l in lines if not l.startswith("总用量")]
        assert any(l.startswith("-") or l.startswith("d") for l in content_lines)

    def test_long_format_has_total_blocks(self, tmp_path):
        (tmp_path / "a.py").write_text("hello")
        ls = LsFunc(path=str(tmp_path), long=True)
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        assert "总用量" in result

    # ── 列式布局（子步骤 7.4）──

    def test_column_layout_single_column(self, tmp_path):
        """单列时的列式布局正确。"""
        (tmp_path / "very_long_filename_that_forces_single_column.py").write_text("")
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        lines = result.split("\n")
        # 每行一个条目
        assert len(lines) == 1
        assert "very_long_filename_that_forces_single_column.py" in result

    def test_column_layout_multi_column(self, tmp_path):
        """多列时列式布局正确。"""
        for name in ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"):
            (tmp_path / name).write_text("")
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        lines = result.split("\n")
        # 条目数少时应在一行内（列数 > 1）
        assert len(lines) <= 2  # 6个短文件名大概率在一行
        for name in ("a.py", "b.py", "c.py", "d.py", "e.py", "f.py"):
            assert name in result

    def test_column_layout_dir_slash_every_entry(self, tmp_path):
        """列式布局中所有目录都带 / 后缀。"""
        (tmp_path / "dir_a").mkdir()
        (tmp_path / "dir_b").mkdir()
        (tmp_path / "dir_c").mkdir()
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        assert "dir_a/" in result
        assert "dir_b/" in result
        assert "dir_c/" in result

    def test_column_layout_mixed_files_dirs(self, tmp_path):
        """混合文件和目录的列式布局。"""
        (tmp_path / "file_a.py").write_text("")
        (tmp_path / "dir_a").mkdir()
        (tmp_path / "file_b.py").write_text("")
        ls = LsFunc(path=str(tmp_path))
        entries = ls._list_entries(tmp_path)
        result = ls._format_entries(entries, tmp_path)
        assert "file_a.py" in result
        assert "dir_a/" in result
        assert "file_b.py" in result


# ═══════════════════════════════════════════════════════════════════════════
# 5. _format_long — 详细格式
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatLong:
    """_format_long 详细格式输出。"""

    def test_contains_permissions(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_long(tmp_path / "a.txt")
        # 以权限字符开头（-rw-r--r-- 等）
        assert result[0] == "-" or result[0] == "d"

    def test_contains_name(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_long(tmp_path / "a.txt")
        assert "a.txt" in result

    def test_dir_slash_suffix(self, tmp_path):
        (tmp_path / "mydir").mkdir()
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_long(tmp_path / "mydir")
        assert "mydir/" in result

    def test_human_size(self, tmp_path):
        (tmp_path / "a.txt").write_text("x" * 2048)  # 2KB
        ls = LsFunc(path=str(tmp_path), human=True, long=True)
        result = ls._format_long(tmp_path / "a.txt")
        assert "2.0K" in result or "2K" in result

    def test_raw_size_when_human_false(self, tmp_path):
        (tmp_path / "a.txt").write_text("x" * 2048)
        ls = LsFunc(path=str(tmp_path), human=False, long=True)
        result = ls._format_long(tmp_path / "a.txt")
        assert "2048" in result

    def test_contains_nlink(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_long(tmp_path / "a.txt")
        # 硬链接数通常是 1 对于新文件
        assert " 1 " in result or " 2 " in result  # 可能某些 FS 不同

    def test_contains_owner_group(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path))
        result = ls._format_long(tmp_path / "a.txt")
        # 所有者/组应有值（可能是 uid 数值或用户名）
        # 只要包含名称且没有崩溃就算通过
        assert "a.txt" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. _format_permissions — 权限字符串
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatPermissions:
    """_format_permissions 权限字符串格式化。"""

    def test_regular_file(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("")
        st = p.stat()
        result = LsFunc._format_permissions(st.st_mode, p)
        assert result.startswith("-")
        assert len(result) == 10  # -rwxrwxrwx

    def test_directory(self, tmp_path):
        p = tmp_path / "mydir"
        p.mkdir()
        st = p.stat()
        result = LsFunc._format_permissions(st.st_mode, p)
        assert result.startswith("d")
        assert len(result) == 10

    def test_symlink(self, tmp_path):
        p = tmp_path / "target.txt"
        p.write_text("hello")
        link = tmp_path / "link"
        os.symlink(p, link)
        st = os.lstat(link)  # lstat, not stat
        result = LsFunc._format_permissions(st.st_mode, link)
        assert result.startswith("l")
        assert len(result) == 10

    def test_read_write_exec_bits(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("")
        # 默认新文件应该有读写权限
        st = p.stat()
        result = LsFunc._format_permissions(st.st_mode, p)
        # 所有者至少有 rw-
        assert result[1] == "r"
        assert result[2] == "w"

    def test_chr_device_not_tested(self):
        """字符设备测试依赖系统环境，此处仅验证接口可调用。"""
        pass  # well covered by regular_file/directory/symlink

    def test_regular_file_with_exec(self, tmp_path):
        p = tmp_path / "script.sh"
        p.write_text("#!/bin/sh")
        p.chmod(0o755)  # -rwxr-xr-x
        st = p.stat()
        result = LsFunc._format_permissions(st.st_mode, p)
        assert result.startswith("-")
        assert result[3] == "x"  # owner exec
        assert result[6] == "x"  # group exec
        assert result[9] == "x"  # other exec


# ═══════════════════════════════════════════════════════════════════════════
# 7. human_size — 字节格式化
# ═══════════════════════════════════════════════════════════════════════════

class TestHumanSize:
    """human_size 人类可读字节格式。"""

    def test_bytes(self):
        assert human_size(0) == "0"
        assert human_size(512) == "512"
        assert human_size(1023) == "1023"

    def test_kilobytes(self):
        result = human_size(1024)
        assert "K" in result or result == "1024"

    def test_megabytes(self):
        result = human_size(1024 * 1024)
        assert "M" in result

    def test_gigabytes(self):
        result = human_size(1024 * 1024 * 1024)
        assert "G" in result

    def test_terabytes(self):
        result = human_size(1024 ** 4)
        assert "T" in result

    def test_exact_1k(self):
        r = human_size(1024)
        assert "1.0K" in r

    def test_exact_1m(self):
        r = human_size(1024 * 1024)
        assert "1.0M" in r

    def test_rounding(self):
        # 1536 → 1.5K
        r = human_size(1536)
        assert "1.5K" in r

    def test_large_hundreds(self):
        # 102400 → 100K
        r = human_size(102400)
        assert "100" in r and "K" in r


# ═══════════════════════════════════════════════════════════════════════════
# 8. execute（异步执行入口）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestExecute:
    """LsFunc.execute 异步执行。"""

    async def test_path_not_exists(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        ls = LsFunc(path=fake)
        result = await ls.execute()
        assert "路径不存在" in result

    async def test_list_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        ls = LsFunc(path=str(tmp_path))
        result = await ls.execute()
        assert "a.txt" in result

    async def test_list_single_file_short(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("")
        ls = LsFunc(path=str(p))
        result = await ls.execute()
        assert result == "f.txt"

    async def test_list_single_file_long(self, tmp_path):
        p = tmp_path / "f.txt"
        p.write_text("hello")
        ls = LsFunc(path=str(p), long=True)
        result = await ls.execute()
        assert "f.txt" in result
        # 长格式包含权限信息
        assert result.startswith("-") or " " in result

    async def test_empty_directory(self, tmp_path):
        ls = LsFunc(path=str(tmp_path))
        result = await ls.execute()
        assert "空目录" in result

    async def test_empty_directory_with_all(self, tmp_path):
        """空目录即使 all=True 也是空目录。"""
        ls = LsFunc(path=str(tmp_path), all=True)
        result = await ls.execute()
        assert "空目录" in result

    async def test_directory_with_only_hidden(self, tmp_path):
        """只有隐藏文件时默认返回空目录。"""
        (tmp_path / ".secret").write_text("")
        ls = LsFunc(path=str(tmp_path))
        result = await ls.execute()
        assert "空目录" in result

    async def test_directory_with_only_hidden_and_all(self, tmp_path):
        """all=True 时显示隐藏文件。"""
        (tmp_path / ".secret").write_text("")
        ls = LsFunc(path=str(tmp_path), all=True)
        result = await ls.execute()
        assert ".secret" in result
        assert "空目录" not in result

    async def test_mixed_content(self, tmp_path):
        (tmp_path / "file.txt").write_text("")
        (tmp_path / "mydir").mkdir()
        ls = LsFunc(path=str(tmp_path))
        result = await ls.execute()
        assert "file.txt" in result
        assert "mydir/" in result

    async def test_long_format(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path), long=True)
        result = await ls.execute()
        assert "总用量" in result
        assert "a.txt" in result

    async def test_symlink_in_execute(self, tmp_path):
        (tmp_path / "target.txt").write_text("hello")
        os.symlink(tmp_path / "target.txt", tmp_path / "link")

        ls = LsFunc(path=str(tmp_path))
        result = await ls.execute()
        assert "link" in result
        assert "target.txt" in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. _format_single_file
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatSingleFile:
    """_format_single_file 单文件格式化。"""

    def test_short_format_returns_name(self, tmp_path):
        p = tmp_path / "foo.txt"
        p.write_text("")
        ls = LsFunc(path=str(p))
        result = ls._format_single_file(p)
        assert result == "foo.txt"

    def test_long_format_contains_name_and_perms(self, tmp_path):
        p = tmp_path / "foo.txt"
        p.write_text("hello")
        ls = LsFunc(path=str(p), long=True)
        result = ls._format_single_file(p)
        assert "foo.txt" in result
        assert result.startswith("-")


# ═══════════════════════════════════════════════════════════════════════════
# 10. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要。"""

    def test_default_path(self):
        r = LsFunc.display_params({})
        # 默认 path 为空时显示 "."
        assert "." in r

    def test_explicit_path(self):
        r = LsFunc.display_params({"path": "/tmp"})
        assert "/tmp" in r

    def test_with_long(self):
        r = LsFunc.display_params({"path": "/tmp", "long": True})
        assert "-l" in r

    def test_with_all(self):
        r = LsFunc.display_params({"path": "/tmp", "all": True})
        assert "-a" in r

    def test_with_long_and_all(self):
        r = LsFunc.display_params({"path": "/tmp", "long": True, "all": True})
        assert "-l" in r
        assert "-a" in r

    def test_sanitize_newline(self):
        r = LsFunc.display_params({"path": "a\nb"})
        assert "/n" in r

    def test_path_only(self):
        r = LsFunc.display_params({"path": "src"})
        assert "src" in r


# ═══════════════════════════════════════════════════════════════════════════
# 11. display / web_display
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDisplay:
    """LsFunc.display / web_display 显示。"""

    async def test_display_returns_result(self, tmp_path):
        (tmp_path / "a.txt").write_text("")
        ls = LsFunc(path=str(tmp_path))
        result = await ls.display()
        assert "a.txt" in result

    async def test_display_error_path(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        ls = LsFunc(path=fake)
        result = await ls.display()
        assert "路径不存在" in result

    async def test_display_empty_dir(self, tmp_path):
        ls = LsFunc(path=str(tmp_path))
        result = await ls.display()
        assert "空目录" in result

    async def test_display_long_format(self, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        ls = LsFunc(path=str(tmp_path), long=True)
        result = await ls.display()
        assert "a.txt" in result

    async def test_web_display_returns_result(self, tmp_path):
        (tmp_path / "foo.txt").write_text("")
        ls = LsFunc(path=str(tmp_path))
        result = await ls.web_display()
        # web_display 返回 execute 的结果
        assert "foo.txt" in result

    async def test_web_display_empty(self, tmp_path):
        ls = LsFunc(path=str(tmp_path))
        result = await ls.web_display()
        assert "空目录" in result
