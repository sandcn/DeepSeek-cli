"""应用运行时插件测试 — ctx.interactive_loop / ctx.application。

覆盖：
- 内核提供 ``ctx.interactive_loop`` 与 ``ctx.application``（cli），minimal 无；
- ``ApplicationService`` 按 prompt 选择交互/单次模式并可运行；
- ``InteractiveLoopService`` 运行主循环/单次模式/构造 loop；
- ``src.application`` 的两种模式经内核服务运行（无内核时回退 app_loop）；
- ``plugins/app`` 经 ``ctx.application`` 运行应用（无服务时回退）。
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from src.kernel import get_current_kernel
from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _args(**kwargs) -> Namespace:
    base = {"prompt": None, "load": None, "command": None, "version": False}
    base.update(kwargs)
    return Namespace(**base)


async def test_services_present_in_cli(cli_kernel):
    from src.plugins.application import ApplicationService, InteractiveLoopService

    assert isinstance(cli_kernel.resolve_service("interactive_loop"), InteractiveLoopService)
    assert isinstance(cli_kernel.resolve_service("application"), ApplicationService)


async def test_minimal_profile_has_no_application():
    kernel = await build_kernel("minimal")
    try:
        assert not kernel.has_service("application")
        assert not kernel.has_service("interactive_loop")
    finally:
        await shutdown_kernel(kernel)


async def test_application_build_selects_mode(cli_kernel):
    from src.application import InteractiveMode, SingleMode

    service = cli_kernel.resolve_service("application")
    app = service.build(loaded_data=None, prompt="")
    assert isinstance(app.mode, InteractiveMode)
    app2 = service.build(loaded_data=None, prompt="hello")
    assert isinstance(app2.mode, SingleMode)
    assert app2.mode._prompt_text == "hello"


async def test_application_run_delegates_to_build(cli_kernel, monkeypatch):
    service = cli_kernel.resolve_service("application")
    captured = {}

    class _FakeApp:
        async def run(self):
            captured["ran"] = True

    def _fake_build(loaded_data=None, prompt=""):
        captured["loaded_data"] = loaded_data
        captured["prompt"] = prompt
        return _FakeApp()

    monkeypatch.setattr(service, "build", _fake_build)
    await service.run(_args(prompt="hi"), loaded_data={"id": "s1"})
    assert captured == {"loaded_data": {"id": "s1"}, "prompt": "hi", "ran": True}


async def test_interactive_loop_run_delegates(cli_kernel, monkeypatch):
    service = cli_kernel.resolve_service("interactive_loop")
    captured = {}

    class _FakeLoop:
        def __init__(self, loaded_data=None):
            captured["loaded_data"] = loaded_data

        async def run(self):
            captured["ran"] = True

    monkeypatch.setattr(service, "create_loop", lambda loaded_data=None: _FakeLoop(loaded_data))
    await service.run({"id": "s9"})
    assert captured == {"loaded_data": {"id": "s9"}, "ran": True}


async def test_interactive_loop_run_single_delegates(cli_kernel, monkeypatch):
    import src.app_loop._single as single_mod

    service = cli_kernel.resolve_service("interactive_loop")
    captured = {}

    async def _fake(prompt_text):
        captured["prompt"] = prompt_text

    monkeypatch.setattr(single_mod, "run_single_mode_async", _fake)
    await service.run_single("single prompt")
    assert captured == {"prompt": "single prompt"}


async def test_interactive_loop_create_loop_returns_loop(cli_kernel):
    from src.app_loop import InteractiveLoop

    service = cli_kernel.resolve_service("interactive_loop")
    assert isinstance(service.create_loop(None), InteractiveLoop)


async def test_interactive_mode_uses_kernel_service(cli_kernel, monkeypatch):
    from src.application import AppContext, InteractiveMode

    captured = {}

    async def _fake_run(loaded_data=None):
        captured["loaded_data"] = loaded_data

    service = cli_kernel.resolve_service("interactive_loop")
    monkeypatch.setattr(service, "run", _fake_run)
    await InteractiveMode(AppContext(loaded_data={"id": "s2"})).run()
    assert captured == {"loaded_data": {"id": "s2"}}


async def test_single_mode_uses_kernel_service(cli_kernel, monkeypatch):
    from src.application import AppContext, SingleMode

    captured = {}

    async def _fake_run_single(prompt_text):
        captured["prompt"] = prompt_text

    service = cli_kernel.resolve_service("interactive_loop")
    monkeypatch.setattr(service, "run_single", _fake_run_single)
    await SingleMode(AppContext(), "ping").run()
    assert captured == {"prompt": "ping"}


async def test_interactive_mode_fallback_without_kernel(monkeypatch):
    from src.application import AppContext, InteractiveMode
    import src.app_loop as app_loop

    assert get_current_kernel() is None
    captured = {}

    async def _fake(loaded_data=None):
        captured["loaded_data"] = loaded_data

    monkeypatch.setattr(app_loop, "run_interactive_mode_async", _fake)
    await InteractiveMode(AppContext(loaded_data={"id": "s3"})).run()
    assert captured == {"loaded_data": {"id": "s3"}}


async def test_single_mode_fallback_without_kernel(monkeypatch):
    from src.application import AppContext, SingleMode
    import src.app_loop as app_loop

    assert get_current_kernel() is None
    captured = {}

    async def _fake(prompt_text):
        captured["prompt"] = prompt_text

    monkeypatch.setattr(app_loop, "run_single_mode_async", _fake)
    await SingleMode(AppContext(), "pong").run()
    assert captured == {"prompt": "pong"}


async def test_app_plugin_run_modes_uses_application_service(cli_kernel, monkeypatch):
    app = cli_kernel.resolve_service("app")
    service = cli_kernel.resolve_service("application")
    captured = {}

    async def _fake_run(args, loaded_data=None):
        captured["args"] = args
        captured["loaded_data"] = loaded_data

    monkeypatch.setattr(service, "run", _fake_run)
    args = _args(prompt=None)
    await app._run_modes(args, {"id": "s4"})
    assert captured == {"args": args, "loaded_data": {"id": "s4"}}
