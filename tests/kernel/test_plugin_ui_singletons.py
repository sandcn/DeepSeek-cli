"""表现层单例收敛为内核服务测试 — ``ctx.ui`` 独占宽缓存与面板控制器。

覆盖 C 组「表现层装配插件化」：
- ``TerminalWidthCache.get_default()`` 内核优先返回 ``ctx.ui.width_cache``；
- ``SubAgentPanelController.get_default()`` 内核优先返回 ``ctx.ui.subagent_panel``；
- ``ctx.ui.assemble()`` 提供 TUI 子系统装配入口；
- 内核缺失时稳定回退进程级单例。
"""

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


async def test_width_cache_is_kernel_service(cli_kernel):
    from src.tui._screen import TerminalWidthCache

    ui = cli_kernel.resolve_service("ui")
    assert TerminalWidthCache.get_default() is ui.width_cache
    assert TerminalWidthCache.get_default() is ui.width_cache


async def test_subagent_panel_is_kernel_service(cli_kernel):
    from src.tui._subagent_panel import SubAgentPanelController

    ui = cli_kernel.resolve_service("ui")
    assert SubAgentPanelController.get_default() is ui.subagent_panel


async def test_ui_assemble_entrypoint(cli_kernel):
    ui = cli_kernel.resolve_service("ui")
    assert callable(ui.assemble)


def test_ui_singletons_fall_back_without_kernel():
    from src.kernel import set_current_kernel
    from src.tui._screen import TerminalWidthCache
    from src.tui._subagent_panel import SubAgentPanelController

    set_current_kernel(None)
    assert TerminalWidthCache.get_default() is TerminalWidthCache.get_default()
    assert SubAgentPanelController.get_default() is SubAgentPanelController.get_default()
