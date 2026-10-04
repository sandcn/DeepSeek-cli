"""插件定义 — 函数插件与 Service 子类插件的统一描述。

插件（Plugin）是一个描述对象，可来自：

1. ``@plugin`` 装饰的函数：``def apply(ctx): ...``；
2. ``Service`` 子类：提供 ``apply``/``__init__`` 及 ``inject``/``provide`` 声明；
3. 普通可调用对象（函数）。

插件可声明：

- ``inject``：依赖的服务 key 列表；全部就绪前 Fiber 停在 PENDING；
- ``provide``：本插件提供的服务 key（Service 子类用，用于文档与自省）；
- ``config``：默认配置字典。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .errors import PluginError


@dataclass
class Plugin:
    """插件描述。"""

    name: str
    apply: Optional[Callable[[Any], Any]] = None
    service_cls: Optional[type] = None
    inject: tuple[str, ...] = ()
    provide: tuple[str, ...] = ()
    config: dict = field(default_factory=dict)
    source: str = ""

    # ── 加载 ────────────────────────────────────────────

    def instantiate(self, ctx: Any) -> Any:
        """在给定上下文上实例化插件，返回可调用结果。"""
        if self.service_cls is not None:
            return self.service_cls(ctx, ctx.config if hasattr(ctx, "config") else {})
        if self.apply is not None:
            return self.apply(ctx)
        raise PluginError(f"插件 {self.name!r} 没有可执行的 apply/service_cls")

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        kind = "service" if self.service_cls is not None else "function"
        return f"<Plugin {self.name!r} ({kind}) inject={list(self.inject)}>"


def _normalize_keys(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    raise PluginError(f"inject/provide 声明非法: {value!r}")


def plugin(
    name: Optional[str] = None,
    *,
    inject: Any = None,
    provide: Any = None,
    config: Optional[dict] = None,
):
    """装饰器：把函数或 Service 子类标记为插件。

    用法::

        @plugin("tools", inject=["config"])
        async def apply(ctx):
            ...
    """

    def _decorate(target: Any) -> Plugin:
        if inspect.isclass(target):
            return _from_service_class(target, name=name, inject=inject, provide=provide, config=config)
        if callable(target):
            return _from_callable(target, name=name, inject=inject, provide=provide, config=config)
        raise PluginError(f"无法将 {target!r} 作为插件注册")

    return _decorate


def _from_callable(
    func: Callable,
    *,
    name: Optional[str],
    inject: Any,
    provide: Any,
    config: Optional[dict],
) -> Plugin:
    declared_inject = inject if inject is not None else getattr(func, "inject", ())
    declared_provide = provide if provide is not None else getattr(func, "provide", ())
    declared_config = config if config is not None else getattr(func, "config", {})
    return Plugin(
        name=name or getattr(func, "plugin_name", None) or getattr(func, "__name__", "anonymous"),
        apply=func,
        inject=_normalize_keys(declared_inject),
        provide=_normalize_keys(declared_provide),
        config=dict(declared_config or {}),
        source=getattr(func, "__module__", ""),
    )


def _from_service_class(
    cls: type,
    *,
    name: Optional[str],
    inject: Any,
    provide: Any,
    config: Optional[dict],
) -> Plugin:
    declared_inject = inject if inject is not None else getattr(cls, "inject", ())
    declared_provide = provide if provide is not None else getattr(cls, "provide", ())
    declared_config = config if config is not None else getattr(cls, "config", {})
    return Plugin(
        name=name or getattr(cls, "plugin_name", None) or getattr(cls, "name", None) or cls.__name__,
        service_cls=cls,
        inject=_normalize_keys(declared_inject),
        provide=_normalize_keys(declared_provide),
        config=dict(declared_config or {}),
        source=getattr(cls, "__module__", ""),
    )


def as_plugin(obj: Any, *, name: Optional[str] = None) -> Plugin:
    """把任意对象转换为 Plugin（已标记对象原样返回）。"""
    if isinstance(obj, Plugin):
        return obj
    if inspect.isclass(obj):
        return _from_service_class(obj, name=name, inject=None, provide=None, config=None)
    if callable(obj):
        return _from_callable(obj, name=name, inject=None, provide=None, config=None)
    raise PluginError(f"无法转换 {obj!r} 为插件")


__all__ = ["Plugin", "plugin", "as_plugin"]
