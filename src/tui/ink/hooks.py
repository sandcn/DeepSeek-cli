"""hooks — use_state / use_effect / use_ref / use_reducer。

调用期绑定：渲染函数组件期间（reconciler.begin_work），``use_*`` 读取
当前 fiber 栈顶（``_current_fiber_stack``）。每个 function fiber 在每次
渲染前 ``reset_hooks()`` 清零 hook_index，``use_*`` 按下标复用上次的
hook 节点（保留状态/引用），从而跨渲染保持状态。

``use_effect`` 的 create 函数返回销毁函数；effect 的提交（先销毁后创建）
由 reconciler 在整棵调和完成后执行。
"""

from __future__ import annotations

from typing import Any, Callable, List

from .fiber import (
    Fiber,
    StateHook,
    RefHook,
    EffectHook,
)

# 渲染期当前 fiber 栈（渲染线程单线程，模块级栈即可）
_current_fiber_stack: List[Fiber] = []

# 状态更新后触发重渲染的回调（session 注入）
_schedule_callback: Callable[[], None] | None = None

# useApp control（exit 等退出操作，assembly 注入）
_app_control: dict | None = None


def set_app_control(control: dict | None) -> None:
    """注入 useApp control（含 exit 等操作）。"""
    global _app_control
    _app_control = control


def get_app_control() -> dict | None:
    """读取 useApp control。"""
    return _app_control


def set_schedule_callback(cb: Callable[[], None] | None) -> None:
    """注入状态更新重渲染回调。"""
    global _schedule_callback
    _schedule_callback = cb


def _push_current(fiber: Fiber) -> None:
    """渲染函数组件前压入当前 fiber（供 reconciler 调用）。"""
    _current_fiber_stack.append(fiber)


def _pop_current() -> None:
    """渲染函数组件结束后弹出当前 fiber（供 reconciler 调用）。"""
    if _current_fiber_stack:
        _current_fiber_stack.pop()


def _current() -> Fiber:
    """读取当前 fiber。"""
    if not _current_fiber_stack:
        raise RuntimeError("use_* hook 只能在函数组件渲染期间调用")
    return _current_fiber_stack[-1]


def _schedule() -> None:
    """请求重渲染。"""
    if _schedule_callback is not None:
        try:
            _schedule_callback()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# use_state / use_reducer
# ═══════════════════════════════════════════════════════════


def _next_state_hook(reducer: Callable[[Any, Any], Any] | None, initial: Any) -> StateHook:
    """获取/创建当前 fiber 的下一个 StateHook 并应用待处理更新。"""
    fiber = _current()
    idx = fiber.hook_index
    fiber.hook_index += 1
    if idx < len(fiber.hooks):
        hook = fiber.hooks[idx]
        assert isinstance(hook, StateHook), f"hook 类型不一致: {type(hook)}"
        if reducer is not None:
            hook.reducer = reducer
    else:
        hook = StateHook(initial, None, reducer)
        fiber.hooks.append(hook)
    if hook.queue:
        if hook.reducer is not None:
            # use_reducer：queue 中为 action，经 reducer 归约
            for action in hook.queue:
                hook.state = hook.reducer(hook.state, action)
        else:
            # use_state：queue 中为新值或更新函数
            for upd in hook.queue:
                if callable(upd):
                    hook.state = upd(hook.state)
                else:
                    hook.state = upd
        hook.queue = None
    return hook


def _make_setter(hook: StateHook) -> Callable[[Any], None]:
    """创建 set_state/dispatch 函数（入队 + 触发重渲染）。"""

    def _set(value: Any) -> None:
        if hook.queue is None:
            hook.queue = []
        hook.queue.append(value)
        _schedule()

    return _set


def use_state(initial: Any) -> tuple[Any, Callable[[Any], None]]:
    """React useState 等价物。

    Args:
        initial: 初始状态值（仅首渲染使用）。

    Returns:
        (state, set_state) 元组。set_state 接受新值或更新函数。
    """
    hook = _next_state_hook(None, initial)
    return (hook.state, _make_setter(hook))


def use_reducer(reducer: Callable[[Any, Any], Any], initial: Any) -> tuple[Any, Callable[[Any], None]]:
    """React useReducer 等价物。

    Args:
        reducer: (state, action) -> new_state。
        initial: 初始状态。

    Returns:
        (state, dispatch) 元组。dispatch 接受 action。
    """
    hook = _next_state_hook(reducer, initial)
    return (hook.state, _make_setter(hook))


# ═══════════════════════════════════════════════════════════
# use_ref
# ═══════════════════════════════════════════════════════════


def use_ref(initial: Any = None) -> RefHook:
    """React useRef 等价物。返回带 ``.current`` 的可变引用对象。"""
    fiber = _current()
    idx = fiber.hook_index
    fiber.hook_index += 1
    if idx < len(fiber.hooks):
        hook = fiber.hooks[idx]
        assert isinstance(hook, RefHook), f"hook 类型不一致: {type(hook)}"
    else:
        hook = RefHook(initial)
        fiber.hooks.append(hook)
    return hook


# ═══════════════════════════════════════════════════════════
# use_effect
# ═══════════════════════════════════════════════════════════


def use_effect(create: Callable[[], Any] | None, deps: list | tuple | None = None) -> EffectHook:
    """React useEffect 等价物（提交期执行）。

    Args:
        create: 创建函数（挂载或依赖变化时执行，返回销毁函数）。
        deps: 依赖列表；None 表示每次渲染都执行。

    Returns:
        EffectHook 节点。
    """
    fiber = _current()
    idx = fiber.hook_index
    fiber.hook_index += 1
    if idx < len(fiber.hooks):
        hook = fiber.hooks[idx]
        assert isinstance(hook, EffectHook), f"hook 类型不一致: {type(hook)}"
    else:
        hook = EffectHook(create, deps, None, None)
        fiber.hooks.append(hook)
    hook.create = create
    hook.deps = list(deps) if deps is not None else deps
    return hook


# ═══════════════════════════════════════════════════════════
# effect 依赖变化判定（reconciler 使用）
# ═══════════════════════════════════════════════════════════


def deps_changed(hook: EffectHook) -> bool:
    """检测 effect 依赖是否变化（首次挂载视为变化）。"""
    if hook.deps is None:
        return True
    if hook.last_deps is None:
        return True
    return hook.last_deps != hook.deps


def mark_effect_committed(hook: EffectHook) -> None:
    """提交后记录 last_deps。"""
    hook.last_deps = list(hook.deps) if hook.deps is not None else None


__all__ = [
    "use_state",
    "use_reducer",
    "use_ref",
    "use_effect",
    "set_schedule_callback",
    "deps_changed",
    "mark_effect_committed",
    "set_app_control",
    "get_app_control",
]
