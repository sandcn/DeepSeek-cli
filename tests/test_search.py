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
