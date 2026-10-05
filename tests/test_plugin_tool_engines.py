"""工具执行引擎插件化测试 — dag/serial/parallel 独立插件条目。

覆盖：
- 清单为每个内置引擎声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部引擎，``ctx.tool_scheduler`` 可自省；
- overlay 禁用单个引擎真正生效（``tool_scheduler`` 抑制默认装配）；
- ``ToolScheduler`` 按 ``engine`` 分派到注册引擎（含自定义引擎/覆盖）；
- serial / parallel 引擎的执行语义；
- 直接 API：register/set_managed/disable/register_tool_engine 与 reset。
"""

from __future__ import annotations

import pytest

import src.core.tool_engines as treg
from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    treg.reset()
    yield
    treg.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_engine():
    specs = [e.config.get("id") for e, p in _resolved() if getattr(p, "name", "") == "tool_engine"]
    assert specs == treg.builtin_tool_engine_ids()
    ids = [e.id for e, p in _resolved() if getattr(p, "name", "") == "tool_engine"]
    assert all(i.startswith("tool_engines::tool_engine_") for i in ids)


def test_tree_declares_engine_bundle():
    from src.plugins.manifest import build_config_tree

    tree = build_config_tree()
    assert "tool_engines" in tree.bundles()
    assert "tool_engines" in tree.bundle("runtime").includes


async def test_default_profile_registers_all(cli_kernel):
    service = cli_kernel.resolve_service("tool_scheduler")
    assert service.engine() == "dag"
    assert set(service.engines()) == {"dag", "serial", "parallel"}


async def _build_with_disable(ids):
    from src.kernel.overlay import apply_overlay

    entries = apply_overlay(resolve_entries("cli", discover_external=False), {"disable": list(ids)})
    resolved = materialize(entries)
    inject_managed_config(resolved)
    kernel = Kernel(name="t", profile="cli")
    for entry, plug in resolved:
        if entry.disabled:
            continue
        kernel.mount(plug, config=entry.config)
    await kernel.settle()
    return kernel


async def test_overlay_disable_single_engine():
    kernel = await _build_with_disable(["tool_engines::tool_engine_serial"])
    try:
        assert "serial" not in kernel.resolve_service("tool_scheduler").engines()
        assert "parallel" in kernel.resolve_service("tool_scheduler").engines()
    finally:
        await kernel.dispose()
    assert "serial" in treg.builtin_tool_engine_factories()


async def test_engine_config_selects_engine():
    from src.plugins.config import apply as config_apply
    from src.plugins.tools import apply as tools_apply
    from src.plugins.tool_scheduler import apply as scheduler_apply

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(tools_apply)
    await kernel.settle()
    kernel.mount(scheduler_apply, config={"engine": "serial"})
    await kernel.settle()
    try:
        service = kernel.resolve_service("tool_scheduler")
        assert service.engine() == "serial"
        assert service.set_engine("parallel") == "serial"
        assert service.engine() == "parallel"
    finally:
        await kernel.dispose()


async def test_schedule_dispatches_to_custom_engine():
    from src.core.tool_executor_async import ToolScheduler

    seen = []

    async def _spy(scheduler, tool_calls, *, agent_ref, on_before, on_after,
                   run_method, is_outermost):
        seen.append([tc["id"] for tc in tool_calls])
        return [(tc["id"], "ok", True) for tc in tool_calls]

    undo = treg.register_tool_engine("spy", lambda: _spy)
    try:
        scheduler = ToolScheduler(registry=_NullRegistry(), engine="spy")
        result = await scheduler.schedule(
            [{"id": "a", "name": "x", "arguments": {}}], agent_ref=object(),
        )
        assert seen == [["a"]]
        assert result == [("a", "ok", True)]
    finally:
        undo()


async def test_schedule_falls_back_to_dag_when_engine_missing():
    from src.core.tool_executor_async import ToolScheduler

    scheduler = ToolScheduler(registry=_NullRegistry(), engine="does-not-exist")
    # 缺省回退默认引擎（dag）→ 内联 DAG 路径；空列表安全返回
    assert await scheduler.schedule([], agent_ref=object()) == []


async def test_serial_engine_runs_in_order():
    from src.core.tool_executor_async import ToolScheduler

    order = []

    class _Sched:
        async def _execute_one_async(self, tc, *, agent_ref, on_before, on_after, run_method):
            order.append(tc["id"])
            return (tc["id"], "ok", True)

    calls = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}, {"id": "3", "name": "c"}]
    results = await treg._serial_engine(
        _Sched(), calls, agent_ref=object(), on_before=None, on_after=None,
        run_method=None, is_outermost=True,
    )
    assert order == ["1", "2", "3"]
    assert [r[0] for r in results] == ["1", "2", "3"]


async def test_parallel_engine_delegates():
    class _Sched:
        def __init__(self):
            self.calls = None

        async def _execute_concurrent(self, tool_calls, **kwargs):
            self.calls = [tc["id"] for tc in tool_calls]
            return [(tc["id"], "ok", True) for tc in tool_calls]

    sched = _Sched()
    results = await treg._parallel_engine(
        sched, [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}],
        agent_ref=object(), on_before=None, on_after=None, run_method=None, is_outermost=True,
    )
    assert sched.calls == ["1", "2"]
    assert len(results) == 2


def test_registry_direct_api():
    assert len(treg.builtin_tool_engine_factories()) == 3
    undo = treg.disable_builtin_tool_engines(["parallel"])
    try:
        assert "parallel" not in treg.builtin_tool_engine_factories()
        assert treg.resolve_tool_engine("parallel") is None
    finally:
        undo()
    assert "parallel" in treg.builtin_tool_engine_factories()

    undo_manage = treg.set_managed_builtin_tool_engines(["serial"])
    try:
        assert "serial" not in treg.builtin_tool_engine_factories()
    finally:
        undo_manage()
    assert "serial" in treg.builtin_tool_engine_factories()

    with pytest.raises(KeyError):
        treg.disable_builtin_tool_engines(["nope"])


def test_override_dag_engine_used():
    from src.core.tool_executor_async import ToolScheduler

    async def _custom_dag(scheduler, tool_calls, *, agent_ref, on_before, on_after,
                          run_method, is_outermost):
        return [("custom", "ok", True)]

    undo = treg.register_builtin_tool_engine("dag", lambda: _custom_dag)
    try:
        scheduler = ToolScheduler(registry=_NullRegistry(), engine="dag")
        assert scheduler._resolve_engine() is _custom_dag
    finally:
        undo()
    assert treg.resolve_tool_engine("dag") is treg._dag_engine


class _NullRegistry:
    def get_schemas(self):
        return []

    def dispatch(self, name, arguments, agent=None):
        raise AssertionError("不应真正调度")
