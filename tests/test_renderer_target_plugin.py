"""渲染目标插件测试 — 每个内置目标一个清单条目。

覆盖：
- 清单为每个内置渲染目标声明独立条目（id 一一对应）；
- 默认 profile 经独立条目注册全部内置目标；
- overlay 禁用单个目标真正生效（renderer 聚合插件抑制默认装配）；
- 条目 config 的 target 引用可替换实现；
- 模块级 ``resolve_render_target`` 与具体目标实现（终端/文件）。
"""

from __future__ import annotations

import pytest

from src.kernel import Kernel, materialize
from src.plugins.bootstrap import (
    build_kernel,
    inject_managed_config,
    resolve_entries,
    shutdown_kernel,
)

import src.renderer.targets.registry as rtr
from src.renderer.targets import FileRenderTarget, TerminalRenderTarget


class _FakeTarget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture(autouse=True)
def _clean_registry():
    rtr.reset()
    yield
    rtr.reset()


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _resolved(profile="cli"):
    return materialize(resolve_entries(profile, discover_external=False))


def test_manifest_declares_each_target():
    targets = []
    for entry, plug in _resolved():
        if getattr(plug, "name", "") == "renderer_target":
            targets.append((entry.id, (entry.config or {}).get("id")))
    assert [spec_id for _, spec_id in targets] == rtr.builtin_render_target_ids()
    assert all(entry_id.startswith("renderer_ext::renderer_target_") for entry_id, _ in targets)


async def test_default_profile_targets(cli_kernel):
    service = cli_kernel.resolve_service("renderer")
    assert service.target_ids() == ["file", "terminal"]
    assert service.builtin_target_ids() == rtr.builtin_render_target_ids()


def test_create_terminal_target():
    target = rtr.resolve_render_target("terminal", width=40)
    assert isinstance(target, TerminalRenderTarget)
    assert target.width == 40


def test_create_file_target(tmp_path):
    path = tmp_path / "out.txt"
    target = rtr.resolve_render_target("file", path=str(path))
    assert isinstance(target, FileRenderTarget)
    target.write_line("hello")
    target.flush()
    target.close()
    assert path.read_text(encoding="utf-8").strip() == "hello"


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


async def test_overlay_disable_target():
    kernel = await _build_with_disable(["renderer_ext::renderer_target_file"])
    try:
        assert "file" not in rtr.builtin_render_target_factories()
        assert "terminal" in rtr.builtin_render_target_factories()
    finally:
        await kernel.dispose()


async def test_entry_replaces_target():
    from src.plugins.config import apply as config_apply
    from src.plugins.renderer import apply as renderer_apply
    from src.plugins.renderer_targets import apply_renderer_target

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    kernel.mount(
        apply_renderer_target,
        config={"id": "file", "target": "tests.test_renderer_target_plugin._FakeTarget"},
    )
    await kernel.settle()
    try:
        target = rtr.resolve_render_target("file", x=1)
        assert isinstance(target, _FakeTarget)
        assert target.kwargs == {"x": 1}
    finally:
        await kernel.dispose()
    assert isinstance(rtr.resolve_render_target("file", path="/tmp/x"), FileRenderTarget)


async def test_entry_without_id_fails():
    from src.plugins.config import apply as config_apply
    from src.plugins.renderer import apply as renderer_apply
    from src.plugins.renderer_targets import apply_renderer_target

    kernel = Kernel(name="t")
    kernel.mount(config_apply)
    kernel.mount(renderer_apply)
    await kernel.settle()
    fiber = kernel.mount(apply_renderer_target, config={})
    await kernel.settle()
    try:
        assert fiber.state.value == "FAILED"
        assert isinstance(fiber.error, ValueError)
    finally:
        await kernel.dispose()


def test_register_extension_target():
    undo = rtr.register_render_target("mine", _FakeTarget)
    try:
        assert "mine" in rtr.render_target_factories()
        target = rtr.resolve_render_target("mine", a=2)
        assert isinstance(target, _FakeTarget)
        assert target.kwargs == {"a": 2}
    finally:
        undo()
    assert rtr.render_target_factories() == {}


def test_disable_and_managed_roundtrip():
    undo = rtr.disable_builtin_render_targets(["file"])
    try:
        assert "file" not in rtr.builtin_render_target_factories()
    finally:
        undo()
    assert "file" in rtr.builtin_render_target_factories()
    with pytest.raises(KeyError):
        rtr.disable_builtin_render_targets(["nope"])
