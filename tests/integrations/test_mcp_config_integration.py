"""MCP 与配置系统 / 提示词构建的集成测试。"""

from __future__ import annotations

from src.config.defaults import CONFIG_KEYS, DEFAULTS
from src.config.schema import _validate_rc
from src.config.view_model import CONFIG_ENTRY_DESCS, build_config_entries
from src.prompt_builder import builder as prompt_builder


# ── 配置元数据 ─────────────────────────────────────────

def test_defaults_contains_mcp_servers():
    assert DEFAULTS["mcp_servers"] == []


def test_config_keys_contains_mcp_servers():
    entry = CONFIG_KEYS["MCP_SERVERS"]
    assert entry["rc_path"] == ("mcp_servers",)
    assert entry["type"] is list
    assert entry["default"] == []


def test_view_model_has_description_and_entry():
    assert "MCP_SERVERS" in CONFIG_ENTRY_DESCS
    entries = build_config_entries({})
    by_key = {e["key"]: e for e in entries}
    assert "MCP_SERVERS" in by_key
    assert by_key["MCP_SERVERS"]["edit_kind"] == "json"
    assert by_key["MCP_SERVERS"]["type"] is list


def test_validate_rc_rejects_non_list():
    rc = _validate_rc({"mcp_servers": "nope"})
    assert rc["mcp_servers"] == []


def test_validate_rc_cleans_entries():
    rc = _validate_rc({"mcp_servers": [
        {"name": "a", "transport": "stdio", "command": "x"},
        {"command": "no-name"},
        "bad",
    ]})
    assert [c["name"] for c in rc["mcp_servers"]] == ["a"]


def test_validate_rc_empty_list_stays_empty():
    assert _validate_rc({"mcp_servers": []})["mcp_servers"] == []


# ── 提示词章节 ─────────────────────────────────────────

def test_build_mcp_section_empty_without_config(monkeypatch):
    import src.config as config_module
    monkeypatch.setattr(config_module, "MCP_SERVERS", [], raising=False)
    assert prompt_builder._build_mcp_section() == ""
    assert prompt_builder._build_mcp_section("review") == ""


def test_build_mcp_section_delegates_when_configured(monkeypatch):
    import src.config as config_module
    import src.mcp as mcp_module
    monkeypatch.setattr(config_module, "MCP_SERVERS", [{"name": "a", "command": "x"}], raising=False)
    seen = []

    def _fake(agent_type=None):
        seen.append(agent_type)
        return "## MCP 外部工具\n- `a`"

    monkeypatch.setattr(mcp_module, "get_mcp_prompt_section", _fake, raising=False)
    assert prompt_builder._build_mcp_section().startswith("## MCP 外部工具")
    prompt_builder._build_mcp_section("review")
    prompt_builder._build_mcp_section("main")
    assert seen == ["execute", "review", "execute"]


def test_build_mcp_section_swallows_errors(monkeypatch):
    import src.config as config_module
    import src.mcp as mcp_module

    monkeypatch.setattr(config_module, "MCP_SERVERS", [{"name": "a", "command": "x"}], raising=False)

    def _boom(agent_type=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(mcp_module, "get_mcp_prompt_section", _boom, raising=False)
    assert prompt_builder._build_mcp_section() == ""


def test_build_prompt_injects_mcp_section(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_build_mcp_section", lambda agent_name="main": "## MCP 外部工具\n- `a`")
    parts = prompt_builder._build_prompt(
        "main", "", "fallback", include_version_control=False,
        include_global_md=False, include_skills=False,
    )
    assert any("MCP 外部工具" in p for p in parts)


def test_build_prompt_can_skip_mcp_section(monkeypatch):
    monkeypatch.setattr(prompt_builder, "_build_mcp_section", lambda agent_name="main": "## MCP 外部工具")
    parts = prompt_builder._build_prompt(
        "main", "", "fallback", include_version_control=False,
        include_global_md=False, include_skills=False, include_mcp=False,
    )
    assert not any("MCP 外部工具" in p for p in parts)
