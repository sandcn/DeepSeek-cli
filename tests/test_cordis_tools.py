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


def test_cordis_tools_registered(cli_kernel):
    tools = cli_kernel.resolve_service("tools")
    for name in ("cordis_inspect", "cordis_define", "cordis_run", "cordis_stop", "cordis_undefine"):
        assert name in tools.names()
