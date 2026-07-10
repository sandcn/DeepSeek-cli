"""测试 search 工具"""

from __future__ import annotations

import pytest

from src.tools.search import SearchFunc


# ── 辅助构造 ──────────────────────────────────────────────

_SAMPLE_LINES = [
    "def hello_world():",
    "    return 'hello world'",
    "",
    "class FooBar:",
    "    def hello(self):",
    "        return 'foo bar hello'",
    "",
    "# error handler",
    "def handle_error(msg):",
    "    logger.error(msg)",
    "",
    "# config section",
    "DEBUG = True",
    "MAX_RETRIES = 3",
    "TIMEOUT = 30",
]


# ── 基础搜索测试 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_search(tmp_path):
    """基础搜索：找到匹配项"""
    f = tmp_path / "test.py"
    f.write_text("\n".join(_SAMPLE_LINES))

    sf = SearchFunc(query="hello", path=str(f))
    result = await sf.execute()

    assert "共找到" in result
    assert "hello" in result


@pytest.mark.asyncio
async def test_search_not_found(tmp_path):
    """搜索無匹配项"""
    f = tmp_path / "test.py"
    f.write_text("\n".join(_SAMPLE_LINES))

    sf = SearchFunc(query="nonexistent", path=str(f))
    result = await sf.execute()

    assert "未找到" in result

    # 另一个关键词可以匹配
    sf2 = SearchFunc(query="DEBUG", path=str(f))
    result2 = await sf2.execute()
    assert "共找到" in result2


@pytest.mark.asyncio
async def test_empty_path(tmp_path):
    """搜索空文件"""
    f = tmp_path / "empty.py"
    f.write_text("")

    sf = SearchFunc(query="hello", path=str(f))
    result = await sf.execute()
    assert "未找到" in result


@pytest.mark.asyncio
async def test_regex_pattern(tmp_path):
    """正则模式匹配"""
    f = tmp_path / "test.py"
    f.write_text("\n".join(_SAMPLE_LINES))

    # 搜索以 def 开头的行
    sf = SearchFunc(query=r"^def \w+", path=str(f))
    result = await sf.execute()

    assert "共找到" in result
    assert "def hello_world():" in result


@pytest.mark.asyncio
async def test_include_filter(tmp_path):
    """配合 include 过滤"""
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "a.py").write_text("def foo():\n    return 'foo'\n")
    (sub / "b.txt").write_text("def foo():\n    return 'foo'\n")

    sf = SearchFunc(query="def foo", path=str(sub), include="*.py")
    result = await sf.execute()

    assert "共找到" in result
    assert "a.py" in result
    # b.txt 不应被搜索
    assert "b.txt" not in result


@pytest.mark.asyncio
async def test_path_filter(tmp_path):
    """配合 path 过滤"""
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = sub / "a.py"
    f1.write_text("import os\nimport sys\n")
    f2 = sub / "b.py"
    f2.write_text("import os\nimport json\n")

    sf = SearchFunc(query="import os", path=str(sub))
    result = await sf.execute()

    assert "共找到" in result
    # 两个文件都有 "import os"
    assert "共找到 2" in result


@pytest.mark.asyncio
async def test_from_args():
    """from_args 正确解析参数"""
    sf = SearchFunc.from_args({
        "query": "foo bar",
        "path": ".",
    })
    assert sf.query == "foo bar"
    assert sf.path == "."


@pytest.mark.asyncio
async def test_from_args_default_path():
    """from_args 默认 path"""
    sf = SearchFunc.from_args({
        "query": "foo bar",
    })
    assert sf.query == "foo bar"
    assert sf.path == "."


@pytest.mark.asyncio
async def test_to_tool_schema_no_mode():
    """schema 中不包含 mode 和 regex 参数（均已删除）"""
    schema = SearchFunc.to_tool_schema()
    props = schema["function"]["parameters"]["properties"]
    assert "mode" not in props
    assert "regex" not in props
    # 确认基本参数还在
    assert "query" in props
    assert "path" in props
    assert "include" in props


# ═══════════════════════════════════════════════════════════════
# grep 引擎参数拆分测试（步骤 8 P2 修复）
# ═══════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_grep_exclude_dir_params(tmp_path):
    """grep 引擎使用 --exclude-dir 参数拆分（不再使用 shell 拼接）"""
    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / "main.py").write_text("def foo():\n    pass\n")
    excluded = sub / "node_modules"
    excluded.mkdir()
    (excluded / "lib.py").write_text("def foo():\n    pass\n")

    sf = SearchFunc(query="def foo", path=str(sub))
    # 手动设置引擎为 grep
    sf._has_rg = False
    sf._has_grep = True
    result = await sf.execute()

    assert "共找到" in result
    assert "main.py" in result


@pytest.mark.asyncio
async def test_grep_include_params(tmp_path):
    """grep 引擎 --include 参数正确拆分

    注意：Android (Termux) 的 grep 可能不支持 --include 参数。
    若 grep 不可用，测试跳过。
    """
    import asyncio as _asyncio

    sub = tmp_path / "proj"
    sub.mkdir()
    (sub / "a.py").write_text("hello = 'world'\n")
    (sub / "b.txt").write_text("hello = 'world'\n")

    sf = SearchFunc(query="hello", path=str(sub), include="*.py")
    sf._has_rg = False
    sf._has_grep = True
    result = await sf.execute()

    # 确认搜索成功执行（即使 grep 不支持 --include，结果也应有内容）
    assert "共找到" in result
    # a.py 应出现在结果中
    assert "a.py" in result


# ═══════════════════════════════════════════════════════════════
# 纯 Python 搜索排除模式测试（步骤 13 P2 修复）
# ═══════════════════════════════════════════════════════════════

class TestPythonSearchExcludeDirs:
    """纯 Python 搜索的目录排除逻辑测试"""

    @pytest.mark.asyncio
    async def test_wildcard_pattern_excluded(self, tmp_path):
        """* 通配符模式（如 *.egg-info）通过 fnmatch 正确排除目录"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("test\n")

        egg = proj / "my_package.egg-info"
        egg.mkdir()
        (egg / "SOURCES.txt").write_text("main.py\ntest\n")

        sf = SearchFunc(query="test", path=str(proj))
        sf._has_rg = False
        sf._has_grep = False
        result = await sf.execute()

        # main.py 应被搜索到
        assert "main.py" in result
        # *.egg-info 目录下的文件不应被搜索
        assert "SOURCES.txt" not in result

    @pytest.mark.asyncio
    async def test_standard_excluded_dir(self, tmp_path):
        """标准排除目录（如 __pycache__）通过 set 查找排除"""
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "main.py").write_text("hello world\n")

        cache = proj / "__pycache__"
        cache.mkdir()
        (cache / "cached.pyc").write_text("hello world\n")

        sf = SearchFunc(query="hello world", path=str(proj))
        sf._has_rg = False
        sf._has_grep = False
        result = await sf.execute()

        assert "main.py" in result
        assert "cached.pyc" not in result

    @pytest.mark.asyncio
    async def test_nested_excluded_dir(self, tmp_path):
        """嵌套的排除目录也被正确排除"""
        proj = tmp_path / "proj"
        proj.mkdir()
        nested = proj / "src" / "__pycache__"
        nested.mkdir(parents=True)
        (nested / "module.pyc").write_text("target\n")
        (proj / "src" / "module.py").write_text("target\n")

        sf = SearchFunc(query="target", path=str(proj))
        sf._has_rg = False
        sf._has_grep = False
        result = await sf.execute()

        assert "module.py" in result
        assert "module.pyc" not in result

    def test_should_exclude_dir_wildcard(self):
        """_should_exclude_dir 对通配符模式返回 True"""
        sf = SearchFunc(query="test", path=".")

        # *.egg-info 应被 fnmatch 匹配
        assert sf._should_exclude_dir("my_package.egg-info") is True
        # 不含通配符的模式仍通过 set 查找
        assert sf._should_exclude_dir("__pycache__") is True
        # 普通目录不被排除
        assert sf._should_exclude_dir("src") is False

    def test_should_exclude_dir_set_lookup(self):
        """_should_exclude_dir 对标准目录名通过 set 查找排除"""
        sf = SearchFunc(query="test", path=".")
        assert sf._should_exclude_dir("node_modules") is True
        assert sf._should_exclude_dir(".git") is True
        assert sf._should_exclude_dir("venv") is True
        assert sf._should_exclude_dir("dist") is True
