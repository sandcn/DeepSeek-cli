"""Cordis 自省/自修改工具测试 — 运行时热挂载自写插件。"""

from __future__ import annotations

import os

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


@pytest.fixture
def runtime_dir(tmp_path, monkeypatch):
    import src.tools.cordis as cordis

    directory = tmp_path / "runtime_plugins"
    monkeypatch.setattr(cordis, "RUNTIME_PLUGINS_DIR", directory)
    monkeypatch.setattr(cordis, "ensure_runtime_plugins_dir", lambda: directory.mkdir(parents=True, exist_ok=True))
    return directory


async def test_inspect_lists_services(cli_kernel):
    from src.tools.cordis import CordisInspectFunc

    out = await CordisInspectFunc().execute()
    assert "services:" in out
    assert "tools" in out
    assert "fibers:" in out


async def test_inspect_filter_by_name(cli_kernel):
    from src.tools.cordis import CordisInspectFunc

    out = await CordisInspectFunc(what="services", name="tools").execute()
    assert "tools" in out
    assert "agent_loop" not in out


async def test_define_run_stop_undefine(cli_kernel, runtime_dir):
    from src.tools.cordis import (
        CordisDefineFunc,
        CordisRunFunc,
        CordisStopFunc,
        CordisUndefineFunc,
    )

    code = (
        "from src.kernel import plugin\n"
        "@plugin('runtime_demo')\n"
        "def apply(ctx):\n"
        "    ctx.provide('runtime_demo_service', 'alive')\n"
    )
    defined = await CordisDefineFunc("runtime_demo", code).execute()
    assert "已定义插件" in defined
    assert (runtime_dir / "runtime_demo.py").is_file()

    ran = await CordisRunFunc("runtime_demo").execute()
    assert "已挂载插件" in ran
    assert cli_kernel.resolve_service("runtime_demo_service") == "alive"

    stopped = await CordisStopFunc("runtime_demo").execute()
    assert "已停止插件" in stopped
    assert not cli_kernel.has_service("runtime_demo_service")

    # 重新挂载后 undefine 应停止并删除文件
    await CordisRunFunc("runtime_demo").execute()
    removed = await CordisUndefineFunc("runtime_demo").execute()
    assert "已删除插件文件" in removed
    assert not (runtime_dir / "runtime_demo.py").exists()
    assert not cli_kernel.has_service("runtime_demo_service")


async def test_define_rejects_illegal_name(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc

    out = await CordisDefineFunc("../evil", "x=1").execute()
    assert out.startswith("(")


async def test_run_missing_file(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisRunFunc

    runtime_dir.mkdir(parents=True, exist_ok=True)
    out = await CordisRunFunc("ghost").execute()
    assert "不存在" in out


from src.core.cordis_tools import CORDIS_TOOLS as _CORDIS_TOOLS


async def test_cordis_tools_not_registered_for_any_agent(cli_kernel):
    """任何 agent 都不能加载 cordis 工具：注册表 / schema / 发现结果均不含。"""
    tools = cli_kernel.resolve_service("tools")
    names = set(tools.names())
    schemas = {s["function"]["name"] for s in tools.schemas()}
    from src.tools.registry import discover_builtin_tools

    discovered = set(discover_builtin_tools())
    for name in _CORDIS_TOOLS:
        assert name not in names
        assert name not in schemas
        assert name not in discovered


def test_global_disabled_tools_cover_cordis():
    from src.tools.tool_policy import GLOBAL_DISABLED_TOOLS, is_globally_disabled

    assert set(_CORDIS_TOOLS) <= set(GLOBAL_DISABLED_TOOLS)
    for name in _CORDIS_TOOLS:
        assert is_globally_disabled(name) is True
    assert is_globally_disabled("read_file") is False


async def test_main_agent_schema_excludes_cordis(cli_kernel):
    """主 Agent 的工具 schema 不含 cordis 工具。"""
    agent = cli_kernel.root.agent_loop.make_event_agent()
    names = {s["function"]["name"] for s in agent.tools}
    assert not any(n.startswith("cordis") for n in names)


async def test_subagent_types_exclude_cordis(cli_kernel):
    """各 SubAgent 类型（map/review/plan/execute）的工具集均不含 cordis。"""
    from src.core.subagent import get_excluded_tools

    tools = cli_kernel.resolve_service("tools")
    schemas = tools.schemas()
    for agent_type in ("map", "review", "plan", "execute"):
        excluded = get_excluded_tools(agent_type)
        names = {
            s["function"]["name"]
            for s in schemas
            if s["function"]["name"] not in excluded
        }
        assert not any(n.startswith("cordis") for n in names)


# ── 加固回归（命名空间 / 幂等 / 异常 / 强制禁用 / 单一来源） ──


def _runtime_code(plugin_name: str, service: str = "runtime_demo_service") -> str:
    return (
        "from src.kernel import plugin\n"
        f"@plugin('{plugin_name}')\n"
        "def apply(ctx):\n"
        f"    ctx.provide('{service}', 'alive')\n"
    )


async def test_inspect_rejects_invalid_what(cli_kernel):
    from src.tools.cordis import CordisInspectFunc

    out = await CordisInspectFunc(what="bogus").execute()
    assert out.startswith("(")


async def test_define_rejects_illegal_names(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc

    for bad in ("a\nb", "CON", "__init__", "a/b", "x" * 200, "a:b"):
        out = await CordisDefineFunc(bad, "x = 1").execute()
        assert out.startswith("("), f"{bad!r} 应被拒绝"


async def test_define_write_failure_returns_message(cli_kernel, runtime_dir, monkeypatch):
    from src.tools.cordis import CordisDefineFunc

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", _boom)
    out = await CordisDefineFunc("wfail", "x=1").execute()
    assert out.startswith("(")
    assert "写入失败" in out


async def test_define_rejects_syntax_error(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc

    out = await CordisDefineFunc("badsyntax", "def broken(\n").execute()
    assert out.startswith("(")
    assert "语法错误" in out
    assert not (runtime_dir / "badsyntax.py").exists()


async def test_run_reports_compiled_error(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisRunFunc

    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "brokenplug.py").write_text("def broken(\n", encoding="utf-8")
    out = await CordisRunFunc("brokenplug").execute()
    assert out.startswith("(")


async def test_run_pending_plugin_reports_not_active(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc, CordisRunFunc

    code = (
        "from src.kernel import plugin\n"
        "@plugin('needs_missing_dep', inject=['no_such_service_xyz'])\n"
        "def apply(ctx):\n"
        "    ctx.provide('never_ready', 1)\n"
    )
    await CordisDefineFunc("pendingplug", code).execute()
    out = await CordisRunFunc("pendingplug").execute()
    assert out.startswith("(")
    assert "未激活" in out


async def test_run_mounts_all_plugins_in_file(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc, CordisRunFunc

    code = (
        "from src.kernel import plugin\n"
        "@plugin('multi_a')\n"
        "def apply_a(ctx):\n"
        "    ctx.provide('multi_a_service', 1)\n"
        "@plugin('multi_b')\n"
        "def apply_b(ctx):\n"
        "    ctx.provide('multi_b_service', 2)\n"
    )
    await CordisDefineFunc("multi", code).execute()
    out = await CordisRunFunc("multi").execute()
    assert "multi_a" in out and "multi_b" in out
    assert cli_kernel.resolve_service("multi_a_service") == 1
    assert cli_kernel.resolve_service("multi_b_service") == 2


async def test_run_is_idempotent_replaces_duplicate(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc, CordisRunFunc

    await CordisDefineFunc("dup", _runtime_code("dup_plugin", "dup_service")).execute()
    await CordisRunFunc("dup").execute()
    assert cli_kernel.resolve_service("dup_service") == "alive"
    second = await CordisRunFunc("dup").execute()
    assert "已替换旧实例" in second
    active = [
        f for f in cli_kernel.fibers()
        if f.definition.name == "dup_plugin" and f.active
    ]
    assert len(active) == 1


async def test_stop_and_undefine_resolve_by_file_name(cli_kernel, runtime_dir):
    from src.tools.cordis import (
        CordisDefineFunc,
        CordisRunFunc,
        CordisStopFunc,
        CordisUndefineFunc,
    )

    await CordisDefineFunc("plugfile", _runtime_code("inner_name", "inner_service")).execute()
    assert "已挂载插件" in await CordisRunFunc("plugfile").execute()
    assert cli_kernel.resolve_service("inner_service") == "alive"

    stopped = await CordisStopFunc("plugfile").execute()
    assert "已停止插件" in stopped and "inner_name" in stopped
    assert not cli_kernel.has_service("inner_service")

    await CordisRunFunc("plugfile").execute()
    removed = await CordisUndefineFunc("plugfile").execute()
    assert "已删除插件文件" in removed
    assert not (runtime_dir / "plugfile.py").exists()


async def test_stop_accepts_py_suffix(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisDefineFunc, CordisRunFunc, CordisStopFunc

    await CordisDefineFunc(
        "suffixplug", _runtime_code("suffix_plugin", "suffix_service")
    ).execute()
    await CordisRunFunc("suffixplug.py").execute()
    out = await CordisStopFunc("suffixplug.py").execute()
    assert "已停止插件" in out


async def test_stop_missing_plugin_returns_message(cli_kernel, runtime_dir):
    from src.tools.cordis import CordisStopFunc

    out = await CordisStopFunc("ghost_plugin").execute()
    assert out.startswith("(")
    assert "未找到插件" in out


async def test_undefine_remove_failure_returns_message(cli_kernel, runtime_dir, monkeypatch):
    from src.tools.cordis import CordisDefineFunc, CordisUndefineFunc

    await CordisDefineFunc("rmfail", "x = 1").execute()
    assert (runtime_dir / "rmfail.py").is_file()

    def _boom(*_a, **_k):
        raise OSError("locked")

    monkeypatch.setattr(os, "remove", _boom)
    out = await CordisUndefineFunc("rmfail").execute()
    assert out.startswith("(")
    assert "删除失败" in out


def test_registry_rejects_globally_disabled_tool():
    from src.tools.cordis import CordisRunFunc
    from src.tools.registry import ToolRegistry

    registry = ToolRegistry()
    with pytest.raises(ValueError):
        registry.register(CordisRunFunc)


def test_cordis_names_share_single_source():
    from src.core.agent_types import excluded_tools
    from src.core.cordis_tools import CORDIS_TOOLS
    from src.tools.tool_policy import GLOBAL_DISABLED_TOOLS, builtin_global_disabled_tool_ids

    assert set(GLOBAL_DISABLED_TOOLS) == set(CORDIS_TOOLS)
    assert set(builtin_global_disabled_tool_ids()) == set(CORDIS_TOOLS)
    for agent_type in ("map", "review", "plan"):
        assert set(CORDIS_TOOLS) <= excluded_tools(agent_type)


async def test_run_does_not_touch_same_stem_external_plugin(cli_kernel, tmp_path, runtime_dir):
    """同名但位于其它目录（外部插件）的 Fiber 不被 run 的幂等替换误卸载。"""
    from src.tools.cordis import CordisDefineFunc, CordisRunFunc

    external = tmp_path / "demo.py"
    external.write_text(
        "from src.kernel import plugin\n"
        "@plugin('external_demo')\n"
        "def apply(ctx):\n"
        "    ctx.provide('external_demo_service', 'ext')\n",
        encoding="utf-8",
    )
    external_fiber = cli_kernel.mount_file(str(external))
    await cli_kernel.settle()
    assert cli_kernel.resolve_service("external_demo_service") == "ext"

    await CordisDefineFunc(
        "demo", _runtime_code("runtime_demo_plugin", "runtime_demo_service")
    ).execute()
    out = await CordisRunFunc("demo").execute()
    assert "已挂载插件" in out
    assert cli_kernel.resolve_service("runtime_demo_service") == "alive"
    assert cli_kernel.resolve_service("external_demo_service") == "ext"
    assert not external_fiber.disposed


def test_cordis_declarations_removed():
    """被全局禁用的 cordis 工具不再有元数据/显示名声明（消除死数据漂移）。"""
    from src.core.cordis_tools import CORDIS_TOOLS
    from src.presentation_data import tool_display_name_map
    from src.tools.metadata_registry import builtin_tool_names

    metadata_names = set(builtin_tool_names())
    display = tool_display_name_map()
    for name in CORDIS_TOOLS:
        assert name not in metadata_names
        assert name not in display
