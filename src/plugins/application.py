"""应用运行时插件 — 提供 ``ctx.interactive_loop`` 与 ``ctx.application``。

「一切皆插件」：应用层不再由入口脚本直接装配——交互主循环（``InteractiveLoop``
与单次模式）与应用模式编排（``Application`` / ``InteractiveMode`` /
``SingleMode``）上移为内核服务：

- ``ctx.interactive_loop``：交互主循环与单次模式的运行入口（按内核服务解析
  UI / 会话 / 命令）；
- ``ctx.application``：应用模式编排（构造 ``AppContext``、选择交互/单次模式、
  驱动 ``Application`` 生命周期）。

二者可按 Profile/Patch 启用、禁用或替换；``plugins/app`` 经 ``ctx.application``
运行应用，``src.application`` 的两种模式经 ``ctx.interactive_loop`` 运行主循环
（内核缺失时回退既有直接调用，保持单元测试与独立调用兼容）。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)


class InteractiveLoopService(Service):
    """交互主循环服务 — 占据 ``ctx.interactive_loop``。"""

    provide = "interactive_loop"
    name = "interactive_loop"
    inject = ("sessions",)

    async def run(self, loaded_data=None) -> None:
        """运行交互式对话主循环。"""
        loop = self.create_loop(loaded_data)
        await loop.run()

    async def run_single(self, prompt_text: str) -> None:
        """运行单次问答模式。"""
        from ..app_loop._single import run_single_mode_async

        await run_single_mode_async(prompt_text)

    def create_loop(self, loaded_data=None):
        """构造一个 ``InteractiveLoop`` 实例（自省/测试用）。"""
        from ..app_loop._loop import InteractiveLoop

        return InteractiveLoop(loaded_data)


class ApplicationService(Service):
    """应用编排服务 — 占据 ``ctx.application``。"""

    provide = "application"
    name = "application"
    inject = ("config", "interactive_loop")

    def build(self, loaded_data=None, prompt: str = ""):
        """构造一个设置了模式的 ``Application`` 实例（未运行）。"""
        from ..application import AppContext, Application, InteractiveMode, SingleMode

        ctx = AppContext(loaded_data=loaded_data)
        mode = SingleMode(ctx, prompt) if prompt else InteractiveMode(ctx)
        app = Application()
        app.set_mode(mode)
        return app

    async def run(self, args, loaded_data=None) -> None:
        """按参数选择运行模式并驱动应用生命周期。"""
        prompt = getattr(args, "prompt", None) or ""
        app = self.build(loaded_data=loaded_data, prompt=prompt)
        await app.run()


@plugin("interactive_loop", inject=["sessions"], provide=["interactive_loop"])
def apply_interactive_loop(ctx):
    return InteractiveLoopService(ctx)


@plugin("application", inject=["config", "interactive_loop"], provide=["application"])
def apply_application(ctx):
    return ApplicationService(ctx)


__all__ = [
    "InteractiveLoopService",
    "ApplicationService",
    "apply_interactive_loop",
    "apply_application",
]
