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
from dataclasses import field as _field
from typing import Any, Callable, TypeVar, overload

# ★ 类型检查支持（P0-2）：本模块的 ``dataclass`` 是标准库 dataclass 的运行时
#   包装函数——静态类型检查器无法推断它生成的 ``__init__`` 签名，导致所有
#   ``@dataclass`` 类（如 ink.output.StyledRun）的构造调用被 mypy 误报
#   "Too many arguments"（全项目 200+ 条）。``typing.dataclass_transform``
#   （PEP 681，Python 3.11+；3.9/3.10 经 typing_extensions）告知类型检查器
#   本装饰器等价于 dataclass，恢复 ``__init__``/``__eq__`` 等生成成员的签名。
try:  # Python 3.11+
    from typing import dataclass_transform as _dataclass_transform
except ImportError:  # pragma: no cover - Python < 3.11
    try:
        from typing_extensions import dataclass_transform as _dataclass_transform
    except ImportError:  # pragma: no cover - 无 typing_extensions 时降级为无操作
        def _dataclass_transform(**kwargs: Any):
            def _decorator(obj):
                return obj

            return _decorator


_T = TypeVar("_T")


@overload
def dataclass(cls: type[_T], /, **kwargs: Any) -> type[_T]: ...


@overload
def dataclass(
    *,
    init: bool = ...,
    repr: bool = ...,
    eq: bool = ...,
    order: bool = ...,
    unsafe_hash: bool = ...,
    frozen: bool = ...,
    slots: bool = ...,
    **kwargs: Any,
) -> Callable[[type[_T]], type[_T]]: ...


@_dataclass_transform(field_specifiers=(_field,))
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
    if slots and sys.version_info < (3, 10):
        # Python<3.10：标准库 dataclass 不支持 slots 参数——先应用 dataclass
        # 生成 __init__/__repr__/__eq__ 等方法（字段默认值嵌入 __init__ 参数），
        # 再用 type() 重建类注入 __slots__（顺序与 CPython 3.10 同构）。
        return _orig_dataclass_with_slots(cls, **kwargs)
    if slots:
        kwargs["slots"] = True

    return _orig_dataclass(cls, **kwargs)


def _orig_dataclass_with_slots(cls, **kwargs):
    """低版本（<3.10）手动模拟 ``dataclass(slots=True)``。

    两步（与 CPython 3.10 ``dataclasses._process_class`` slots 分支同构）：
    1. 先 ``_orig_dataclass(cls)``：生成 __init__/__repr__/__eq__/__hash__
       等方法，字段默认值（``char: str = ""`` 等类变量）嵌入 __init__
       参数默认值；
    2. ``type()`` 重建类：复制处理后的类命名空间（过滤 __dict__/
       __weakref__ 描述符），**移除字段默认值类变量**（默认值已在 __init__
       参数中，类变量不再需要——type() 禁止 __slots__ 名称与类变量同名），
       设置 ``__slots__ = 字段名 + ('__weakref__',)``。重建发生在**类语句
       执行期**（外部 ``cls.__slots__ = ...`` 赋值无效——__dict__ 描述符在
       类创建时已由 type.__new__ 决定），实例无 __dict__，与 3.10+
       slots=True 语义对齐。

    限制（2026-08-06 文档化，review 确认当前全部使用点均不触发）：
    - **仅支持无显式基类的 dataclass**（项目内 ``@dataclass(slots=True)``
      使用点均为直接定义）；存在基类时退化为普通 dataclass（slots 不生效）。
      注意：``base = next(iter(cls.__bases__), None)`` 只检查第一个基类——
      ``class C(object, Mixin)`` 多基类场景下不退化为普通 dataclass，
      ``type(cls.__name__, (), ns)`` 会静默丢弃 Mixin（3.10 同构分支保留
      ``cls.__bases__``，本实现简化）；当前无此使用点，勿新增多基类 slots
      dataclass。
    - **ClassVar/InitVar 注解字段**会被误加入 ``__slots__`` 并 pop 掉类变量
      （CPython 3.10 slots 分支会剔除 ``_FIELD_CLASSVAR``/``_FIELD_INITVAR``，
      不进 ``__slots__``）；当前无此使用点，勿在 slots dataclass 内使用
      ClassVar 注解（类常量直接无注解赋值——见 ``core/color.py``
      ``GradientDescriptor.__effect_options`` 的修复）。
    - **自定义元类**：使用 ``type(cls)`` 而非默认 ``type`` 保留元类
      （3.10 同构分支亦然）；当前无自定义元类使用点。
    """
    cls = _orig_dataclass(cls, **kwargs)
    base = next(iter(cls.__bases__), None)
    if base is not None and base is not object:
        return cls
    ns = {
        k: v for k, v in cls.__dict__.items()
        if k not in ("__dict__", "__weakref__")
    }
    names = tuple(ns.get("__annotations__", {}).keys())
    if not names:
        return cls
    for name in names:
        ns.pop(name, None)
    ns["__slots__"] = names + ("__weakref__",)
    # ★ 2026-08-06：用 ``type(cls)`` 重建——自定义元类 slots dataclass 保留
    #   元类（修复前默认 ``type`` 静默丢失元类）。
    return type(cls)(cls.__name__, (), ns)

