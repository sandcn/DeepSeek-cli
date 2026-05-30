"""_compat — Python 版本兼容模块

提供各版本间行为一致的装饰器/工具函数。
当前目标：
- ``dataclass(slots=True)`` 在 Python<3.10 的兼容
- ``contextlib.aclosing`` 在 Python<3.10 的兼容
- ``asyncio.get_event_loop()`` 在子线程中的安全调用（Python 3.9 兼容）

**提示**：本模块统一管理所有 Python 版本兼容逻辑，各模块从这里导入而非直接使用
标准库中低版本不存在的 API。
"""

from __future__ import annotations

import sys
import asyncio
from dataclasses import dataclass as _orig_dataclass
from dataclasses import field as _field  # noqa: F401 — 重新导出


def dataclass(_cls=None, /, **kwargs):
    """兼容各 Python 版本的 dataclass 装饰器。

    **Python>=3.10**: 完全等同于标准库 ``dataclasses.dataclass``，
    支持 ``slots=True`` 等全部参数。

    **Python<3.10**: 自动忽略 ``slots`` 参数（标准库不支持该参数），
    以普通 dataclass 方式创建，避免 ``TypeError`` 报错。
    注意：slots 优化仅在 3.10+ 生效，低版本无法模拟完整的 slot 行为。
    """
    slots = kwargs.pop("slots", False)

    if _cls is None:
        # 有括号调用：@dataclass(slots=True, ...)
        def wrap(cls):
            return _build_dataclass(cls, slots=slots, **kwargs)

        return wrap

    # 无括号调用：@dataclass
    return _build_dataclass(_cls, slots=slots, **kwargs)


def _build_dataclass(cls, *, slots=False, **kwargs):
    """执行 dataclass 包装 + slots 兼容处理。"""
    if slots and sys.version_info >= (3, 10):
        kwargs["slots"] = True

    return _orig_dataclass(cls, **kwargs)


# ── aclosing（Python 3.10+ 标准库） ────────────────────────

if sys.version_info < (3, 10):
    # contextlib.aclosing 在 Python 3.10 才加入（PEP 807）。
    # 低版本用 @asynccontextmanager 实现等价行为：
    # 在 async with 块退出时（无论正常/异常），自动调用 thing.aclose()。
    import contextlib

    @contextlib.asynccontextmanager
    async def aclosing(thing):
        """兼容版本的 aclosing — 等价于 Python 3.10 的 ``contextlib.aclosing``。

        用法:
            async with aclosing(async_iter):
                async for item in async_iter:
                    ...
        """
        try:
            yield thing
        finally:
            await thing.aclose()
else:
    from contextlib import aclosing  # noqa: F401 — 重新导出


# ── asyncio.get_event_loop 安全调用（Python 3.9 兼容） ──────

def safe_get_event_loop():
    """在当前线程中安全地获取事件循环。

    Python 3.9 中，``asyncio.get_event_loop()`` 在没有事件循环的
    子线程中会抛出 ``RuntimeError: There is no current event loop``。
    本函数将其转换为返回 ``None``，避免异常传播。

    Python 3.10+ 中 ``get_event_loop()`` 仅发出 ``DeprecationWarning``，
    但仍会返回事件循环（或创建新循环）。本函数在 3.10+ 中直接委托
    ``get_event_loop()``，保持向后兼容。

    Returns:
        ``AbstractEventLoop | None`` — 当前线程的事件循环（如有），否则 ``None``
    """
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        # Python 3.9: 没有事件循环的子线程抛出 RuntimeError
        return None
