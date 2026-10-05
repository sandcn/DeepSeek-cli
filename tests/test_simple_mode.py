"""主 Agent 简单模式回归测试。

用户需求：main agent 增加简单模式并加载 prompts/prompts_export_main_simple.md。
三态循环（Ctrl+B）：空模式 → 简单模式 → 标准模式 → 空模式；默认空模式。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.prompt_builder.builder as builder

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EMPTY_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "prompts_export_main_empty.md"
_SIMPLE_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "prompts_export_main_simple.md"
_MAIN_PROMPT_PATH = _PROJECT_ROOT / "prompts" / "prompts_export_main.md"


@pytest.fixture
def restore_mode():
    original = builder.get_mode()
    yield
    builder.set_mode(original)


@pytest.fixture
def isolate_runtime_sections(monkeypatch):
    """隔离运行时动态章节（环境/Git/技能/MCP），只验证提词文件加载。"""
    monkeypatch.setattr(builder, "build_work_md", lambda *a, **k: "")
    monkeypatch.setattr(builder, "build_environment_info", lambda *a, **k: "")
    monkeypatch.setattr(builder, "build_skills_prompt_section", lambda *a, **k: "")
    monkeypatch.setattr(builder, "_build_mcp_section", lambda *a, **k: "")


# ── 提词文件 ──────────────────────────────────────────────

def test_simple_prompt_file_exists():
    """简单模式提词文件存在且非空。"""
    assert _SIMPLE_PROMPT_PATH.is_file()
    assert _SIMPLE_PROMPT_PATH.read_text(encoding="utf-8").strip()


def test_simple_prompt_differs_from_other_modes():
    """简单模式提词与空模式/标准模式内容均不同。"""
    simple = _SIMPLE_PROMPT_PATH.read_text(encoding="utf-8")
    assert simple != _EMPTY_PROMPT_PATH.read_text(encoding="utf-8")
    assert simple != _MAIN_PROMPT_PATH.read_text(encoding="utf-8")


# ── 模式状态 ──────────────────────────────────────────────

def test_default_mode_is_empty(restore_mode):
    builder.set_mode(builder._MODE_EMPTY)
    assert builder.get_mode() == "empty"
    assert builder.is_empty_mode() is True
    assert builder.is_simple_mode() is False
    assert builder.is_standard_mode() is False


def test_cycle_order_empty_simple_standard(restore_mode):
    """Ctrl+B 循环顺序：空 → 简单 → 标准 → 空。"""
    builder.set_mode(builder._MODE_EMPTY)
    assert builder.cycle_mode() == "simple"
    assert builder.is_simple_mode() is True
    assert builder.cycle_mode() == "standard"
    assert builder.is_standard_mode() is True
    assert builder.cycle_mode() == "empty"
    assert builder.is_empty_mode() is True


def test_set_mode_and_invalid_fallback(restore_mode):
    assert builder.set_mode("simple") == "simple"
    assert builder.set_mode("standard") == "standard"
    assert builder.set_mode("bogus") == "empty"


def test_cycle_mode_from_invalid_state_falls_back(restore_mode):
    """真源被外部改成未知值时，下一次 cycle 从空模式起步。"""
    builder._MODE = "bogus"
    assert builder.cycle_mode() == "empty"


def test_legacy_empty_mode_flag_sync(restore_mode):
    builder.set_mode("simple")
    assert builder._EMPTY_MODE is False
    builder.set_mode("empty")
    assert builder._EMPTY_MODE is True


def test_set_simple_mode(restore_mode):
    builder.set_simple_mode(True)
    assert builder.is_simple_mode() is True
    builder.set_simple_mode(False)
    assert builder.is_standard_mode() is True


def test_toggle_empty_mode_two_state(restore_mode):
    """旧 toggle_empty_mode 保持空 ↔ 标准二态语义（兼容）。"""
    builder.set_mode("empty")
    assert builder.toggle_empty_mode() is False
    assert builder.is_standard_mode() is True
    assert builder.toggle_empty_mode() is True
    assert builder.is_empty_mode() is True


def test_mode_label():
    assert builder.mode_label("empty") == "空模式"
    assert builder.mode_label("simple") == "简单模式"
    assert builder.mode_label("standard") == "标准模式"
    assert builder.mode_label("unknown") == "空模式"
    builder.set_mode("simple")
    assert builder.mode_label() == "简单模式"


# ── build_system_prompt 选择提词文件 ──────────────────────

def test_build_system_prompt_uses_simple_prompt(monkeypatch, restore_mode):
    builder.set_mode("simple")
    captured = {}

    def fake_build(agent_name, export_name, fallback, *args, **kwargs):
        captured["agent"] = agent_name
        captured["export"] = export_name
        return ["x"]

    monkeypatch.setattr(builder, "_build_prompt", fake_build)
    assert builder.build_system_prompt() == ["x"]
    assert captured == {"agent": "main", "export": "prompts_export_main_simple"}


def test_build_system_prompt_standard_mode(monkeypatch, restore_mode):
    builder.set_mode("standard")
    captured = {}

    def fake_build(agent_name, export_name, fallback, *args, **kwargs):
        captured["export"] = export_name
        return ["x"]

    monkeypatch.setattr(builder, "_build_prompt", fake_build)
    assert builder.build_system_prompt() == ["x"]
    assert captured["export"] == "prompts_export_main"


def test_build_system_prompt_loads_simple_content(restore_mode, isolate_runtime_sections):
    """简单模式下实际加载的首段提词 = prompts_export_main_simple.md 内容。"""
    builder.set_mode("simple")
    parts = builder.build_system_prompt(cwd=str(_PROJECT_ROOT))
    assert parts
    assert parts[0] == _SIMPLE_PROMPT_PATH.read_text(encoding="utf-8")
    assert parts[0] != _EMPTY_PROMPT_PATH.read_text(encoding="utf-8")
    assert parts[0] != _MAIN_PROMPT_PATH.read_text(encoding="utf-8")


def test_cycle_switches_prompt_file(monkeypatch, restore_mode):
    """连续 cycle 依次命中 simple / standard / empty 三个提词文件。"""
    captured = []

    def fake_build(agent_name, export_name, fallback, *args, **kwargs):
        captured.append(export_name)
        return ["x"]

    monkeypatch.setattr(builder, "_build_prompt", fake_build)
    builder.set_mode("empty")
    builder.build_system_prompt()
    builder.cycle_mode()
    builder.build_system_prompt()
    builder.cycle_mode()
    builder.build_system_prompt()
    assert captured == [
        "prompts_export_main_empty",
        "prompts_export_main_simple",
        "prompts_export_main",
    ]


# ── 模式行显示 ─────────────────────────────────────────────

def _mode_line_text(mode, width: int = 80) -> str:
    from src.tui.app.input_area import _build_mode_line
    line = _build_mode_line(width, mode)
    return "".join(run.text for run in line.runs)


def test_mode_line_simple_text():
    assert "简单模式" in _mode_line_text("simple")


def test_mode_line_bool_compat():
    """bool 旧调用兼容：True=空模式，False=标准模式。"""
    assert "空模式" in _mode_line_text(True)
    assert "标准模式" in _mode_line_text(False)


def test_mode_line_str_variants():
    assert "空模式" in _mode_line_text("empty")
    assert "标准模式" in _mode_line_text("standard")


def test_mode_line_width_invariant():
    from src.tui.app.input_area import _build_mode_line
    for width in (40, 80, 120):
        for mode in ("empty", "simple", "standard"):
            assert _build_mode_line(width, mode).width == width


def test_normalize_mode():
    from src.tui.app.input_area import _normalize_mode
    assert _normalize_mode(True) == "empty"
    assert _normalize_mode(False) == "standard"
    assert _normalize_mode("empty") == "empty"
    assert _normalize_mode("simple") == "simple"
    assert _normalize_mode("standard") == "standard"
    assert _normalize_mode("bogus") == "standard"
    assert _normalize_mode(None) == "standard"


def test_input_snap_key_includes_mode(monkeypatch):
    """模式状态进 use_memo deps——三态切换后 InputArea 即时重建。"""
    from src.tui.app import input_area
    props = {"text": "", "width": 80}
    monkeypatch.setattr(builder, "get_mode", lambda: "empty")
    key_empty = input_area._input_snap_key(props, 80, 0.0)
    monkeypatch.setattr(builder, "get_mode", lambda: "simple")
    key_simple = input_area._input_snap_key(props, 80, 0.0)
    monkeypatch.setattr(builder, "get_mode", lambda: "standard")
    key_standard = input_area._input_snap_key(props, 80, 0.0)
    assert key_empty != key_simple
    assert key_simple != key_standard
    assert "empty" in key_empty and "simple" in key_simple and "standard" in key_standard


# ── prompt 插件服务 ────────────────────────────────────────

def test_prompt_service_exposes_mode_api(restore_mode):
    from src.plugins.prompt import PromptService

    service = PromptService.__new__(PromptService)
    builder.set_mode("simple")
    assert service.get_mode() == "simple"
    assert service.is_simple_mode() is True
    assert service.mode_label() == "简单模式"
    assert service.cycle_mode() == "standard"
    assert service.is_standard_mode() is True
    assert service.set_mode("empty") == "empty"
    assert service.is_empty_mode() is True
