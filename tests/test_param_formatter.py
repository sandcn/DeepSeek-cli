"""工具参数格式化测试 — 覆盖 src/core/param_formatter.py。

验证 extract_key_params 的关键参数值提取与紧凑 k=v 输出。
"""

import pytest

from src.core.param_formatter import extract_key_params


def test_extract_read_file_path():
    assert extract_key_params("read_file", {"path": "pyproject.toml"}) == "pyproject.toml"


def test_extract_bash_command():
    assert extract_key_params("bash", {"command": "ls -la"}) == "ls -la"


def test_extract_search_query_path():
    result = extract_key_params("search", {"query": "foo", "path": "src/"})
    assert result == "foo src/"


def test_extract_mv_source_destination():
    result = extract_key_params("mv", {"source": "a", "destination": "b"})
    assert result == "a b"


def test_extract_args_as_json_string():
    assert extract_key_params("read_file", '{"path": "x.py"}') == "x.py"


def test_extract_args_invalid_json_string():
    assert extract_key_params("read_file", "not-json") == "not-json"


def test_extract_empty_dict():
    assert extract_key_params("read_file", {}) == ""


def test_extract_unknown_tool_kv():
    result = extract_key_params("unknown_tool", {"a": 1, "b": 2})
    assert result == "a=1 b=2"


def test_extract_show_all_known_tool():
    result = extract_key_params("read_file", {"path": "x", "extra": "y"}, show_all=True)
    assert "path=x" in result
    assert "extra=y" in result


def test_extract_truncates_long_value():
    result = extract_key_params("read_file", {"path": "a" * 100})
    assert len(result) <= 60


def test_extract_missing_key_returns_empty():
    assert extract_key_params("read_file", {"other": "x"}) == ""
