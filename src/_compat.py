"""_compat — Python 版本兼容模块

提供各版本间行为一致的装饰器/工具函数。
当前目标：
- ``dataclass(slots=True)`` 在 Python<3.10 的兼容

**提示**：本模块统一管理所有 Python 版本兼容逻辑，各模块从这里导入而非直接使用
标准库中低版本不存在的 API。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass as _orig_dataclass


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

