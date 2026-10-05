"""上下文插件 — 提供 ``ctx.context``。

「一切皆插件」：上下文管理（压缩策略 / 上下文管理器创建）从领域硬编码
上移为内核服务。默认策略链是 ``[SummarizeStrategy, DropStrategy]``；外部
插件可经 ``ctx.context.register_strategy(name, factory)`` 注册自定义压缩
策略，或用 ``ctx.context.set_strategy_builder(fn)`` 整体替换策略构建逻辑。

``ChatSession`` 经 ``active_context_manager_factory()`` 使用本服务创建
``ContextManager``（内核缺失时回退直接构造），因此替换策略即可改变整条
上下文压缩链路。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..kernel import Service, plugin

_logger = logging.getLogger(__name__)

#: 默认策略名（按优先级排序）
DEFAULT_STRATEGY_NAMES: Tuple[str, ...] = ("summarize", "drop")


def _summarize_factory():
    from ..core.compression import SummarizeStrategy

    return SummarizeStrategy()


def _drop_factory():
    from ..core.compression import DropStrategy

    return DropStrategy()


def _builtin_strategies() -> Dict[str, Callable[[], Any]]:
    return {
        "summarize": _summarize_factory,
        "drop": _drop_factory,
    }


class ContextService(Service):
    """上下文服务 — 占据 ``ctx.context``。"""

    provide = "context"
    name = "context"
    inject = ("config",)

    def __init__(self, ctx, config=None):
        super().__init__(ctx, config)
        cfg = config or getattr(ctx, "config", None) or {}
        self._strategies: Dict[str, Callable[[], Any]] = _builtin_strategies()
        self._default_names: Tuple[str, ...] = tuple(cfg.get("default_strategies") or DEFAULT_STRATEGY_NAMES)
        self._builder: Optional[Callable[[Optional[Tuple[str, ...]]], list]] = None
        # 清单接管 / 显式禁用：内置策略的默认装配被抑制，仅条目注册者生效
        self._managed: set = set(cfg.get("managed_strategies") or ())
        self._disabled: set = set(cfg.get("disabled_strategies") or ())
        for name in self._managed | self._disabled:
            self._strategies.pop(name, None)
        ctx.effect(lambda: self._on_unload)

    def _on_unload(self) -> None:
        self._strategies = {}
        self._builder = None

    # ── 注册表 ───────────────────────────────────────────

    def strategy_names(self) -> List[str]:
        return sorted(self._strategies)

    def managed(self) -> List[str]:
        """被清单接管的策略名（默认装配被抑制，由独立条目注册）。"""
        return sorted(self._managed)

    def disabled(self) -> List[str]:
        """被显式禁用的策略名。"""
        return sorted(self._disabled)

    def register_strategy(self, name: str, factory: Callable[[], Any]) -> Callable[[], None]:
        """注册自定义压缩策略工厂（注册即副作用，卸载时自动撤销）。"""
        if not isinstance(name, str) or not name:
            raise ValueError(f"策略名必须是非空字符串: {name!r}")
        if not callable(factory):
            raise TypeError(f"策略工厂必须可调用: {factory!r}")
        self._strategies[name] = factory
        released = False

        def _undo() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._strategies.pop(name, None)

        self.ctx.effect(lambda: _undo)
        return _undo

    def unregister_strategy(self, name: str) -> bool:
        return self._strategies.pop(name, None) is not None

    def get_strategy(self, name: str) -> Callable[[], Any]:
        factory = self._strategies.get(name)
        if factory is None:
            raise KeyError(f"未注册的压缩策略: {name!r}（可用: {self.strategy_names()}）")
        return factory

    def build_strategies(self, names: Optional[Tuple[str, ...]] = None) -> list:
        """按名字构建策略实例列表（None 使用默认策略链）。

        清单接管/禁用导致某默认策略缺失时自动跳过，保证压缩链路不因缺项中断。
        """
        if self._builder is not None:
            return list(self._builder(names))
        selected = tuple(names) if names else self._default_names
        return [self.get_strategy(name)() for name in selected if name in self._strategies]

    def set_strategy_builder(self, builder: Optional[Callable]) -> Any:
        """整体替换策略构建器，返回旧构建器（None 表示回退内置构建）。"""
        previous = self._builder
        self._builder = builder
        return previous

    def default_names(self) -> Tuple[str, ...]:
        return self._default_names

    # ── 上下文管理器创建 ─────────────────────────────────

    def manager(self, messages, model: Optional[str] = None, **kwargs):
        """创建 ``ContextManager``（唯一对外创建入口）。

        ``config_port`` / ``strategies`` 未显式传入时用本服务的默认值。
        """
        from ..core.context_manager import ContextManager

        kwargs.setdefault("config_port", self.ctx.consume("config").port)
        kwargs.setdefault("strategies", self.build_strategies())
        if model is None:
            model = self.ctx.consume("config").model()
        return ContextManager(messages, model, **kwargs)

    def build_strategies_for_names(self, names) -> list:
        """按显式名字列表构建策略（外部插件用）。"""
        return self.build_strategies(tuple(names) if names else None)


@plugin("context", inject=["config"], provide=["context"])
def apply(ctx):
    return ContextService(ctx, ctx.config)


__all__ = ["ContextService", "DEFAULT_STRATEGY_NAMES", "apply"]
