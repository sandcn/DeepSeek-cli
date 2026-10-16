"""MCP 与工具注册表 / Agent 权限 / 应用启动链路的集成测试。"""

from __future__ import annotations

import inspect

from src.core.subagent import _TOOL_EXCLUSION_MAP, _get_excluded_tools
from src.mcp.client import McpToolDef
from src.mcp.manager import register_tool_policy, unregister_tool_policy
from src.mcp.tool import build_mcp_tool_class
from src.tools.base import Func
from src.tools.registry import ToolRegistry


def _registry():
    return ToolRegistry(initial_tools={})


def _tool_cls(server="srv", tool="echo"):
    return build_mcp_tool_class(
        server,
        McpToolDef(name=tool, description="d",
                   input_schema={"type": "object", "properties": {"text": {"type": "string"}}}),
    )


def test_registry_register_and_schema():
    registry = _registry()
    cls = _tool_cls()
    registry.register(cls)
    schemas = registry.get_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert "mcp__srv__echo" in names


def test_registry_dispatch_builds_instance():
    registry = _registry()
    registry.register(_tool_cls())
    instance = registry.dispatch("mcp__srv__echo", {"text": "hi"})
    assert isinstance(instance, Func)
    assert instance.arguments == {"text": "hi"}


def test_registry_unregister():
    registry = _registry()
    registry.register(_tool_cls())
    assert registry.unregister("mcp__srv__echo") is True
    assert registry.unregister("mcp__srv__echo") is False
    assert registry.get_tools() == {}


def test_unregister_invalidates_schema_cache():
    registry = _registry()
    registry.register(_tool_cls())
    assert "mcp__srv__echo" in [s["function"]["name"] for s in registry.get_schemas()]
    registry.unregister("mcp__srv__echo")
    assert [s["function"]["name"] for s in registry.get_schemas()] == []


def test_default_policy_blocks_readonly_agents():
    name = "mcp__srv__echo"
    register_tool_policy([name])
    try:
        assert name in _get_excluded_tools("map")
        assert name in _get_excluded_tools("review")
        assert name in _get_excluded_tools("plan")
        assert name not in _get_excluded_tools("execute")

        allowed, reason = Func.can_use(name, "review")
        assert allowed is False and reason
        allowed2, reason2 = Func.can_use(name, "execute")
        assert allowed2 is True and reason2 is None
    finally:
        unregister_tool_policy([name])


def test_policy_can_grant_review_access():
    name = "mcp__srv__readonly_echo"
    register_tool_policy([name], {"execute", "review"})
    try:
        assert Func.can_use(name, "review")[0] is True
        assert Func.can_use(name, "map")[0] is False
    finally:
        unregister_tool_policy([name])


def test_app_startup_wires_mcp_lifecycle():
    from src.app_init import main as main_module
    from src.plugins import app as app_module
    from src.plugins import mcp as mcp_module

    # main 只保留组合根（经 ctx.app 运行）；MCP 启动接线在 app 插件，
    # 关闭经 ctx.mcp 服务（McpService 卸载副作用）。
    assert 'resolve_service("app")' in inspect.getsource(main_module)
    assert "setup_mcp" in inspect.getsource(app_module)
    assert "shutdown_mcp" in inspect.getsource(mcp_module)


def test_tool_display_name_unknown_mcp_tool_falls_back():
    from src.tools.registry import get_tool_display_name
    assert get_tool_display_name("mcp__srv__echo") == "mcp__srv__echo"


def test_exclusion_map_is_set_of_str():
    for agent_type in ("map", "review", "plan", "execute"):
        assert isinstance(_TOOL_EXCLUSION_MAP[agent_type], set)


def test_list_changed_notification_is_logged(caplog):
    import logging

    from src.mcp.transport import _log_notification

    with caplog.at_level(logging.INFO, logger="src.mcp.transport"):
        _log_notification("srv", {"method": "notifications/tools/list_changed"})
    assert "工具列表已变更" in caplog.text


def test_other_notification_is_debug_only(caplog):
    import logging

    from src.mcp.transport import _log_notification

    with caplog.at_level(logging.DEBUG, logger="src.mcp.transport"):
        _log_notification("srv", {"method": "notifications/resources/updated"})
    assert "工具列表已变更" not in caplog.text
