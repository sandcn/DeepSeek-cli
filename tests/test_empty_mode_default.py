"""空模式默认开启回归测试。

用户需求：默认进入空模式——启动时主 Agent 系统提词直接加载
prompts_export_main_empty.md（仅基础安全/通用规范），Ctrl+B 可切回
标准模式（prompts_export_main.md）。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

import src.prompt_builder.builder as builder

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EMPTY_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "prompts_export_main_empty.md"
_MAIN_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "prompts_export_main.md"


@pytest.fixture
def restore_empty_mode():
    original = builder._EMPTY_MODE
    yield
    builder._EMPTY_MODE = original


@pytest.fixture
def isolate_runtime_sections(monkeypatch):
    """隔离运行时动态章节（环境/Git/技能/MCP），只验证提词文件加载。"""
    monkeypatch.setattr(builder, "build_work_md", lambda *a, **k: "")
    monkeypatch.setattr(builder, "build_environment_info", lambda *a, **k: "")
    monkeypatch.setattr(builder, "build_skills_prompt_section", lambda *a, **k: "")
    monkeypatch.setattr(builder, "_build_mcp_section", lambda *a, **k: "")


def test_fresh_import_defaults_to_empty_mode(restore_empty_mode):
    """模块重新导入（等价启动加载）后默认即为空模式。"""
    importlib.reload(builder)
    assert builder.is_empty_mode() is True


def test_toggle_switches_between_modes(restore_empty_mode):
    """默认空模式下 Ctrl+B 切出为标准模式，再切换回空模式。"""
    builder.set_empty_mode(True)
    assert builder.toggle_empty_mode() is False
    assert builder.is_empty_mode() is False
    assert builder.toggle_empty_mode() is True
    assert builder.is_empty_mode() is True


def test_build_system_prompt_defaults_to_empty_prompt(monkeypatch, restore_empty_mode):
    """默认（空模式）build_system_prompt 选择 prompts_export_main_empty。"""
    builder.set_empty_mode(True)
    captured = {}

    def fake_build(agent_name, export_name, fallback, *args, **kwargs):
        captured["agent"] = agent_name
        captured["export"] = export_name
        return ["x"]

    monkeypatch.setattr(builder, "_build_prompt", fake_build)
    assert builder.build_system_prompt() == ["x"]
    assert captured == {"agent": "main", "export": "prompts_export_main_empty"}


def test_standard_mode_uses_main_prompt(monkeypatch, restore_empty_mode):
    """退出空模式后 build_system_prompt 选择 prompts_export_main。"""
    builder.set_empty_mode(False)
    captured = {}

    def fake_build(agent_name, export_name, fallback, *args, **kwargs):
        captured["export"] = export_name
        return ["x"]

    monkeypatch.setattr(builder, "_build_prompt", fake_build)
    assert builder.build_system_prompt() == ["x"]
    assert captured["export"] == "prompts_export_main"


def test_build_system_prompt_loads_empty_prompt_content(
    restore_empty_mode, isolate_runtime_sections,
):
    """实际加载的首段提词 = prompts_export_main_empty.md 内容（非标准提词）。"""
    builder.set_empty_mode(True)
    parts = builder.build_system_prompt(cwd=str(_PROJECT_ROOT))
    assert parts
    assert parts[0] == _EMPTY_PROMPT_PATH.read_text(encoding="utf-8")
    assert parts[0] != _MAIN_PROMPT_PATH.read_text(encoding="utf-8")
