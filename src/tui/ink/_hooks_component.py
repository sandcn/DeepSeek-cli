"""hooks 组件族 — useApp / memo / forwardRef / useImperativeHandle /
use_error_state / usePrevious + app control / render flush / 终端挂起注入。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——组件级 hooks
独立成模块（ErrorBoundary 内部 hook / 命令式句柄 / 应用控制 / 渲染生命周期
扩展），供 session（``set_app_control``/``set_render_flush_fn``/
``set_suspend_terminal_fn``）、error_boundary（``use_error_state``）、
组件库（memo/forwardRef/useImperativeHandle/useApp）共享。依赖
``_hooks_core``（``_next_hook``/``_current``/``_memo_deps_changed``/
``use_ref``）。

依赖方向：本模块 → _hooks_core / fiber；不反向依赖。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .fiber import MemoHook, EffectHook
from ._hooks_core import (
    _current,
    _next_hook,
    _memo_deps_changed,
    use_ref,
)
# ★ 模块级可变状态唯一真源在 hooks.py 门面（见 _hooks_core.py 注释）。
from src.tui.ink import hooks as _hooks_module

# ★ logger 名保持 ``src.tui.ink.hooks``（模块拆分后日志命名不变，见
#   _hooks_core.py 注释）。
_logger = logging.getLogger("src.tui.ink.hooks")


# ═══════════════════════════════════════════════════════════
# use_error_state（方向B 步骤9 — ErrorBoundary 内部 hook）
# ═══════════════════════════════════════════════════════════


def use_error_state() -> Any:
    """ErrorBoundary 内部 hook：读取 reconciler 注入的 boundary error。

    子组件渲染异常被边界捕获后，reconciler 将异常对象记录到边界 fiber 的
    ``_boundary_error`` 字段；ErrorBoundary 组件下次渲染经本 hook 读取，
    非 None 时渲染 ``fallback(error)``（或默认占位）。

    Returns:
        boundary error（异常对象）；None 表示无错误。
    """
    fiber = _current()
    return getattr(fiber, "_boundary_error", None)


# ═══════════════════════════════════════════════════════════
# useImperativeHandle / forwardRef（完善 react ink）
# ═══════════════════════════════════════════════════════════


def _make_imperative_cleanup(hook: MemoHook) -> Callable:
    """构造 useImperativeHandle 的 effect create（返回卸载清理函数）。

    卸载清理：清空「**最近一次写入的 ref**」（``hook._last_ref``）——
    父组件换入新 ref 对象时 useImperativeHandle 渲染期已把句柄写入新 ref，
    destroy 须清理最近写入的 ref（修复前 effect create 闭包捕获的是创建时
    的旧 ref——父组件换入新 ref 对象但用户 deps 未变（effect 未重建）时
    旧 destroy 只清旧 ref，新 ref.current 残留句柄引用，P3-7 换 ref 泄漏）。

    引用检查语义（React）：deps 变化后旧 destroy 不得清掉新句柄——destroy
    仅当 ``ref.current is value``（仍指向本组件最近一次句柄）时置 None；
    新句柄已写入（``value is not`` 匹配）时不清。
    """

    def _create():
        value = hook.value

        def _destroy():
            ref = getattr(hook, "_last_ref", None)
            if ref is not None and getattr(ref, "current", None) is value:
                ref.current = None

        return _destroy

    return _create


def forwardRef(fn: Callable) -> Callable:
    """React.forwardRef 等价物：透传 ref 给渲染函数。

    返回标记 ``_is_forward_ref`` 的包装函数；reconciler 调用函数组件时检测
    该标记，改以 ``(props, ref)`` 双参调用（ref 取自 ``props.ref``——React
    约定 ref 作为 prop 传入，不进入普通 props）。

    用法::

        def _Inner(props, ref):
            useImperativeHandle(ref, lambda: {"focus": do_focus}, ())
            return h(TEXT, {"children": "inner"})
        Inner = forwardRef(_Inner)
        # 父组件
        ref = use_ref(None)
        h(Inner, {"ref": ref})
        # 之后 ref.current.focus()

    Args:
        fn: ``(props, ref) -> Element`` 渲染函数。

    Returns:
        带 ``_is_forward_ref`` 标记的函数组件（fiber key 保留原函数模块限定）。
    """
    def Forwarded(props, ref=None):
        return fn(props, ref if ref is not None else props.get("ref"))

    Forwarded._is_forward_ref = True
    Forwarded._forward_ref_fn = fn
    Forwarded.__name__ = getattr(fn, "__name__", "Forwarded")
    Forwarded.__module__ = getattr(fn, "__module__", __name__)
    return Forwarded


def useImperativeHandle(ref, factory: Callable, deps: list | tuple | None = None) -> None:
    """React useImperativeHandle 等价物：向父组件暴露命令式句柄。

    依赖变化（或挂载）时执行 ``factory()`` 写入 ``ref.current``；组件卸载时
    置 ``ref.current = None``（React 语义）。deps=None 表示每次渲染都更新。

    与 React 一致：ref 为 ``use_ref`` 返回的 ``RefHook``（``.current`` 可变）。

    Args:
        ref: 父组件传入的 ref 对象（RefHook 或任意带 ``.current`` 的对象）。
        factory: 生成句柄的工厂函数。
        deps: 依赖列表；None 表示每次渲染都更新。
    """
    hook = _next_hook(MemoHook, factory, deps, None, None)
    hook.factory = factory
    hook.deps = list(deps) if deps is not None else deps
    memo_changed = _memo_deps_changed(hook)
    if memo_changed:
        hook.value = factory()
        hook.last_deps = list(hook.deps) if hook.deps is not None else None
    if ref is not None:
        # ★ P3 修复（review 方向）：deps 未变化（memo_changed=False）但 ref
        #   身份变化（父组件传入新 ref 对象）时仍把当前 ``hook.value`` 写入
        #   ``ref.current``——修复前仅 memo_changed 分支写 ref，新 ref.current
        #   恒为 None（父组件拿不到句柄）。
        # ★ P3-7（review 方向）：记录「最近一次写入的 ref」——destroy 据此
        #   清理（_make_imperative_cleanup 不再捕获创建时 ref，父组件换入新
        #   ref 对象时新 ref.current 残留句柄被正确清理）。
        hook._last_ref = ref
        ref.current = hook.value
    # 卸载清理（EffectHook 通道）：**恒消费 2 槽**（deps 含句柄身份——句柄
    # 重建时旧 destroy 不会清掉新句柄（_make_imperative_cleanup 引用检查）。
    # ★ BUG-37（review 方向）：修复前 ``ref is not None`` 才消费 EffectHook
    #   槽、``ref is None`` 时不消费——ref 在渲染间从 None ↔ 非 None 切换时
    #   后续 hook 下标错位（HookStateError 或静默状态错配，违反 Rules of
    #   Hooks）。恒消费 2 槽；ref 为 None 时 EffectHook 置空（deps=None →
    #   每帧执行；create=None 且旧 destroy 残留时先清理旧 ref——ref 从非
    #   None 变 None 场景正确释放）。
    if ref is not None:
        eff = _next_hook(EffectHook, None, None, None, None)
        eff.create = _make_imperative_cleanup(hook)
        # ★ P1 修复（review 方向）：deps **无条件**设置——修复前仅
        #   ``memo_changed`` 分支写 eff.deps：ref 从 None → 非 None 且用户
        #   deps 未变（memo_changed=False）时 eff.deps 残留 None（ref=None
        #   渲染期写入）→ ``deps_changed()`` 恒 True（deps=None 表示每帧
        #   执行）→ 每帧提交期执行 destroy → 渲染期刚写入的句柄被清空。
        #   现无条件设置 deps（ref/hook/句柄身份三元组），last_deps 仅在
        #   memo_changed 时重置（None=首次需执行）；memo_changed=False 时
        #   保留上帧 last_deps（提交后 mark_effect_committed 记录）——
        #   deps 未变 → deps_changed() False → 不执行 destroy，句柄保留。
        eff.deps = (id(ref), id(hook), id(hook.value))
        eff.last_deps = None if memo_changed else getattr(eff, "last_deps", None)
    else:
        eff = _next_hook(EffectHook, None, None, None, None)
        eff.create = None
        eff.deps = None
        eff.last_deps = None


# ═══════════════════════════════════════════════════════════
# useApp / memo（方向B 步骤10）
# ═══════════════════════════════════════════════════════════


def memo(Component: Callable, are_equal: Callable | None = None) -> Callable:
    """React.memo 等价物：组件级渲染短路。

    返回带 ``_is_memo``/``_are_equal`` 标记的包装函数；reconciler 在
    props 未变（``are_equal`` 或默认浅比较 ``props == last_props``）
    且无待处理 state 更新时跳过组件函数调用与子树重建（memo 短路）。

    Args:
        Component: 函数组件。
        are_equal: 自定义相等比较 ``(prev_props, next_props) -> bool``；
            None 时默认浅比较（``==``；props 含不可比较对象时 try/except
            兜底视为不等 → 重渲染，安全侧）。

    Returns:
        包装后的 memo 组件函数（保留原组件名/模块，避免 fiber key 冲突）。
    """
    def Memoized(props):
        return Component(props)

    Memoized._is_memo = True
    Memoized._are_equal = are_equal
    Memoized.__name__ = getattr(Component, "__name__", "Memoized")
    Memoized.__module__ = getattr(Component, "__module__", __name__)
    return Memoized


def set_app_control(control: dict | None) -> None:
    """注入 app control（session 注入：``{"exit": fn, "clear": fn}``）。

    对齐既有测试契约：``_hooks._app_control["exit"]`` 可调用。
    ``None`` 清除注入（测试清理路径）。
    """
    _hooks_module._app_control = control


#: 别名（保留 ``set_app_callbacks`` 命名兼容；二者等价）
set_app_callbacks = set_app_control


def set_render_flush_fn(fn: Callable[[], Any] | None) -> None:
    """注入渲染 flush 等待回调（session 调用）。"""
    _hooks_module._render_flush_fn = fn


def set_suspend_terminal_fn(fn: Callable[[Any], Any] | None) -> None:
    """注入终端挂起回调（session 调用——editor/子进程流程）。"""
    _hooks_module._suspend_terminal_fn = fn


def useApp() -> dict:
    """React useApp 等价物：返回应用控制函数 ``{"exit", "clear",
    "waitUntilRenderFlush", "suspendTerminal"}``。

    - ``exit``：请求退出（session 置 exit_requested + 停止渲染，幂等）。
    - ``clear``：请求全帧清屏重绘（非全屏模型：强制全量重绘，非 DECSTBM
      清屏——文档注明与 react-ink 的差异）。
    - ``waitUntilRenderFlush``（React Ink v6）：返回 awaitable，等待渲染
      flush 完成（session 注入回调；未注入时返回已解决 awaitable）。
    - ``suspendTerminal``（React Ink v6）：挂起终端给子进程（callback 提供
      时同步执行并重绘；未注入时直接执行 callback）。

    未注入控制时返回 no-op（安全兜底，不抛异常）。
    """
    control = _hooks_module._app_control or {}

    def _noop(*args, **kwargs):
        return None

    async def _already_flushed():
        return None

    def _flush():
        if _hooks_module._render_flush_fn is not None:
            try:
                return _hooks_module._render_flush_fn()
            except Exception:
                # ★ P3-6（review 方向）：不静默吞异常——记 debug 日志（flush
                #   回调异常降级为已解决 awaitable，不中断渲染，但须可观测）。
                _logger.debug("render flush 回调异常，降级为已解决 awaitable", exc_info=True)
        return _already_flushed()

    def _suspend(callback=None):
        if _hooks_module._suspend_terminal_fn is not None:
            try:
                return _hooks_module._suspend_terminal_fn(callback)
            except Exception:
                # ★ P3-6（review 方向）：不静默吞异常——记 debug 日志（终端
                #   挂起回调异常降级为直接执行 callback，但须可观测）。
                _logger.debug("终端挂起回调异常，降级为直接执行 callback", exc_info=True)
        if callback is not None:
            try:
                callback()
            except Exception:
                # ★ P3-6（review 方向）：不静默吞异常——记 debug 日志（降级
                #   路径 callback 异常不传播（挂起流程已降级），但须可观测）。
                _logger.debug("suspendTerminal 降级 callback 异常", exc_info=True)
        return None

    return {
        "exit": control.get("exit") or _noop,
        "clear": control.get("clear") or _noop,
        "waitUntilRenderFlush": _flush,
        "suspendTerminal": _suspend,
    }


# ═══════════════════════════════════════════════════════════
# usePrevious（完善 react ink hook 库）
# ═══════════════════════════════════════════════════════════


def usePrevious(value: Any) -> Any:
    """返回上一次渲染时的值（React 社区标准 hook，完善 ink hook 库）。

    首次渲染返回 None；后续渲染返回上一帧传入的值。实现基于 ``use_ref``
    （渲染期先读旧值再写新值）——跨渲染保持、不触发额外重渲染。

    典型用途：状态变化检测（``use_effect`` 依赖旧值执行过渡逻辑）、
    条件动画（值变化时切换显示风格）。与 ``use_memo`` 的 deps 语义互补
    （deps 解决「何时重算」，usePrevious 解决「旧值是什么」）。

    Args:
        value: 当前渲染值（任意类型）。

    Returns:
        上一次渲染时的 value；首次渲染返回 None。
    """
    ref = use_ref(None)
    prev = ref.current
    ref.current = value
    return prev


__all__ = [
    "set_app_control",
    "set_app_callbacks",
    "set_render_flush_fn",
    "set_suspend_terminal_fn",
    "use_error_state",
    "_make_imperative_cleanup",
    "forwardRef",
    "useImperativeHandle",
    "memo",
    "useApp",
    "usePrevious",
]
