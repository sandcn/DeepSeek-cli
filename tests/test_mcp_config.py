"""MCP server 配置解析与校验单元测试。"""

from __future__ import annotations

from src.mcp.config import (
    DEFAULT_AGENT_TYPES,
    DEFAULT_TIMEOUT,
    McpServerConfig,
    load_mcp_servers,
    parse_mcp_servers,
    parse_server,
    validate_mcp_servers,
)


def test_parse_stdio_server():
    cfg = parse_server({
        "name": "fs",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "server"],
        "env": {"A": "1"},
        "timeout": 12,
    })
    assert isinstance(cfg, McpServerConfig)
    assert cfg.transport == "stdio"
    assert cfg.command == "npx"
    assert cfg.args == ["-y", "server"]
    assert cfg.env == {"A": "1"}
    assert cfg.timeout == 12.0
    assert cfg.is_stdio


def test_parse_http_server():
    cfg = parse_server({"name": "r", "transport": "http", "url": "https://x/mcp"})
    assert cfg is not None
    assert cfg.transport == "http"
    assert cfg.url == "https://x/mcp"
    assert not cfg.is_stdio


def test_transport_defaults_to_stdio():
    cfg = parse_server({"name": "s", "command": "python"})
    assert cfg.transport == "stdio"


def test_reject_missing_name():
    assert parse_server({"command": "x"}) is None


def test_reject_non_dict():
    assert parse_server("oops") is None
    assert parse_server(None) is None
    assert parse_server([1, 2]) is None


def test_reject_stdio_without_command():
    assert parse_server({"name": "s"}) is None


def test_reject_http_without_url():
    assert parse_server({"name": "s", "transport": "http"}) is None


def test_reject_http_with_bad_scheme():
    assert parse_server({"name": "s", "transport": "http", "url": "ftp://x"}) is None


def test_reject_unknown_transport():
    assert parse_server({"name": "s", "transport": "carrier-pigeon", "command": "x"}) is None


def test_timeout_clamped_and_defaulted():
    assert parse_server({"name": "s", "command": "x", "timeout": 0}).timeout == DEFAULT_TIMEOUT
    assert parse_server({"name": "s", "command": "x", "timeout": "bad"}).timeout == DEFAULT_TIMEOUT
    assert parse_server({"name": "s", "command": "x", "timeout": -3}).timeout == DEFAULT_TIMEOUT
    assert parse_server({"name": "s", "command": "x", "timeout": 10 ** 9}).timeout <= 600.0


def test_agents_filtered_and_defaulted():
    cfg = parse_server({"name": "s", "command": "x", "agents": ["execute", "bogus"]})
    assert cfg.agents == ["execute"]
    assert cfg.allowed_agents == {"execute"}

    cfg2 = parse_server({"name": "s", "command": "x", "agents": ["nope"]})
    assert set(cfg2.agents) == set(DEFAULT_AGENT_TYPES)


def test_allowed_agents_property():
    cfg = McpServerConfig(name="s", agents=["map", "review", "execute"])
    assert cfg.allowed_agents == {"map", "review", "execute"}
    # 空/非法 → 默认
    cfg2 = McpServerConfig(name="s", agents=[])
    assert cfg2.allowed_agents == set(DEFAULT_AGENT_TYPES)


def test_enabled_defaults_true_and_parses_strings():
    assert parse_server({"name": "s", "command": "x"}).enabled is True
    assert parse_server({"name": "s", "command": "x", "enabled": "false"}).enabled is False
    assert parse_server({"name": "s", "command": "x", "enabled": 0}).enabled is False


def test_parse_mcp_servers_dedupes_by_name():
    servers = parse_mcp_servers([
        {"name": "dup", "command": "a"},
        {"name": "dup", "command": "b"},
        {"name": "ok", "command": "c"},
    ])
    assert [s.name for s in servers] == ["dup", "ok"]
    assert servers[0].command == "a"


def test_parse_mcp_servers_skips_invalid_entries():
    servers = parse_mcp_servers([None, {"name": "ok", "command": "c"}, "x", {}])
    assert [s.name for s in servers] == ["ok"]


def test_parse_mcp_servers_non_list_returns_empty():
    assert parse_mcp_servers(None) == []
    assert parse_mcp_servers({}) == []
    assert parse_mcp_servers("nope") == []


def test_validate_mcp_servers_cleans_entries():
    cleaned = validate_mcp_servers([
        {"name": "a", "command": "x"},
        {"command": "no-name"},
        "bad",
        {"name": "b", "transport": "http", "url": "https://x"},
    ])
    assert [c["name"] for c in cleaned] == ["a", "b"]


def test_validate_mcp_servers_non_list():
    assert validate_mcp_servers("x") == []
    assert validate_mcp_servers(None) == []


def test_load_mcp_servers_reads_config(monkeypatch):
    import src.config as config_module
    monkeypatch.setattr(config_module, "MCP_SERVERS", [{"name": "cfg", "command": "x"}], raising=False)
    servers = load_mcp_servers()
    assert [s.name for s in servers] == ["cfg"]


def test_load_mcp_servers_non_list_returns_empty(monkeypatch):
    import src.config as config_module
    monkeypatch.setattr(config_module, "MCP_SERVERS", {"not": "a list"}, raising=False)
    assert load_mcp_servers() == []


# ── 边界类型转换 ───────────────────────────────────────

def test_enabled_float_values():
    assert parse_server({"name": "s", "command": "x", "enabled": 0.0}).enabled is False
    assert parse_server({"name": "s", "command": "x", "enabled": 1.5}).enabled is True


def test_timeout_bool_is_rejected():
    assert parse_server({"name": "s", "command": "x", "timeout": True}).timeout == DEFAULT_TIMEOUT
    assert parse_server({"name": "s", "command": "x", "timeout": False}).timeout == DEFAULT_TIMEOUT


def test_timeout_nan_falls_back():
    assert parse_server({"name": "s", "command": "x", "timeout": float("nan")}).timeout == DEFAULT_TIMEOUT


def test_inherit_env_defaults_false_and_parses():
    assert parse_server({"name": "s", "command": "x"}).inherit_env is False
    assert parse_server({"name": "s", "command": "x", "inherit_env": True}).inherit_env is True
    assert parse_server({"name": "s", "command": "x", "inherit_env": "yes"}).inherit_env is True


def test_parallel_safe_parses():
    assert parse_server({"name": "s", "command": "x"}).parallel_safe is False
    assert parse_server({"name": "s", "command": "x", "parallel_safe": True}).parallel_safe is True


def test_cwd_and_description_parse():
    cfg = parse_server({"name": "s", "command": "x", "cwd": "/tmp", "description": "d"})
    assert cfg.cwd == "/tmp"
    assert cfg.description == "d"
