"""布局树遍历辅助 — host 子节点收集 / function 链下降。

模块边界（2026-08-05 架构优化）：从 ``ink/layout.py`` 拆分——树遍历为
纯结构操作（无布局副作用），独立成模块供 ``_layout_transform``（reflow
重排）/``_layout_measure``（测量子节点收集）/``_layout_absolute``
（绝对定位遍历）共享。
"""

from __future__ import annotations

from .fiber import Fiber


def _skip_function(fiber: Fiber | None) -> Fiber | None:
    """沿 function 链下降，返回首个 host fiber（或 None）。"""
    f = fiber
    while f is not None and f.is_function:
        f = f.child
    return f


def layout_children(fiber: Fiber) -> list[Fiber]:
    """返回 fiber 的直接 host 子节点（跳过 function 链）。

    方向1（Fragment 支持）：Fragment host（``fragment``）为透明分组容器——
    其子节点递归扁平化直接流入父容器布局（不产生独立布局盒）。嵌套 Fragment
    经递归自然展开。

    ★ P-H2（性能）：直接 host 子节点快速路径——``_skip_function`` 对非
    function fiber 立即返回自身，但每次调用有函数调用开销（10Hz 大组件树
    每容器每帧重复调用）。改为：function fiber 才走 ``_skip_function``，
    普通 host 子节点直接处理（行为与 ``_skip_function`` 等价——其对 host
    节点恒返回自身）。
    """
    result: list[Fiber] = []
    child = fiber.child
    while child is not None:
        if child.is_function:
            host = _skip_function(child)
        else:
            host = child
        if host is not None:
            if host.is_host and host.type == "fragment":
                result.extend(layout_children(host))
            else:
                result.append(host)
        child = child.sibling
    return result


__all__ = ["_skip_function", "layout_children"]
