"""测试 find 工具

测试策略
--------
- 每个测试类关注一个概念，每个测试方法覆盖单一行为
- 文件操作使用 tmp_path 做目录隔离
- 使用纯 Python 实现的 find（无外部依赖），不须 mock
"""

from __future__ import annotations

import os
import pytest
from pathlib import Path

from src.tools.find import FindFunc
from src.tools._constants import should_exclude_dir as _should_exclude_dir


# ═══════════════════════════════════════════════════════════════════════════
# 1. _should_exclude_dir（模块级函数）
# ═══════════════════════════════════════════════════════════════════════════

class TestShouldExcludeDir:
    """_should_exclude_dir 排除目录检测。"""

    @pytest.mark.parametrize("name", [
        "node_modules", "__pycache__", ".git", ".hg", ".svn",
        "venv", ".venv", "env", ".env",
        ".idea", ".vscode", ".vscode-server",
        "dist", "build", "target",
        ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ".tox", ".nox", ".bundle",
        ".next", ".nuxt", ".output",
        "__snapshots__", "__fixtures__",
        ".chat",
    ])
    def test_excludes_known_dirs(self, name):
        assert _should_exclude_dir(name) is True

    def test_excludes_egg_info_glob(self):
        assert _should_exclude_dir("my.egg-info") is True
        assert _should_exclude_dir("foo.egg-info") is True

    def test_glob_pattern_is_case_sensitive(self):
        """通配符匹配应区分大小写。"""
        assert _should_exclude_dir("My.Egg-Info") is False
        # 但 set 中的精确匹配不区分大小写，此处 My.Egg-Info 不在 set 中
        # 且不匹配 *.egg-info 通配符（大小写敏感）

    def test_partial_match_not_false_positive(self):
        """部分匹配不应误判：xegg-info 不匹配 *.egg-info。"""
        assert _should_exclude_dir("xegg-info") is False

    @pytest.mark.parametrize("name", ["src", "tests", "", "mydir", "data", "docs"])
    def test_allows_normal_names(self, name):
        assert _should_exclude_dir(name) is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. __init__ 参数处理
# ═══════════════════════════════════════════════════════════════════════════

class TestInit:
    """FindFunc.__init__ 参数处理。"""

    def test_default_path_is_cwd(self):
        f = FindFunc(pattern="*.py")
        assert f.pattern == "*.py"
        assert f.root_path == os.getcwd()
        assert f.filter_type is None
        assert f.depth == 0

    def test_explicit_path(self):
        f = FindFunc(pattern="*.py", path="/tmp")
        assert f.root_path == "/tmp"

    def test_type_file(self):
        f = FindFunc(pattern="*.py", type="file")
        assert f.filter_type == "file"

    def test_type_dir(self):
        f = FindFunc(pattern="test_*", type="dir")
        assert f.filter_type == "dir"

    def test_depth_positive(self):
        f = FindFunc(pattern="*.py", depth=3)
        assert f.depth == 3

    def test_depth_negative_clamped_to_zero(self):
        f = FindFunc(pattern="*.py", depth=-5)
        assert f.depth == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. from_args
# ═══════════════════════════════════════════════════════════════════════════

class TestFromArgs:
    """from_args 从字典创建 FindFunc 实例。"""

    def test_required_only(self):
        f = FindFunc.from_args({"pattern": "*.py"})
        assert f.pattern == "*.py"
        assert f.root_path == os.getcwd()
        assert f.filter_type is None
        assert f.depth == 0

    def test_extra_params_ignored(self):
        f = FindFunc.from_args({"pattern": "*.py", "unknown": "ignored", "extra": 42})
        assert f.pattern == "*.py"
        assert f.root_path == os.getcwd()

    def test_missing_pattern_raises(self):
        with pytest.raises(ValueError, match="缺少必需参数"):
            FindFunc.from_args({})


# ═══════════════════════════════════════════════════════════════════════════
# 4. _sync_find_files 目录遍历逻辑
#
# 根目录层级（current_depth=0）的文件也会被匹配。
# 测试同时覆盖根目录层级和子目录层级的匹配逻辑。
# ═══════════════════════════════════════════════════════════════════════════

class TestSyncFindFiles:
    """_sync_find_files 目录遍历与匹配逻辑。"""

    # ── 辅助 ──

    @staticmethod
    def _setup_src(tmp_path) -> Path:
        src = tmp_path / "src"
        src.mkdir()
        return src

    # ── 基础匹配 ──

    def test_simple_pattern_match(self, tmp_path):
        src = self._setup_src(tmp_path)
        (src / "foo.py").write_text("")
        (src / "bar.txt").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "foo.py" in names
        assert "bar.txt" not in names

    def test_multiple_patterns_or(self, tmp_path):
        """多个空格分隔的模式，OR 匹配。"""
        src = self._setup_src(tmp_path)
        (src / "a.py").write_text("")
        (src / "b.json").write_text("")
        (src / "c.txt").write_text("")

        f = FindFunc(pattern="*.py *.json", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "a.py" in names
        assert "b.json" in names
        assert "c.txt" not in names

    def test_type_file_filter(self, tmp_path):
        src = self._setup_src(tmp_path)
        (src / "script.py").write_text("")
        (src / "mydir").mkdir()

        f = FindFunc(pattern="*", path=str(tmp_path), type="file")
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "script.py" in names
        assert "mydir" not in names

    def test_type_dir_filter(self, tmp_path):
        src = self._setup_src(tmp_path)
        (src / "script.py").write_text("")
        (src / "mydir").mkdir()

        f = FindFunc(pattern="*", path=str(tmp_path), type="dir")
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "mydir" in names
        assert "script.py" not in names

    # ── 深度控制 ──

    def test_depth_0_unlimited(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path), depth=0)
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "deep.py" in names

    def test_depth_1_reaches_subdir_files(self, tmp_path):
        """depth=1：子目录 depth=1 的文件被匹配，depth>1 的条目被跳过。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("")
        deep2 = sub / "deep2"
        deep2.mkdir()
        (deep2 / "too_deep.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path), depth=1)
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "deep.py" in names
        assert "too_deep.py" not in names

    def test_depth_2_reaches_two_levels(self, tmp_path):
        """depth=2：包含 depth 1 和 depth 2 的条目。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        deep = sub / "deep"
        deep.mkdir()
        (deep / "very.py").write_text("")
        (sub / "subfile.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path), depth=2)
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "subfile.py" in names
        assert "very.py" in names

    # ── 排除目录 ──

    def test_excluded_dirs_skipped(self, tmp_path):
        excluded = tmp_path / "node_modules"
        excluded.mkdir()
        (excluded / "ignore.py").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "keep.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "keep.py" in names
        assert "ignore.py" not in names

    def test_multiple_excluded_dirs_all_skipped(self, tmp_path):
        for d in ("node_modules", "__pycache__", ".git", "venv"):
            p = tmp_path / d
            p.mkdir()
            (p / "ignored.py").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "active.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "active.py" in names
        assert "ignored.py" not in names

    # ── 边界 ──

    def test_empty_pattern_returns_empty(self, tmp_path):
        f = FindFunc(pattern="", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        assert results == []

    def test_whitespace_only_pattern_returns_empty(self, tmp_path):
        f = FindFunc(pattern="   ", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        assert results == []

    # ── 根目录自身匹配 ──

    def test_root_directory_self_match(self, tmp_path):
        """根目录自身（通过名称）参与匹配。"""
        f = FindFunc(pattern="*", path=str(tmp_path), type="dir")
        results = f._sync_find_files(tmp_path)
        assert tmp_path in results

    def test_root_directory_matches_by_name(self, tmp_path):
        """根目录名称匹配指定 pattern 时被加入结果。"""
        dir_name = tmp_path.name
        f = FindFunc(pattern=dir_name, path=str(tmp_path), type="dir")
        results = f._sync_find_files(tmp_path)
        assert tmp_path in results

    # ── 排序 ──

    def test_results_contain_all_matches(self, tmp_path):
        """匹配结果应包含所有命中的条目。"""
        src = self._setup_src(tmp_path)
        (src / "z.py").write_text("")
        (src / "a.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = [p.name for p in results]
        assert "a.py" in names
        assert "z.py" in names

    # ── 根目录文件匹配 ──

    def test_root_level_files_are_matched(self, tmp_path):
        """根目录层级（current_depth=0）的文件应被匹配。"""
        (tmp_path / "rootfile.py").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "subfile.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "rootfile.py" in names   # 根目录文件被匹配
        assert "subfile.py" in names    # 子目录文件正常匹配

    def test_root_level_star_matches_all_files(self, tmp_path):
        """* 通配符在根目录层级应匹配所有文件。"""
        (tmp_path / "foo.py").write_text("")
        (tmp_path / "bar.txt").write_text("")
        (tmp_path / "baz.json").write_text("")

        f = FindFunc(pattern="*", path=str(tmp_path), type="file")
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "foo.py" in names
        assert "bar.txt" in names
        assert "baz.json" in names

    def test_glob_still_matches_subdir_files_after_root_fix(self, tmp_path):
        """修复后，glob 模式在子目录层级仍应正常工作。"""
        src = tmp_path / "src"
        src.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()
        (src / "main.py").write_text("")
        (tests / "test_main.py").write_text("")
        (tmp_path / "Readme.md").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "main.py" in names
        assert "test_main.py" in names
        assert "Readme.md" not in names

    # ── 通配符高级匹配（子步骤 7.4）──

    def test_charset_pattern(self, tmp_path):
        """[Tt]est.py 字符集模式。"""
        src = self._setup_src(tmp_path)
        (src / "Test.py").write_text("")
        (src / "test.py").write_text("")
        (src / "best.py").write_text("")

        f = FindFunc(pattern="[Tt]est.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "Test.py" in names
        assert "test.py" in names
        assert "best.py" not in names

    def test_exclude_charset_pattern(self, tmp_path):
        """[!.]* 排除隐藏文件模式。"""
        src = self._setup_src(tmp_path)
        (src / "visible.py").write_text("")
        (src / ".hidden.py").write_text("")

        f = FindFunc(pattern="[!.]*", path=str(tmp_path), type="file")
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "visible.py" in names
        assert ".hidden.py" not in names

    def test_single_char_wildcard(self, tmp_path):
        """config?.py 单字符通配符。"""
        src = self._setup_src(tmp_path)
        (src / "config1.py").write_text("")
        (src / "config2.py").write_text("")
        (src / "config10.py").write_text("")  # 两个字符，不应匹配

        f = FindFunc(pattern="config?.py", path=str(tmp_path))
        results = f._sync_find_files(tmp_path)
        names = {p.name for p in results}
        assert "config1.py" in names
        assert "config2.py" in names
        assert "config10.py" not in names


# ═══════════════════════════════════════════════════════════════════════════
# 5. execute（异步执行入口）
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestExecute:
    """FindFunc.execute 异步执行。"""

    async def test_path_not_exists(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        f = FindFunc(pattern="*.py", path=fake)
        result = await f.execute()
        assert "路径不存在" in result

    async def test_path_is_file_not_dir(self, tmp_path):
        p = tmp_path / "afile.txt"
        p.write_text("")
        f = FindFunc(pattern="*.py", path=str(p))
        result = await f.execute()
        assert "路径不是目录" in result

    async def test_empty_dir_returns_not_found(self, tmp_path):
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "未找到结果" in result

    async def test_finds_file_in_subdir(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "test.py").write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "test.py" in result
        assert "找到" in result

    async def test_unicode_filename(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "中文文件.py").write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "中文文件.py" in result


# ═══════════════════════════════════════════════════════════════════════════
# 6. _format_results 输出格式化
# ═══════════════════════════════════════════════════════════════════════════

class TestFormatResults:
    """_format_results 结果格式化。"""

    def test_empty_results(self, tmp_path):
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = f._format_results([], tmp_path)
        assert "未找到结果" in result

    def test_single_file(self, tmp_path):
        p = tmp_path / "foo.py"
        p.write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = f._format_results([p], tmp_path)
        assert "foo.py" in result
        assert "📄" in result
        assert "找到" in result

    def test_single_dir(self, tmp_path):
        d = tmp_path / "mydir"
        d.mkdir()
        f = FindFunc(pattern="*", path=str(tmp_path))
        result = f._format_results([d], tmp_path)
        assert "mydir" in result
        assert "📁" in result

    def test_root_self_display(self, tmp_path):
        """根目录自身显示为目录名。"""
        f = FindFunc(pattern=tmp_path.name, path=str(tmp_path))
        result = f._format_results([tmp_path], tmp_path)
        assert tmp_path.name in result

    def test_type_label_in_header(self, tmp_path):
        """有结果时 type 标签出现在 header 中。"""
        p = tmp_path / "foo.py"
        p.write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path), type="file")
        result = f._format_results([p], tmp_path)
        assert "文件" in result

    def test_mixed_file_and_dir(self, tmp_path):
        (tmp_path / "file.py").write_text("")
        (tmp_path / "mydir").mkdir()
        results = [tmp_path / "file.py", tmp_path / "mydir"]
        f = FindFunc(pattern="*", path=str(tmp_path))
        result = f._format_results(results, tmp_path)
        assert "📄" in result
        assert "📁" in result

    def test_sorted_by_relative_path(self, tmp_path):
        """结果按相对路径排序。"""
        p1 = tmp_path / "z.py"
        p1.write_text("")
        p2 = tmp_path / "a.py"
        p2.write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = f._format_results([p1, p2], tmp_path)
        # a.py 应出现在 z.py 之前
        a_idx = result.index("a.py")
        z_idx = result.index("z.py")
        assert a_idx < z_idx


# ═══════════════════════════════════════════════════════════════════════════
# 7. display_params
# ═══════════════════════════════════════════════════════════════════════════

class TestDisplayParams:
    """display_params 参数摘要。"""

    def test_pattern_only(self):
        r = FindFunc.display_params({"pattern": "*.py"})
        assert "*.py" in r

    def test_with_path(self):
        r = FindFunc.display_params({"pattern": "*.py", "path": "src"})
        assert "in:src" in r

    def test_with_type(self):
        r = FindFunc.display_params({"pattern": "*.py", "type": "file"})
        assert "type:file" in r

    def test_with_path_and_type(self):
        r = FindFunc.display_params({"pattern": "*.py", "path": "src", "type": "dir"})
        assert "in:src" in r
        assert "type:dir" in r

    def test_empty_pattern(self):
        r = FindFunc.display_params({"pattern": ""})
        # should not crash
        assert isinstance(r, str)

    def test_sanitize_newline(self):
        r = FindFunc.display_params({"pattern": "a\nb"})
        assert "/n" in r

    def test_long_pattern_not_truncated(self):
        """长 pattern 不再被截断，返回完整内容。"""
        long_pattern = "a" * 100
        r = FindFunc.display_params({"pattern": long_pattern}, max_len=20)
        assert "a" * 100 in r


# ═══════════════════════════════════════════════════════════════════════════
# 8. display
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestDisplay:
    """FindFunc.display 显示。"""

    async def test_display_returns_result(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "test.py").write_text("")
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.display()
        assert "test.py" in result
        assert "找到" in result

    async def test_display_not_found(self, tmp_path):
        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.display()
        assert "未找到结果" in result

    async def test_display_error_path(self, tmp_path):
        fake = str(tmp_path / "nonexistent")
        f = FindFunc(pattern="*.py", path=fake)
        result = await f.display()
        assert "路径不存在" in result


# ═══════════════════════════════════════════════════════════════════════════
# 9. 集成测试
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
class TestIntegration:
    """FindFunc 集成场景测试。"""

    async def test_find_only_py_in_subdir(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("")
        (src / "utils.py").write_text("")
        (tmp_path / "README.md").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "main.py" in result
        assert "utils.py" in result
        # README.md 不是 .py 文件，不应被匹配

    async def test_find_dirs_only(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        tests = tmp_path / "tests"
        tests.mkdir()
        (tmp_path / "file.txt").write_text("")

        f = FindFunc(pattern="*", path=str(tmp_path), type="dir")
        result = await f.execute()
        assert "src" in result
        assert "tests" in result
        assert "file.txt" not in result

    async def test_or_pattern_integration(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "a.py").write_text("")
        (sub / "b.json").write_text("")
        (sub / "c.txt").write_text("")

        f = FindFunc(pattern="*.py *.json", path=str(tmp_path))
        result = await f.execute()
        assert "a.py" in result
        assert "b.json" in result
        assert "c.txt" not in result

    async def test_depth_1_excludes_deeper(self, tmp_path):
        """depth=1 时 depth>1 的条目被排除。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        deep = sub / "deep"
        deep.mkdir()
        (deep / "hidden.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path), depth=1)
        result = await f.execute()
        assert "hidden.py" not in result

    async def test_excluded_dir_content_hidden(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "pkg.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "未找到结果" in result or "pkg.py" not in result

    async def test_find_by_name_in_subdir(self, tmp_path):
        """在指定子目录中查找（文件放在子目录的子目录中）。"""
        sub = tmp_path / "sub"
        sub.mkdir()
        inner = sub / "inner"
        inner.mkdir()
        (inner / "target.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(sub))
        result = await f.execute()
        assert "target.py" in result

    async def test_root_level_files_matched_integration(self, tmp_path):
        """集成测试：根目录层级的文件应被 find 匹配。"""
        (tmp_path / "root_alpha.py").write_text("")
        (tmp_path / "root_beta.py").write_text("")
        (tmp_path / "root_note.txt").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "root_alpha.py" in result
        assert "root_beta.py" in result
        assert "root_note.txt" not in result

    async def test_root_level_star_all_files_integration(self, tmp_path):
        """集成测试：* 通配符在根目录层级匹配所有文件。"""
        (tmp_path / "data.csv").write_text("")
        (tmp_path / "index.html").write_text("")

        f = FindFunc(pattern="*", path=str(tmp_path), type="file")
        result = await f.execute()
        assert "data.csv" in result
        assert "index.html" in result

    async def test_root_and_subdir_files_together(self, tmp_path):
        """根目录文件和子目录文件应同时被匹配。"""
        (tmp_path / "config.py").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("")

        f = FindFunc(pattern="*.py", path=str(tmp_path))
        result = await f.execute()
        assert "config.py" in result
        assert "main.py" in result  # 格式化输出中为 src/main.py
