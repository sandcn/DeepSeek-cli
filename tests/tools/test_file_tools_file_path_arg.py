"""read / write / edit 的路径参数契约测试（file_path）。

2026-10：read/write/edit 的模型侧路径参数由 ``path`` 改名为 ``file_path``
（``read_image`` 同时删除 ``path`` 参数，见 test_read_image.py）。

覆盖：
- to_tool_schema 声明 ``file_path`` 且不再声明 ``path``
- from_args 把 ``file_path`` 映射到内部构造参数 ``path``
- 旧参数名 ``path`` 被拒绝（缺 file_path 报错）
"""

from __future__ import annotations

import pytest

from src.tools.edit import EditFunc
from src.tools.read import ReadFunc
from src.tools.write import WriteFunc

#: (工具类, 除路径外的必需参数) —— 用于 schema / from_args 参数化
_FILE_TOOLS = [
    (ReadFunc, {}),
    (WriteFunc, {"content": "x"}),
    (EditFunc, {"old_string": "a", "new_string": "b"}),
]


@pytest.mark.parametrize("cls,extra", _FILE_TOOLS)
def test_schema_declares_file_path(cls, extra):
    """schema 的属性与 required 都使用 file_path，不再出现 path。"""
    params = cls.to_tool_schema()["function"]["parameters"]
    assert "file_path" in params["properties"]
    assert "path" not in params["properties"]
    assert params["required"][0] == "file_path"


@pytest.mark.parametrize("cls,extra", _FILE_TOOLS)
def test_from_args_maps_file_path_to_path(cls, extra):
    """from_args 接受 file_path 并映射到内部构造参数 path。"""
    func = cls.from_args({"file_path": "a.py", **extra})
    assert func.path == "a.py"


@pytest.mark.parametrize("cls,extra", _FILE_TOOLS)
def test_legacy_path_argument_rejected(cls, extra):
    """旧参数名 path 不再被接受（按缺 file_path 报错）。"""
    with pytest.raises(ValueError):
        cls.from_args({"path": "a.py", **extra})


def test_write_from_args_keeps_content():
    func = WriteFunc.from_args({"file_path": "a.py", "content": "hi"})
    assert func.content == "hi"


def test_edit_from_args_keeps_replace_options():
    func = EditFunc.from_args({
        "file_path": "a.py",
        "old_string": "a",
        "new_string": "b",
        "replace_all": True,
        "use_regex": True,
        "regex_flags": "i",
    })
    assert func.replace_all is True
    assert func.use_regex is True
    assert func.regex_flags == "i"
