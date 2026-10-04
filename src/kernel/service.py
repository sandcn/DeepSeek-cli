"""Service — 插件向其他插件提供能力的基类。

插件可以是 ``@plugin`` 装饰的函数，也可以是 ``Service`` 子类。Service 子类
在实例化时把自己注册到当前上下文，占据一个稳定的 ``ctx.<key>``。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional


def _keys(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


class Service:
    """服务基类。

    子类可声明：

    - ``provide``：本服务占据的上下文 key（字符串或字符串序列）；
    - ``inject``：依赖的服务 key；
    - ``name``：服务名（缺省用类名）。
    """

    provide: Any = ()
    inject: Any = ()
    name: str = ""
    config: dict = {}

    def __init__(self, ctx: Any, config: Optional[dict] = None) -> None:
        self.ctx = ctx
        self.config = dict(config) if config else {}
        for key in _keys(self.provide):
            ctx.provide(key, self)

    @property
    def service_name(self) -> str:
        return self.name or type(self).__name__

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<Service {self.service_name}>"


__all__ = ["Service"]
