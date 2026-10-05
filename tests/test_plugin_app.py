"""应用组合根插件测试 — ``ctx.app`` 承载应用生命周期装配。

覆盖：
- 内核提供 ``ctx.app``（AppService）；
- ``run`` 的分发逻辑（version / dump-config / check-invariants）经插件完成；
- bootstrap / stop / shutdown 的幂等与清理语义。
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def headless_kernel():
    kernel = await build_kernel("headless")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


def _args(**kwargs) -> Namespace:
    base = {
        "command": None,
        "version": False,
        "dump_config": False,
        "check_invariants": False,
        "patch": None,
        "verbose": 0,
        "model": None,
        "load": None,
        "prompt": None,
    }
    base.update(kwargs)
    return Namespace(**base)


async def test_app_service_present(headless_kernel):
    from src.plugins.app import AppService

    assert isinstance(headless_kernel.resolve_service("app"), AppService)


async def test_app_run_version_dispatches(headless_kernel, monkeypatch):
    app = headless_kernel.resolve_service("app")
    calls = []
    monkeypatch.setattr(app, "bootstrap", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(app, "register_signals", lambda: calls.append("signals"))
    monkeypatch.setattr(app, "stop", lambda: calls.append("stop"))

    from src.tui.events import consumers

    monkeypatch.setattr(
        consumers, "publish_output",
        lambda text, level="info", source="": calls.append(("output", text)),
    )

    await app.run(_args(command="version", version=True))
    assert calls[0] == "bootstrap"
    assert "signals" in calls
    assert any(isinstance(c, tuple) and "Chat" in c[1] for c in calls)
    assert calls[-1] == "stop"


async def test_app_run_check_invariants(headless_kernel, monkeypatch):
    app = headless_kernel.resolve_service("app")
    monkeypatch.setattr(app, "bootstrap", lambda: None)
    monkeypatch.setattr(app, "register_signals", lambda: None)
    monkeypatch.setattr(app, "stop", lambda: None)

    async def _no_mcp():
        return None

    monkeypatch.setattr(app, "_setup_mcp", _no_mcp)

    from src.tui.events import consumers

    captured = []
    monkeypatch.setattr(
        consumers, "publish_output",
        lambda text, level="info", source="": captured.append(text),
    )

    await app.run(_args(check_invariants=True))
    joined = "\n".join(captured)
    assert "运行时不变量" in joined


async def test_app_bootstrap_and_shutdown(headless_kernel):
    app = headless_kernel.resolve_service("app")
    app.bootstrap()
    assert app._bootstrapped is True
    app.bootstrap()
    assert app._bootstrapped is True
    app.shutdown()
    assert app._output_consumer is None
