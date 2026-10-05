"""不变量插件 — 提供 ``ctx.invariants``（运行时自检）。

在独立 Fiber 里断言本插件树拥有的运行期关系（服务合法性、工具注册表同源、
Fiber 依赖一致、可替换组件齐全）。任何违反都会被记录/上报，便于「一切皆
插件」的动态组合在运行期保持自洽。

「一切皆插件」：内置检查的**声明**收敛在 ``src.kernel.invariant_registry``，
每条对应清单中的**独立插件条目**（``invariant``，经
``src.plugins.invariant_entries``）——可被 Profile/Bundle 声明，也可被
Patch/Overlay 按 id 单独禁用、覆盖（替换检查实现）或替换。本服务收到组合根
注入的 ``managed_invariants``（清单已接管的 id，含被禁用的）时抑制对应内置
检查的默认装配；无清单（单元测试、独立调用）时无接管，全部内置检查默认生效。

检查实现集中在 ``src.plugins.invariant_checks``（惰性 import 各依赖领域），
外部插件可经 ``ctx.invariants.register(name, check)`` 追加自定义检查。
"""

from __future__ import annotations

import logging

from ..kernel import Service, plugin
from ..kernel.invariants import InvariantRegistry

_logger = logging.getLogger(__name__)


class InvariantsService(Service):
    """不变量服务 — 占据 ``ctx.invariants``。"""

    provide = "invariants"
    name = "invariants"
    inject = ("tools",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        from ..kernel.invariant_registry import (
            disable_builtin_checks,
            set_managed_builtin_checks,
        )

        managed = cfg.get("managed_invariants") or ()
        if managed:
            undo_managed = set_managed_builtin_checks(managed)
            ctx.effect(lambda: undo_managed)
        disabled = cfg.get("disabled_invariants") or ()
        self._disabled = list(disabled)
        if disabled:
            undo_disabled = disable_builtin_checks(disabled)
            ctx.effect(lambda: undo_disabled)

        self._registry = InvariantRegistry()
        self._install_checks()
        ctx.effect(lambda: self._registry.clear)
        # 启动自检：等内核稳定（无 PENDING/LOADING）后再跑，失败只记日志、不阻断
        # 启动（与 dsh 的 invariant fiber 一致）。若在此同步自检，部分依赖其它
        # 插件注册的服务（如清单条目注册的压缩策略/流式处理器）尚未就绪，会
        # 产生假阳性告警。
        self._schedule_startup_check()

    def _install_checks(self) -> None:
        """把当前生效的内置检查 + 注册表扩展装入本服务的检查注册表。"""
        from ..kernel.invariant_registry import active_invariant_checks, extension_checks

        for name, check in active_invariant_checks().items():
            self._registry.register(name, check)
        for name, check in extension_checks().items():
            self._registry.register(name, check)

    def _schedule_startup_check(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._deferred_startup_check())

    async def _deferred_startup_check(self) -> None:
        import asyncio

        for _ in range(500):
            await asyncio.sleep(0)
            try:
                fibers = list(self.ctx.kernel.fibers())
            except Exception:
                break
            if all(f.state.value not in ("PENDING", "LOADING") for f in fibers):
                break
        try:
            failures = self.check()
        except Exception:
            _logger.debug("启动不变量自检异常", exc_info=True)
            return
        for failure in failures:
            _logger.warning("运行时不变量失败: %s", failure)

    # ── 自省 ─────────────────────────────────────────────

    def names(self) -> list:
        """当前生效的检查名（内置装配 + 扩展）。"""
        return self._registry.names()

    def managed(self) -> list:
        """清单已接管的内置检查 id。"""
        from ..kernel.invariant_registry import managed_check_ids

        return list(managed_check_ids())

    def disabled(self) -> list:
        return sorted(self._disabled)

    # ── 注册 ─────────────────────────────────────────────

    def register(self, name: str, check) -> None:
        """注册一条扩展检查（注册即副作用，随本服务 Fiber 卸载撤销）。"""
        undo = self._registry.register(name, check)
        self.ctx.effect(lambda: undo)

    def unregister(self, name: str) -> bool:
        return self._registry.unregister(name)

    def check(self) -> list:
        return self._registry.check_all(self.ctx.kernel)


@plugin("invariants", inject=["tools"], provide=["invariants"])
def apply(ctx):
    return InvariantsService(ctx)


__all__ = ["InvariantsService", "apply"]
