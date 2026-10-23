"""工具 schema 提词（模型可见 description）回归测试。

覆盖：
- 全部内置工具 schema 可构建且 JSON 可序列化；
- 提词长度预算（功能描述 + 参数短语，防止回涨）；
- 关键调用约定（参数集合、必填项、enum、默认值）不因精简丢失；
- ``user_select`` 的 1 分钟空闲超时与 header 12 字符限制文案保留；
- ``bash_opt`` 的 op 清单在功能描述中给出。
"""

from __future__ import annotations

import json

from src.tools._bash_opt_schema import build_bash_opt_schema
from src.tools.registry import discover_builtin_tools

#: 当前内置工具数量（新增/删除内置工具时同步更新）
_EXPECTED_TOOL_COUNT = 19

#: 单工具 function.description 字符上限（bash_opt 当前 262）
_DESC_LIMIT = 300

#: 全部工具 function.description 总字符上限（当前 884）
_TOTAL_DESC_LIMIT = 1000

#: 参数 description 字符上限（只保留一句功能短语）
_PARAM_DESC_LIMIT = 30


def _schemas():
    return {name: cls.to_tool_schema() for name, cls in discover_builtin_tools().items()}


def _param_props(schema):
    return schema["function"]["parameters"]["properties"]


def test_all_builtin_tool_schemas_build() -> None:
    schemas = _schemas()
    assert len(schemas) == _EXPECTED_TOOL_COUNT
    for name, schema in schemas.items():
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] == name
        assert function["description"].strip()
        assert function["parameters"]["type"] == "object"
        json.dumps(schema, ensure_ascii=False)


def test_description_length_budget() -> None:
    schemas = _schemas()
    total = 0
    for name, schema in schemas.items():
        length = len(schema["function"]["description"])
        assert length <= _DESC_LIMIT, f"{name} 功能描述过长: {length}"
        total += length
        for param, spec in _param_props(schema).items():
            desc = spec.get("description", "")
            assert len(desc) <= _PARAM_DESC_LIMIT, f"{name}.{param} 参数描述过长: {desc!r}"
    assert total <= _TOTAL_DESC_LIMIT, f"功能描述总长 {total} 超预算"


def test_key_parameter_contracts() -> None:
    schemas = _schemas()

    read = schemas["read"]["function"]["parameters"]
    assert read["required"] == ["file_path"]
    assert set(read["properties"]) == {
        "file_path", "start_line", "end_line", "show_line_numbers",
    }

    edit = schemas["edit"]["function"]["parameters"]
    assert edit["required"] == ["file_path", "old_string", "new_string"]
    assert {"replace_all", "use_regex", "regex_flags"} <= set(edit["properties"])

    recursive = schemas["rm"]["function"]["parameters"]["properties"]["recursive"]
    assert recursive["default"] is False

    glob = schemas["glob"]["function"]["parameters"]["properties"]
    assert glob["depth"]["default"] == 0
    assert glob["type"]["enum"] == ["file", "dir"]

    subagent_type = schemas["subagent"]["function"]["parameters"]["properties"]["type"]
    assert subagent_type["enum"] == ["map", "review", "plan", "execute"]

    user_select = schemas["user_select"]["function"]["parameters"]
    assert user_select["required"] == ["title", "options"]
    assert "1 分钟没有任何操作" in schemas["user_select"]["function"]["description"]
    # ★ 2026-10-23（用户需求）：说明并入 options，删除 option_descriptions。
    props = user_select["properties"]
    assert "option_descriptions" not in props
    options_item = props["options"]["items"]
    assert options_item["type"] == "object"
    assert options_item["required"] == ["label"]
    assert "description" in options_item["properties"]


def test_bash_opt_schema_keeps_ops_and_op_list() -> None:
    schema = build_bash_opt_schema()
    function = schema["function"]
    assert function["parameters"]["required"] == ["op"]

    props = function["parameters"]["properties"]
    for key in ("task_id", "op", "timeout", "allow_hidden", "path", "actions"):
        assert key in props

    enum = props["op"]["enum"]
    for op in (
        "read", "wait", "kill", "stdin", "keys", "screenshot", "windows",
        "window", "elements", "wait_window", "clipboard", "locate", "pixel",
        "annotate", "record", "replay", "sequence", "release",
        "click", "move", "type",
    ):
        assert op in enum

    # op 说明收敛为一句，op 清单放在功能描述中
    assert props["op"]["description"] == "要执行的操作"
    description = function["description"]
    for op in ("read", "wait", "kill", "release", "click", "type"):
        assert op in description

    json.dumps(schema, ensure_ascii=False)


def test_key_function_contracts_after_trimming() -> None:
    schemas = _schemas()

    bash_desc = schemas["bash"]["function"]["description"]
    assert "returncode" in bash_desc
    assert "task_id" in bash_desc
    assert "60 秒" in bash_desc

    assert "文件:行号:内容" in schemas["grep"]["function"]["description"]
    assert "image" in schemas["read_image"]["function"]["description"]
    assert "URL" in schemas["web_fetch"]["function"]["description"]

    user_select = schemas["user_select"]
    assert "1 分钟没有任何操作" in user_select["function"]["description"]
    header = user_select["function"]["parameters"]["properties"]["header"]
    assert "12" in header["description"]

    assert "read" in schemas["subagent_opt"]["function"]["description"]
    assert "type" in schemas["subagent"]["function"]["description"]
