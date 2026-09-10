"""host 组件注册表 — 允许应用注册自定义 host 标签。

布局与绘制泛化：应用可注册 ``(tag, measure_fn, paint_fn)``：
  - measure_fn(fiber, avail_w) -> (width, height)：测量容器/叶子尺寸。
  - paint_fn(fiber, canvas)：将内容绘制到画布（canvas 为 {col: (char, style)}）。

由 layout._measure / components._paint 在标准 host 标签（box/text/static/
spacer/app）之外查询本注册表。

★ P3（review）：注册表为全局可变对象，被 ``_measure``/``_paint`` 热路径查询
——运行期被任意模块 ``register_host`` 覆盖即静默改变渲染行为。现对「同 tag
重复注册且实现不同」记 warning（幂等覆盖仍允许——测试重注册场景需要），
使语义漂移可观测。
"""

from __future__ import annotations

import logging
from typing import Callable

_logger = logging.getLogger(__name__)

_REGISTRY: dict[str, tuple[Callable, Callable]] = {}


def register_host(tag: str, measure_fn: Callable, paint_fn: Callable) -> None:
    """注册自定义 host 组件（同 tag 重复注册覆盖，实现变化时告警）。

    Args:
        tag: host 标签名。
        measure_fn: ``(fiber, avail_w) -> (width, height)``。
        paint_fn: ``(fiber, canvas)``。
    """
    existing = _REGISTRY.get(tag)
    if existing is not None and existing != (measure_fn, paint_fn):
        _logger.warning("register_host 覆盖已注册 host %s（实现不相同的重注册）", tag)
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
