"""ls / find 工具删除回归测试（2026-10-05）。

需求：从项目中彻底删除目录列举工具 ``ls``（``LsFunc``）与文件查找工具
``find``（``FindFunc``）——工具集不再注册二者，源码 / 提示词 / 清单 / 元数据 /
显示名 / 共享常量 / 工具表现均不再残留这两个工具。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_REMOVED = ("ls", "find")


def test_ls_find_not_in_builtin_tools():
    from src.tools.registry import discover_builtin_tools

    names = discover_builtin_tools()
    for tool in _REMOVED:
        assert tool not in names


def test_builtin_tool_count_is_16():
    from src.tools.registry import discover_builtin_tools

    assert len(discover_builtin_tools()) == 16


def test_ls_find_not_in_registry_tools():
    from src.tools.registry import get_tools

    names = get_tools()
    for tool in _REMOVED:
        assert tool not in names


def test_tools_package_has_no_ls_find_export():
    import src.tools as tools

    assert not hasattr(tools, "Ls")
    assert not hasattr(tools, "Find")
    assert "Ls" not in tools.__all__
    assert "Find" not in tools.__all__


@pytest.mark.parametrize("module", ["src.tools.ls", "src.tools.find"])
def test_tool_module_removed(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_manifest_has_no_ls_find_entry():
    from src.plugins.manifest import (
        TOOL_METADATA_ENTRIES,
        TOOL_PLUGIN_ENTRIES,
        TOOL_STYLE_ENTRIES,
    )

    assert all(e["id"] not in ("tool_ls", "tool_find") for e in TOOL_PLUGIN_ENTRIES)
    assert all(
        (e.get("config") or {}).get("name") not in _REMOVED
        for e in TOOL_PLUGIN_ENTRIES
    )
    assert all(
        (e.get("config") or {}).get("name") not in _REMOVED
        for e in TOOL_METADATA_ENTRIES
    )
    assert all(
        (e.get("config") or {}).get("id") not in ("tool_ls", "tool_find")
        for e in TOOL_STYLE_ENTRIES
    )


def test_metadata_registry_has_no_ls_find():
    from src.tools.metadata_registry import BUILTIN_TOOL_METADATA

    for tool in _REMOVED:
        assert tool not in BUILTIN_TOOL_METADATA


def test_display_name_mapping_removed():
    from src.presentation_data import tool_display_name_map

    mapping = tool_display_name_map()
    for tool in _REMOVED:
        assert tool not in mapping


def test_tool_head_tools_excludes_ls_find():
    from src.presentation_data import ui_default

    heads = ui_default("tool_head_tools")
    assert "ls" not in heads
    assert "find" not in heads
    assert heads == ["read_file"]


def test_removed_constants_removed():
    from src.tools import _constants as C

    for attr in ("EXCLUDED_DIRS", "excluded_dirs", "should_exclude_dir"):
        assert not hasattr(C, attr), attr


def test_param_formatter_treats_ls_find_as_unknown():
    from src.core.param_formatter import extract_key_params

    assert extract_key_params("ls", {"path": "."}) == "path=."
    assert extract_key_params("find", {"pattern": "*.py"}) == "pattern=*.py"


def test_prompts_do_not_reference_ls_find_tools():
    prompt_dir = ROOT / "prompts"
    files = sorted(prompt_dir.glob("prompts_export_*.md"))
    assert files, "未找到 prompts_export_*.md"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "`ls`" not in text, f"{path.name} 仍引用 ls 工具"
        assert "`find`" not in text, f"{path.name} 仍引用 find 工具"
