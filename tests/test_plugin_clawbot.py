"""ClawBot 插件测试 — ``ctx.clawbot`` 承载微信远程控制模式装配。"""

from __future__ import annotations

import pytest

from src.plugins.bootstrap import build_kernel, shutdown_kernel


@pytest.fixture
async def cli_kernel():
    kernel = await build_kernel("cli")
    try:
        yield kernel
    finally:
        await shutdown_kernel(kernel)


async def test_clawbot_service_present(cli_kernel):
    from src.plugins.clawbot import ClawbotService

    assert isinstance(cli_kernel.resolve_service("clawbot"), ClawbotService)


async def test_clawbot_run_delegates(cli_kernel, monkeypatch):
    service = cli_kernel.resolve_service("clawbot")
    called = {}

    async def _fake(model=None, re_login=False, tui=True):
        called.update(model=model, re_login=re_login, tui=tui)

    import src.clawbot.runner as runner

    monkeypatch.setattr(runner, "run_clawbot", _fake)
    await service.run(model="m", re_login=True, tui=False)
    assert called == {"model": "m", "re_login": True, "tui": False}
