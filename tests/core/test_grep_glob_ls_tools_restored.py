"""grep / glob / ls 工具还原回归测试。

需求：还原此前被移除的三个内置工具 —— ``grep``（``GrepFunc``）、
``glob``（``GlobFunc``）、``ls``（``LsFunc``）。工具集重新注册三者，并且
源码、提示词、清单、元数据、显示名、共享常量、调度路径与表现层引用全部恢复。
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from tests._paths import repo_root

ROOT = repo_root()
_RESTORED = ("grep", "glob", "ls")


# ── 注册与包导出 ──────────────────────────────────────


def test_restored_tools_in_builtin_tools():
    from src.tools.registry import discover_builtin_tools

    names = discover_builtin_tools()
    for tool in _RESTORED:
        assert tool in names


def test_builtin_tool_count_is_19():
    from src.tools.registry import discover_builtin_tools

    assert len(discover_builtin_tools()) == 19


def test_restored_tools_in_registry_tools():
    from src.tools.registry import get_tools

    names = get_tools()
    for tool in _RESTORED:
        assert tool in names


def test_tools_package_exports_restored_classes():
    import src.tools as tools

    assert tools.Grep.__name__ == "GrepFunc"
    assert tools.Glob.__name__ == "GlobFunc"
    assert tools.Ls.__name__ == "LsFunc"
    assert {"Grep", "Glob", "Ls"} <= set(tools.__all__)


@pytest.mark.parametrize("module", ["src.tools.grep", "src.tools.glob", "src.tools.ls"])
def test_tool_modules_importable(module):
    assert importlib.import_module(module) is not None


# ── 工具 schema ──────────────────────────────────────


@pytest.mark.parametrize(
    "module,class_name,tool_name,required",
    [
        ("src.tools.grep", "GrepFunc", "grep", ["query"]),
        ("src.tools.glob", "GlobFunc", "glob", ["pattern"]),
        ("src.tools.ls", "LsFunc", "ls", []),
    ],
)
def test_tool_class_name_and_schema(module, class_name, tool_name, required):
    cls = getattr(importlib.import_module(module), class_name)
    assert cls.name == tool_name
    schema = cls.to_tool_schema()["function"]
    assert schema["name"] == tool_name
    assert schema["parameters"]["required"] == required
    assert callable(cls.display_params)


# ── 清单条目 ─────────────────────────────────────────


def test_manifest_has_restored_tool_entries():
    from src.plugins.manifest import (
        TOOL_METADATA_ENTRIES,
        TOOL_PLUGIN_ENTRIES,
        TOOL_STYLE_ENTRIES,
    )

    plugins = {e["id"]: e for e in TOOL_PLUGIN_ENTRIES}
    assert plugins["tool_grep"]["config"]["tool"] == "src.tools.grep.GrepFunc"
    assert plugins["tool_glob"]["config"]["tool"] == "src.tools.glob.GlobFunc"
    assert plugins["tool_ls"]["config"]["tool"] == "src.tools.ls.LsFunc"
    for tool in _RESTORED:
        assert plugins[f"tool_{tool}"]["config"]["name"] == tool

    meta_names = {(e.get("config") or {}).get("name") for e in TOOL_METADATA_ENTRIES}
    assert set(_RESTORED) <= meta_names

    style_ids = {(e.get("config") or {}).get("id") for e in TOOL_STYLE_ENTRIES}
    assert "tool_glob" in style_ids


def test_metadata_registry_has_restored_tools():
    from src.tools.metadata_registry import BUILTIN_TOOL_METADATA

    for tool in _RESTORED:
        meta = BUILTIN_TOOL_METADATA[tool]
        assert meta["tool_category"] == "read"
        assert meta["parallel_safe"] is True
        assert meta["requires_network"] is False


# ── 表现层 ───────────────────────────────────────────


def test_display_name_mapping_restored():
    from src.presentation_data import tool_display_name_map

    mapping = tool_display_name_map()
    assert mapping["glob"] == "Glob"
    assert mapping["ls"] == "Ls"
    assert mapping["grep"] == "Grep"


def test_tool_head_tools_restored():
    from src.presentation_data import ui_default
    from src.tui.app._model_helpers import _TOOL_HEAD_TOOLS, _TOOL_TAIL_TOOLS

    expected = ["read"]
    assert ui_default("tool_head_tools") == expected
    assert _TOOL_HEAD_TOOLS == tuple(expected)
    # ★ 2026-10-10 用户需求：grep / glob 由头显示改为尾显示（「… 前 N 行省略」）。
    assert "grep" not in _TOOL_HEAD_TOOLS
    assert "glob" not in _TOOL_HEAD_TOOLS
    assert "grep" in _TOOL_TAIL_TOOLS
    assert "glob" in _TOOL_TAIL_TOOLS
    # ★ 2026-10-13 用户需求：ls 由头显示改为尾显示（「… 前 N 行省略」）。
    assert "ls" not in _TOOL_HEAD_TOOLS
    assert "ls" in _TOOL_TAIL_TOOLS
    assert ui_default("tool_tail_tools") == [
        "bash", "execute_command", "grep", "glob", "ls",
    ]


def test_glob_tool_style_restored():
    from src.tui import _tool_styles as styles

    assert styles.tool_category("glob") == "search"
    assert styles.tool_icon("glob") == "\u2315"


# ── 共享常量 ─────────────────────────────────────────


def test_shared_constants_restored():
    from src.tools import _constants as C

    assert "node_modules" in C.EXCLUDED_DIRS
    assert ".git" in C.excluded_dirs()
    assert "*.o" in C.EXCLUDED_FILE_PATTERNS
    assert "*.so" in C.excluded_file_patterns()
    assert "node_modules" in C.rg_exclude_globs()
    assert "*.o" in C.rg_exclude_globs()
    assert "node_modules" in C.grep_exclude_dirs()
    assert "*.o" in C.grep_exclude_files()
    assert C.should_exclude_dir("__pycache__") is True
    assert C.should_exclude_dir("foo.egg-info") is True
    assert C.should_exclude_dir("src") is False


def test_param_formatter_restored_key_params():
    from src.core.param_formatter import extract_key_params

    assert extract_key_params("grep", {"query": "foo", "path": "src"}) == "foo src"
    assert extract_key_params("glob", {"pattern": "*.py"}) == "*.py"
    assert extract_key_params("ls", {"path": "."}) == "."


def test_display_result_template_restored():
    from src.tools.base import Func

    assert asyncio.iscoroutinefunction(Func._display_result_template)


# ── 调度与策略引用 ───────────────────────────────────


def test_tool_dag_treats_restored_tools_as_read():
    from src.core.tool_dag import ToolDAG

    cases = (
        ("grep", {"query": "x", "path": "src"}),
        ("glob", {"pattern": "*.py", "path": "src"}),
        ("ls", {"path": "src"}),
    )
    for tool, args in cases:
        write_paths, read_paths, delete_paths = ToolDAG._extract_tool_paths(tool, args)
        assert read_paths, tool
        assert not write_paths, tool
        assert not delete_paths, tool


def test_subagent_and_policy_reference_restored_tools():
    import src.tools.tool_policy as policy
    from src.tools.subagent import SubagentFunc

    assert "read/grep/glob/ls" in (policy.__doc__ or "")
    desc = SubagentFunc.to_tool_schema()["function"]["parameters"]["properties"]["type"]["description"]
    assert "read/grep/glob/ls" in desc


def test_tool_descriptions_reference_restored_tools():
    from src.tools.bash import BashFunc
    from src.tools.rm import RmFunc
    from src.tools.edit import EditFunc

    bash_desc = BashFunc.to_tool_schema()["function"]["description"]
    assert "搜索用 grep、找文件用 glob/ls" in bash_desc
    assert "grep" in RmFunc.to_tool_schema()["function"]["description"]
    assert "grep" in EditFunc.to_tool_schema()["function"]["description"]


def test_fallback_prompts_reference_grep_tool():
    from src.prompt_builder.builder import _FALLBACK_MAIN_PROMPT, _FALLBACK_SUB_PROMPT

    for text in (_FALLBACK_MAIN_PROMPT, _FALLBACK_SUB_PROMPT):
        assert "使用 grep 搜索代码" in text


# ── 文档引用 ─────────────────────────────────────────


def test_prompt_docs_reference_restored_tools():
    files = sorted((ROOT / "prompts").glob("prompts_export_*.md"))
    assert files, "未找到 prompts_export_*.md"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert "grep" in text, f"{path.name} 未引用 grep 工具"
        assert "glob" in text, f"{path.name} 未引用 glob 工具"
        assert "ls" in text, f"{path.name} 未引用 ls 工具"


def test_readme_references_restored_tools():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "`grep`" in text
    assert "`glob`" in text
    assert "`ls`" in text


# ── 功能行为 ─────────────────────────────────────────


async def test_glob_executes(tmp_path):
    from src.tools.glob import GlobFunc

    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y", encoding="utf-8")
    output = await GlobFunc(pattern="*.py", path=str(tmp_path)).execute()
    assert "a.py" in output
    assert "b.py" in output


async def test_ls_executes(tmp_path):
    from src.tools.ls import LsFunc

    (tmp_path / "hello.txt").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    output = await LsFunc(path=str(tmp_path)).execute()
    assert "hello.txt" in output
    assert "subdir/" in output


async def test_grep_executes_python_engine(tmp_path):
    from src.tools.grep import GrepFunc

    (tmp_path / "m.py").write_text("def target_func():\n    pass\n", encoding="utf-8")
    tool = GrepFunc(query="target_func", path=str(tmp_path))
    tool._has_rg = False
    tool._has_grep = False
    output = await tool.execute()
    assert "target_func" in output
