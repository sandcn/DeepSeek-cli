"""bash_opt schema 抽离守卫。

``BashOptFunc.to_tool_schema`` 曾是单函数 880 行、几乎全部为 JSON Schema
字面量；现已抽到 ``src/tools/_bash_opt_schema.py``。本测试固化抽离后的
契约（内容等价、返回全新对象、模块无重依赖）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.tools._bash_opt_schema import build_bash_opt_schema
from src.tools.bash_opt import BashOptFunc


def test_schema_shape_and_required_fields():
    schema = build_bash_opt_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "bash_opt"
    # 只有 op 是绝对必需；task_id 对 clipboard / record / release / 带 path 的
    # annotate / screen=… 的 screenshot 可省略（执行层按 op 再校验）。
    assert fn["parameters"]["required"] == ["op"]
    assert "task_id" in fn["parameters"]["properties"]
    assert isinstance(fn["description"], str) and fn["description"]


def test_op_enum_covers_all_documented_ops():
    props = build_bash_opt_schema()["function"]["parameters"]["properties"]
    enum = props["op"]["enum"]
    for op in ("read", "wait", "kill", "stdin", "keys", "screenshot",
               "windows", "window", "elements", "wait_window", "clipboard",
               "locate", "pixel", "annotate", "record", "replay", "sequence"):
        assert op in enum, f"op 枚举缺少 {op}"
    # INPUT_OPS（GUI 输入类）来自单一真源
    from src.tools._window_input.action import INPUT_OPS

    for op in INPUT_OPS:
        assert op in enum


def test_schema_returns_fresh_objects():
    first = build_bash_opt_schema()
    second = build_bash_opt_schema()
    assert first == second
    assert first is not second
    assert first["function"] is not second["function"]
    # 修改一个不应影响另一个
    first["function"]["name"] = "mutated"
    assert build_bash_opt_schema()["function"]["name"] == "bash_opt"


def test_tool_class_delegates_to_schema_module():
    assert BashOptFunc.to_tool_schema() == build_bash_opt_schema()


def test_schema_module_avoids_heavy_imports_at_module_level():
    """schema 模块只应声明数据（+ 延迟导入 INPUT_OPS），不得在导入期拉起工具链。"""
    from src.tools import _bash_opt_schema

    tree = ast.parse(Path(_bash_opt_schema.__file__).read_text(encoding="utf-8"))
    module_level_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level_imports.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module != "__future__":
                module_level_imports.append(node.module or "")
    # 仅允许 typing（无第三方、无 src.* 重量模块）
    assert module_level_imports == ["typing"], module_level_imports


def test_to_tool_schema_stays_small():
    """``to_tool_schema`` 不应再回涨为巨型字面量函数。"""
    from src.tools import bash_opt

    tree = ast.parse(Path(bash_opt.__file__).read_text(encoding="utf-8"))
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "to_tool_schema":
            target = node
    assert target is not None, "bash_opt 未定义 to_tool_schema"
    length = (target.end_lineno or target.lineno) - target.lineno + 1
    assert length <= 10, f"to_tool_schema 回涨到 {length} 行，schema 字面量应放 _bash_opt_schema"


def test_every_property_has_description():
    props = build_bash_opt_schema()["function"]["parameters"]["properties"]
    missing = [name for name, spec in props.items() if not spec.get("description")]
    assert not missing, f"以下参数缺少 description: {missing}"


def test_documented_enum_values_are_present():
    props = build_bash_opt_schema()["function"]["parameters"]["properties"]
    assert props["button"]["enum"] == ["left", "right", "middle"]
    assert set(props["phase"]["enum"]) == {"press", "down", "up"}
    assert set(props["method"]["enum"]) == {"auto", "sendinput", "message"}
    assert props["on_error"]["enum"] == ["stop", "continue"]
    assert set(props["mode"]["enum"]) == {"point", "region", "find"}
    assert "restore_geometry" in props["window_action"]["enum"]


@pytest.mark.parametrize("flag_key", ["relative_event", "restore_clipboard", "append", "diff"])
def test_boolean_flags_are_booleans(flag_key):
    props = build_bash_opt_schema()["function"]["parameters"]["properties"]
    assert props[flag_key]["type"] == "boolean"
