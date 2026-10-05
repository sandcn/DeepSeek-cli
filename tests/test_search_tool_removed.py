"""search 工具删除回归测试（2026-10-05）。

需求：从项目中彻底删除代码搜索工具 ``search``（``SearchFunc``）——
工具集不再注册 search，源码 / 提示词 / 清单 / 元数据 / 显示名 / 共享常量
均不再残留该工具。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_search_not_in_builtin_tools():
    from src.tools.registry import discover_builtin_tools

    assert "search" not in discover_builtin_tools()


def test_search_not_in_registry_tools():
    from src.tools.registry import get_tools

    get_tools()
    assert "search" not in get_tools()


def test_tools_package_has_no_search_export():
    import src.tools as tools

    assert not hasattr(tools, "Search")
    assert "Search" not in tools.__all__


def test_search_module_removed():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.tools.search")


def test_manifest_has_no_search_entry():
    from src.plugins.manifest import TOOL_METADATA_ENTRIES, TOOL_PLUGIN_ENTRIES

    assert all(e["id"] != "tool_search" for e in TOOL_PLUGIN_ENTRIES)
    assert all(
        (e.get("config") or {}).get("name") != "search" for e in TOOL_PLUGIN_ENTRIES
    )
    assert all(
        (e.get("config") or {}).get("name") != "search" for e in TOOL_METADATA_ENTRIES
    )


def test_metadata_registry_has_no_search():
    from src.tools.metadata_registry import BUILTIN_TOOL_METADATA

    assert "search" not in BUILTIN_TOOL_METADATA


def test_search_display_name_mapping_removed():
    from src.presentation_data import tool_display_name_map

    assert "search" not in tool_display_name_map()


def test_search_specific_constants_removed():
    from src.tools import _constants as C

    assert not hasattr(C, "EXCLUDED_FILE_PATTERNS")
    assert not hasattr(C, "excluded_file_patterns")
    assert not hasattr(C, "rg_exclude_globs")
    assert not hasattr(C, "grep_exclude_dirs")
    assert not hasattr(C, "grep_exclude_files")


def test_prompts_do_not_reference_search_tool():
    prompt_dir = ROOT / "prompts"
    files = sorted(prompt_dir.glob("prompts_export_*.md"))
    assert files, "未找到 prompts_export_*.md"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "`search`" not in text, f"{path.name} 仍引用 search 工具"
        assert "使用 search" not in text, f"{path.name} 仍指导使用 search 工具"
