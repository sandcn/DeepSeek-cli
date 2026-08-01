"""host 组件注册表 — 允许应用注册自定义 host 标签。

布局与绘制泛化：应用可注册 ``(tag, measure_fn, paint_fn)``：
  - measure_fn(fiber, avail_w) -> (width, height)：测量容器/叶子尺寸。
  - paint_fn(fiber, canvas)：将内容绘制到画布（canvas 为 {col: (char, style)}）。

由 layout._measure / components._paint 在标准 host 标签（box/text/static/
spacer/app）之外查询本注册表。
"""

from __future__ import annotations

from typing import Callable

_REGISTRY: dict[str, tuple[Callable, Callable]] = {}


def register_host(tag: str, measure_fn: Callable, paint_fn: Callable) -> None:
    """注册自定义 host 组件。

    Args:
        tag: host 标签名。
        measure_fn: ``(fiber, avail_w) -> (width, height)``。
        paint_fn: ``(fiber, canvas)``。
    """
    _REGISTRY[tag] = (measure_fn, paint_fn)


def unregister_host(tag: str) -> None:
    """注销自定义 host（测试用）。"""
    _REGISTRY.pop(tag, None)


def get_host(tag: str) -> tuple[Callable, Callable] | None:
    """查询自定义 host 组件。"""
    return _REGISTRY.get(tag)


def has_host(tag: str) -> bool:
    return tag in _REGISTRY


__all__ = ["register_host", "unregister_host", "get_host", "has_host"]
