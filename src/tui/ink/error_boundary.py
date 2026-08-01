"""error_boundary.py — ErrorBoundary 函数组件（组件树异常局部降级）。

ErrorBoundary 在 reconciler 的函数组件渲染 try/except 之上提供边界：
  - 子组件渲染抛异常 → reconciler 沿 return_ 链找到最近带 ``_is_boundary``
    标记的 fiber（ErrorBoundary 组件），记录 ``_boundary_error`` 并渲染
    fallback；未找到边界 → 异常照常传播（崩溃恢复语义保留）。
  - ErrorBoundary 组件经 ``use_error_state()`` 读取自身 fiber 上的
    ``_boundary_error``，非 None 时渲染 ``fallback(props)``（默认占位）。

契约：
  - children 经 props 显式传入：``h(ErrorBoundary, {"fallback": fb, "children": h(Child)})``
    （本引擎函数组件不接收 element.children——统一走 props）。
  - fallback 可调用 **函数组件**（接收 ``props``，经 ``props["error"]`` 读取
    异常对象——P1-3 修复：构造 ``h(fallback, {"error": error})`` 让 reconciler
    在独立 fiber 上渲染，内部可正常使用 use_state/use_effect 等 hooks，不污染
    被包裹组件 hook 链）；或 Element（直接渲染）；fallback 自身抛异常 → 直接
    传播（递归边界：boundary 自身渲染异常不二次兜底）。
  - onError 回调在异常首次发生时调用一次（含 error 信息）。
  - hook 状态机异常（HookStateError，如 hook 顺序/类型错误）不参与边界捕获
    ——编程错误照常传播（reconciler 对 HookStateError 直接 re-raise）。
  - P3-1：ErrorBoundary 一旦捕获错误**永久**渲染 fallback（``_boundary_error``
    不清零）；即使下一帧抛异常组件已被移除，``use_error_state()`` 仍返回旧
    错误——需经 key 变化/重新挂载重置（React 语义对齐）。

设计模式: 模板方法（Template Method）— 渲染骨架 + 异常钩子。
"""

from __future__ import annotations

from typing import Any, Callable

from src.tui.ink import hooks as _hooks
from src.tui.ink.element import Element, TEXT, h

#: callable fallback 构造元素时的内部标记（props 键）——reconciler 据此识别
#: fallback 组件 fiber，其渲染异常不二次参与 boundary 捕获（防递归）。
_FALLBACK_MARKER = "_fallback"


def _default_fallback(error: Any) -> Element:
    """默认 fallback 占位（未提供 fallback prop 时）。"""
    return Element(
        TEXT,
        {"children": f"⚠ {type(error).__name__}: {error}"},
        (),
    )


def _build_fallback_element(props: dict, error: Any) -> Element:
    """根据 boundary props 与 error 构建 fallback 元素（组件与 reconciler 共用）。

    P1-3 修复：callable fallback 构造 ``h(fallback, {"error": error})``——由
    reconciler 在**独立 fiber** 上渲染（fallback 为函数组件时内部 use_state/
    use_effect 等 hooks 读写自身 fiber，不污染被包裹组件 hook 链）。props 带
    内部 ``_fallback`` 标记供 reconciler 识别（fallback 自身抛异常直接传播，
    不二次兜底——递归边界）。

    fallback 自身抛异常 → 直接传播（递归边界：boundary 自身渲染异常不
    二次兜底，保持崩溃恢复语义）。

    Args:
        props: boundary 组件 props（含可选 ``fallback``）。
        error: 捕获的异常对象。

    Returns:
        渲染用的 fallback Element。
    """
    fallback = props.get("fallback")
    if callable(fallback):
        result = h(fallback, {"error": error, _FALLBACK_MARKER: True})
    elif fallback is None:
        result = _default_fallback(error)
    else:
        result = fallback
    if result is None:
        result = Element(TEXT, {"children": ""}, ())
    elif not isinstance(result, Element):
        result = Element(TEXT, {"children": str(result)}, ())
    return result


def ErrorBoundary(props: dict) -> Element:
    """ErrorBoundary 组件：子组件异常局部降级。

    Props:
        fallback: 可调用 **函数组件** ``(props) -> Element|str``（经
            ``props["error"]`` 读取异常；P1-3 契约）或 Element；缺省默认占位。
        onError: 异常回调 ``(error) -> None``（首次发生时调用一次）。
        children: 子元素（Element），经 props 显式传入
            （``h(ErrorBoundary, {"children": h(Child)})``）。

    P3-1（React 语义对齐）：一旦捕获错误**永久**渲染 fallback——``_boundary_error``
    不清零，即使下一帧抛异常组件已被移除，``use_error_state()`` 仍返回旧错误；
    需经 key 变化/重新挂载重置。
    """
    fiber = _hooks._current()
    fiber._is_boundary = True
    error = _hooks.use_error_state()
    if error is not None:
        return _build_fallback_element(props, error)
    children = props.get("children")
    if children is None:
        return Element(TEXT, {"children": ""}, ())
    return children


def create_error_boundary(
    Component: Callable,
    fallback: Callable | Element | None = None,
    onError: Callable | None = None,
) -> Callable:
    """创建带 ErrorBoundary 包裹的函数组件（便利工厂）。

    Args:
        Component: 子组件。
        fallback: fallback（函数组件接收 ``props``/Element/None 默认占位）。
        onError: 异常回调。

    Returns:
        包裹 ErrorBoundary 的新函数组件（``h(Wrapped, props)`` 时 props
        原样透传子组件）。
    """
    def Wrapped(props):
        # P2-3：剥离 key——React 语义中 key 不进入 props（reconciler 用 key
        # 做调和，透传给子组件会作为 prop 渲染进组件 props）。
        child_props = {k: v for k, v in props.items() if k != "key"}
        return h(
            ErrorBoundary,
            {"fallback": fallback, "onError": onError, "children": h(Component, child_props)},
        )

    return Wrapped


__all__ = ["ErrorBoundary", "create_error_boundary"]
