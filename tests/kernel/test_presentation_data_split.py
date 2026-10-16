"""表现层数据表拆分守卫（``presentation_data`` ↔ ``presentation_data_tables``）。

``src/presentation_data.py`` 曾把「数据内容」与「注册表机制」混在 1391 行里；
现拆为：

- ``src/presentation_data_tables``：纯数据（仅 ``from __future__ import annotations``，
  无 registry / 无 threading / 无副作用）；
- ``src/presentation_data``：注册表 + 访问器，并按名 re-export 全部数据常量
  （旧导入路径不变）。

本测试固化：re-export 同一对象、数据模块无副作用、注册表仍能取到默认表。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src import presentation_data as pd
from src import presentation_data_tables as pdt


def test_tables_module_exports_only_data():
    """数据模块的 __all__ 全部指向模块级数据常量（无函数/类）。"""
    for name in pdt.__all__:
        obj = getattr(pdt, name)
        assert not callable(obj), f"{name} 不应是可调用对象（数据模块只放数据）"


def test_tables_module_has_no_heavy_imports():
    tree = ast.parse(Path(pdt.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            imported.append(node.module or "")
    assert imported == [], f"数据模块不应有导入（当前: {imported}）"
    assert not any(isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                   for n in tree.body), "数据模块不应定义类/函数"


def test_registry_module_reexports_same_objects():
    for name in pdt.__all__:
        assert getattr(pd, name) is getattr(pdt, name), name


@pytest.mark.parametrize("table_id,expected_type", [
    ("emoji", dict),
    ("circled_digits", list),
    ("bullet", list),
    ("trace_kind", dict),
    ("model_keymap", list),
])
def test_default_tables_still_registered(table_id, expected_type):
    spec = pd.default_data_table(table_id)
    assert spec is not None, f"内置表 {table_id} 未注册"
    assert isinstance(spec.data, expected_type)


def test_accessors_return_registered_data():
    assert pd.emoji_map() == pdt.EMOJI_MAP
    assert pd.circled_digits() == pdt.CIRCLED_DIGITS
    assert pd.html_tag_colors() == pdt.HTML_TAG_COLORS


def test_legacy_constant_import_path_still_works():
    """历史用例经 ``from src.presentation_data import <CONST>`` 取原始常量。"""
    for name in ("CONFIG_ENTRY_DESC_MAP", "CONFIG_ENTRY_OPTION_MAP",
                 "MODEL_KEYMAP_DATA", "TOOL_DISPLAY_NAME_MAP", "UI_DEFAULTS_DATA"):
        assert hasattr(pd, name), f"presentation_data 不再导出 {name}"


def test_registry_module_stays_reasonably_small():
    lines = len(Path(pd.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 700, f"presentation_data 回涨到 {lines} 行，数据应放 presentation_data_tables"


def test_all_builtin_specs_reference_existing_data():
    """每张内置表的 data 必须是非空值（防拆分后漏接某个常量）。"""
    for spec in pd._BUILTIN_SPECS:
        assert spec.data is not None, f"表 {spec.id} 的 data 为 None"
        assert len(spec.data) > 0, f"表 {spec.id} 的 data 为空"
