"""hooks — use_state / use_effect / use_ref / use_reducer。

调用期绑定：渲染函数组件期间（reconciler.begin_work），``use_*`` 读取
当前 fiber 栈顶（``_current_fiber_stack``）。每个 function fiber 在每次
渲染前 ``reset_hooks()`` 清零 hook_index，``use_*`` 按下标复用上次的
hook 节点（保留状态/引用），从而跨渲染保持状态。

``use_effect`` 的 create 函数返回销毁函数；effect 的提交（先销毁后创建）
由 reconciler 在整棵调和完成后执行。

useImperativeHandle 评估（方向② 步骤5）：需引入 forwardRef/ref 转发
基础设施（fiber 增加 ref 挂载点、组件间 ref 传递协议），当前框架无消费
方，成本高——**不做**（评估结论保留可追溯）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, List

from .fiber import (
    Fiber,
    StateHook,
    RefHook,
    EffectHook,
    MemoHook,
    InputHook,
    Context,
)

_logger = logging.getLogger(__name__)


class HookStateError(RuntimeError):
    """hook 状态机异常（编程错误：渲染期外调用 / hook 类型不一致）。

    不参与 ErrorBoundary 捕获——hook 顺序/类型错误视为编程错误，须向
    调用方传播（reconciler 捕获函数组件渲染异常时对 HookStateError 直接
    re-raise，不执行边界降级）。
    """


# 渲染期当前 fiber 栈（渲染线程单线程，模块级栈即可）
_current_fiber_stack: List[Fiber] = []

# 状态更新后触发重渲染的回调（session 注入）
_schedule_callback: Callable[[], None] | None = None

# context 注册表（create_context → reconciler provider host 消费）
_context_registry: dict[str, Context] = {}

# input router 注入回调（session 注入；reconciler 每帧发布 composite router）
_input_router_callback: Callable[[Any], None] | None = None

# app control（session 注入：{"exit": fn, "clear": fn}；useApp 读取）
_app_control: dict | None = None

# context 缓存版本号（方向B 步骤11）：provider 值变化时递增；
# use_context 命中校验（与 contexts 内容解耦，避免依赖每帧重置的 contexts）。
_context_version: int = 0


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
        raise HookStateError("use_* hook 只能在函数组件渲染期间调用")
    return _current_fiber_stack[-1]


def _schedule() -> None:
    """请求重渲染。"""
    if _schedule_callback is not None:
        try:
            _schedule_callback()
        except Exception:
            _logger.debug("schedule 回调异常", exc_info=True)


def _next_hook(hook_cls: type, *init_args) -> HookNode:
    """获取/创建当前 fiber 的下一个 hook 节点（公共骨架，方向② 步骤5）。

    模板方法：从 fiber.hook_index 取下标、递增，按下标复用同类型 hook
    （保留状态）或创建新 hook（``hook_cls(*init_args)``）。各 hook 特有
    的初始化/更新逻辑在返回后由调用方补充（``_next_state_hook`` 应用
    queue、``use_effect``/``use_memo``/``use_input`` 更新字段）。

    Args:
        hook_cls: 期望的 hook 类型（StateHook/RefHook/EffectHook/...）。
        *init_args: 创建新 hook 时的构造参数。

    Returns:
        HookNode：复用或新建的 hook 节点。

    Raises:
        HookStateError: 下标处已有 hook 但类型不一致（hook 顺序变化，编程错误）。
    """
    fiber = _current()
    idx = fiber.hook_index
    fiber.hook_index += 1
    if idx < len(fiber.hooks):
        hook = fiber.hooks[idx]
        if not isinstance(hook, hook_cls):
            raise HookStateError(f"hook 类型不一致: {type(hook)}")
    else:
        hook = hook_cls(*init_args)
        fiber.hooks.append(hook)
    return hook


# ═══════════════════════════════════════════════════════════
# use_state / use_reducer
# ═══════════════════════════════════════════════════════════


def _next_state_hook(reducer: Callable[[Any, Any], Any] | None, initial: Any) -> StateHook:
    """获取/创建当前 fiber 的下一个 StateHook 并应用待处理更新。"""
    hook = _next_hook(StateHook, initial, None, reducer)
    if reducer is not None:
        hook.reducer = reducer
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
    hook = _next_hook(RefHook, initial)
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
    hook = _next_hook(EffectHook, create, deps, None, None)
    hook.create = create
    hook.deps = list(deps) if deps is not None else deps
    return hook


def useLayoutEffect(create: Callable[[], Any] | None, deps: list | tuple | None = None) -> EffectHook:
    """React useLayoutEffect 等价物（方向② 步骤5，当前为 useEffect 别名）。

    当前架构无「绘制前」阶段（非全屏流动模型：单帧渲染 + 调和后统一提交
    effects），useLayoutEffect 与 useEffect 均在调和后提交期执行，行为
    等价。保留命名供 React 生态组件移植（hook 签名一致）。

    方向2 L5 评估结论（可追溯）：**当前与 useEffect 等价（无绘制前阶段）**；
    独立 hook 类型（区分 use_layout_effect）评估——需引入「绘制前提交
    期」基础设施（effects 分批：layout effects 在布局后立即执行、passive
    effects 在帧后执行），当前无消费方需要该时序差异，**收益低、不实施**
    （保持单一 EffectHook 类型，避免 hook 类型不一致编程错误面扩大）。

    Args:
        create: 创建函数（挂载或依赖变化时执行，返回销毁函数）。
        deps: 依赖列表；None 表示每次渲染都执行。

    Returns:
        EffectHook 节点。
    """
    return use_effect(create, deps)


# ═══════════════════════════════════════════════════════════
# effect 依赖变化判定（reconciler 使用）
# ═══════════════════════════════════════════════════════════


def _object_is(a, b) -> bool:
    """React Object.is 语义比较（方向1 步骤1 提取，use_effect/use_memo 单一真源）。

    规则：
      - ``a is b`` → True（同一对象/引用；小整数等 is 缓存命中）；
      - 同为 int/float 且 type 相同 → NaN 与 NaN 相等、+0 与 -0 不等
        （用 ``math.copysign(1, a)`` 区分）、其余按 ``==``；
      - bool 与 int / int 与 float 等 type 不同返回 False。
    """
    if a is b:
        return True
    ta, tb = type(a), type(b)
    if ta is int and tb is int:
        return a == b
    if ta is float and tb is float:
        if a != a and b != b:          # NaN 与 NaN 相等
            return True
        if a == 0 and b == 0:          # +0 与 -0 不等
            return math.copysign(1, a) == math.copysign(1, b)
        return a == b
    return False


def _deps_equal(a, b) -> bool:
    """浅比较依赖列表是否相等（use_effect / use_memo 共用，INK-4 一致性）。

    None 与任何列表不等（None 表示每次渲染重算）；列表按长度相等 + 逐项
    ``_object_is``（React Object.is 语义：数值按 Object.is 规则、其余按
    is 引用比较——引用类型字段须自行保证稳定）。
    """
    if a is None or b is None:
        return a is b
    if len(a) != len(b):
        return False
    return all(_object_is(x, y) for x, y in zip(a, b))


def deps_changed(hook: EffectHook) -> bool:
    """检测 effect 依赖是否变化（首次挂载视为变化）。"""
    if hook.deps is None:
        return True
    if hook.last_deps is None:
        return True
    return not _deps_equal(hook.last_deps, hook.deps)


def mark_effect_committed(hook: EffectHook) -> None:
    """提交后记录 last_deps。"""
    hook.last_deps = list(hook.deps) if hook.deps is not None else None


# ═══════════════════════════════════════════════════════════
# use_memo / use_callback（INK-2 / INK-4）
# ═══════════════════════════════════════════════════════════


def _memo_deps_changed(hook: MemoHook) -> bool:
    """检测 memo 依赖是否变化（首次计算视为变化）。"""
    if hook.deps is None:
        return True
    if hook.last_deps is None:
        return True
    return not _deps_equal(hook.last_deps, hook.deps)


def use_memo(factory: Callable[[], Any], deps: list | tuple | None = None) -> Any:
    """React useMemo 等价物。

    缓存计算结果跨渲染复用；依赖变化时重新执行 factory。

    Args:
        factory: 计算结果工厂函数。
        deps: 依赖列表；None 表示每次渲染都重新计算（与 useEffect deps=None
            语义对齐）。

    Returns:
        缓存值（deps 未变化时返回上次计算结果）。
    """
    hook = _next_hook(MemoHook, factory, deps, None, None)
    hook.factory = factory
    hook.deps = list(deps) if deps is not None else deps
    if _memo_deps_changed(hook):
        hook.value = factory()
        hook.last_deps = list(hook.deps) if hook.deps is not None else None
    return hook.value


def use_callback(fn: Callable, deps: list | tuple | None = None) -> Callable:
    """React useCallback 等价物。

    返回稳定函数引用；依赖变化时返回新的 fn。

    Args:
        fn: 回调函数。
        deps: 依赖列表。

    Returns:
        fn 本身（deps 未变化时返回同一函数对象）。
    """
    return use_memo(lambda: fn, deps)


# ═══════════════════════════════════════════════════════════
# create_context / use_context（INK-3）
# ═══════════════════════════════════════════════════════════


def create_context(default: Any = None) -> Context:
    """React createContext 等价物。

    返回 Context 对象（含 default 与唯一 tag）。``ctx.Provider`` 为 host
    标签字符串，可直接用于 ``h(ctx.Provider, {"value": v}, ...)``。

    Args:
        default: 默认值（未找到 Provider 时 use_context 返回）。

    Returns:
        Context 对象。
    """
    ctx = Context(default=default, tag="")
    ctx.tag = f"__ctx_{id(ctx)}__"
    ctx.Provider = ctx.tag
    _context_registry[ctx.tag] = ctx
    return ctx


def use_context(ctx: Context) -> Any:
    """React useContext 等价物。

    沿当前 fiber 的 return_ 链向上查找最近的 Provider 提供的值；
    未找到 Provider 时返回 ctx.default。

    性能（方向B 步骤11）：逐 fiber 缓存——同 fiber 多次 use_context 同 ctx
    只 O(depth) 一次；Provider 值变化时 reconciler 清空子树缓存并递增
    ``_context_version``（版本号校验缓存命中，与 contexts 内容解耦）。

    Args:
        ctx: create_context 返回的 Context 对象。

    Returns:
        Provider 提供的 value（或 ctx.default）。
    """
    fiber = _current()
    cache = fiber._context_cache
    if fiber._context_cache_version == _context_version and ctx.tag in cache:
        return cache[ctx.tag]
    value = ctx.default
    f = fiber.return_
    while f is not None:
        if f.contexts and ctx.tag in f.contexts:
            value = f.contexts[ctx.tag]
            break
        f = f.return_
    cache[ctx.tag] = value
    fiber._context_cache_version = _context_version
    return value


def _bump_context_version() -> None:
    """provider 值变化 → 递增 context 缓存版本号（失效全部逐 fiber 缓存）。

    由 reconciler 在 provider 值变更检测时调用（与子树清缓存配合：
    子树精确清空 + 版本号防御失效；误失效只导致多一次查找，无正确性风险）。
    """
    global _context_version
    _context_version += 1


# ═══════════════════════════════════════════════════════════
# use_input（INK-1）
# ═══════════════════════════════════════════════════════════


def set_input_router_callback(cb: Callable[[Any], None] | None) -> None:
    """注入 input router 发布回调（session 注入，消费端接线 InputDispatcher）。"""
    global _input_router_callback
    _input_router_callback = cb


def _publish_input_router(router) -> None:
    """发布 composite input router（reconciler 每帧调用）。"""
    if _input_router_callback is not None:
        try:
            _input_router_callback(router)
        except Exception:
            _logger.debug("input router 发布异常", exc_info=True)


def use_input(handler: Callable[[Any], bool], is_active: bool = True) -> None:
    """React useInput 等价物（与 react-ink useInput(inputHandler, {isActive}) 对齐）。

    Args:
        handler: 按键处理回调，签名 ``(event) -> bool``——返回 True 表示消费
            事件（跳过旧回调路径）；False/异常放行（走旧路径）。
        is_active: 是否参与输入路由；False 时 hook 不参与（不消费）。

    Returns:
        None（与 react-ink 一致）。
    """
    hook = _next_hook(InputHook, handler, is_active)
    hook.handler = handler
    hook.is_active = is_active
    return None


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
# memo / useApp / useFocus（方向B 步骤10）
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
    global _app_control
    _app_control = control


#: 别名（保留 ``set_app_callbacks`` 命名兼容；二者等价）
set_app_callbacks = set_app_control


def useApp() -> dict:
    """React useApp 等价物：返回应用控制函数 ``{"exit": fn, "clear": fn}``。

    - ``exit``：请求退出（session 置 exit_requested + 停止渲染，幂等）。
    - ``clear``：请求全帧清屏重绘（非全屏模型：强制全量重绘，非 DECSTBM
      清屏——文档注明与 react-ink 的差异）。

    未注入控制时返回 no-op（安全兜底，不抛异常）。
    """
    control = _app_control or {}

    def _noop(*args, **kwargs):
        return None

    return {
        "exit": control.get("exit") or _noop,
        "clear": control.get("clear") or _noop,
    }


def useFocus(is_focused: bool = True) -> None:
    """React useFocus 等价物：注册当前 fiber 的 InputHook 焦点标志。

    焦点仲裁：reconciler 构建 input router 时优先仅取 ``focused`` 且
    ``active`` 的 hook；focused 集合为空时回退全部 active hook
    （无焦点仲裁时行为不变，零回归）。与 ``use_input`` 配套使用
    （**先 ``use_input`` 后 ``useFocus``**——P2-7 契约）。

    P2-7：useFocus 经 ``reversed(fiber.hooks)`` 取最近的 InputHook；若用户
    先 useFocus 后 use_input（反序），找不到 InputHook——显式 raise
    HookStateError（编程错误），而非静默 no-op（静默会导致焦点标志丢失或
    命中更早的 hook，行为不可预期）。

    Args:
        is_focused: 是否参与焦点优先路由；False 时该 hook 在存在其他
            focused hook 时不参与路由。

    Raises:
        HookStateError: 当前 fiber 无任何已注册 InputHook（必须先调用
            ``use_input``）。
    """
    fiber = _current()
    for hook in reversed(fiber.hooks):
        if isinstance(hook, InputHook):
            hook.focused = is_focused
            return None
    raise HookStateError(
        "useFocus 必须在 use_input 之后调用（当前 fiber 未注册 InputHook）"
    )


__all__ = [
    "use_state",
    "use_reducer",
    "use_ref",
    "use_effect",
    "useLayoutEffect",
    "use_memo",
    "use_callback",
    "use_context",
    "create_context",
    "use_input",
    "use_error_state",
    "memo",
    "useApp",
    "useFocus",
    "set_schedule_callback",
    "set_input_router_callback",
    "set_app_control",
    "set_app_callbacks",
    "deps_changed",
    "mark_effect_committed",
    "_deps_equal",
    "HookStateError",
]
