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

方向3 评估（ClassComponent / ref / forwardRef / 绝对定位，预期不做）：
均无消费方，需引入基础设施（fiber 生命周期 class、ref 传递协议、绝对定位
坐标系）；评估不做（成本高、无消费方、收益低）——useImperativeHandle
评估结论见上文，可追溯。
"""

from __future__ import annotations

import itertools
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
    PasteHook,
    SyncStoreHook,
    Context,
    HookNode,
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

# std 流访问器（session 注入；useStdin/useStdout/useStderr 读取——完善 react ink）
_stdin_accessor: Callable[[], Any] | None = None
_stdout_accessor: Callable[[], Any] | None = None
_stderr_accessor: Callable[[], Any] | None = None


def set_schedule_callback(cb: Callable[[], None] | None) -> None:
    """注入状态更新重渲染回调。"""
    global _schedule_callback
    _schedule_callback = cb


def set_std_accessors(
    stdin_fn: Callable[[], Any] | None,
    stdout_fn: Callable[[], Any] | None,
    stderr_fn: Callable[[], Any] | None,
) -> None:
    """注入 std 流访问器（session 调用；useStdin/useStdout/useStderr 读取）。

    访问器为惰性函数（每帧渲染期调用时取最新流对象——stdin 在
    ``set_input`` 后才注入，stdout 为渲染器流可替换）。
    """
    global _stdin_accessor, _stdout_accessor, _stderr_accessor
    _stdin_accessor = stdin_fn
    _stdout_accessor = stdout_fn
    _stderr_accessor = stderr_fn


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

#: 惰性初始值待解析哨兵（use_state/use_reducer 的 initial 为 callable 时标记；
#: 首个渲染期解析一次，后续渲染忽略——React useState 惰性初始化语义）。
_INIT_PENDING = object()


def _next_state_hook(reducer: Callable[[Any, Any], Any] | None, initial: Any) -> StateHook:
    """获取/创建当前 fiber 的下一个 StateHook 并应用待处理更新。

    React 惰性初始化（方向1）：initial 为 callable 时仅首个渲染调用一次
    （``hook.state is _INIT_PENDING`` 标记），后续渲染复用既有 state——
    修复前 callable initial 被原样存入 state（渲染出 ``<function ...>``）且
    每次渲染重新求值（意外副作用）。
    """
    init_value = _INIT_PENDING if callable(initial) else initial
    hook = _next_hook(StateHook, init_value, None, reducer)
    if reducer is not None:
        hook.reducer = reducer
    if hook.state is _INIT_PENDING:
        hook.state = initial() if callable(initial) else initial
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


def _make_setter(fiber: Fiber, hook: StateHook) -> Callable[[Any], None]:
    """创建 set_state/dispatch 函数（入队 + 触发重渲染）。

    方向3（已卸载组件 setter 修复）：setter 捕获渲染期 fiber——组件已卸载
    （``fiber.deleted=True``）时 set_state 不排队不触发重渲染（修复前 setter
    闭包仅持 hook，无条件 ``_schedule()``，已卸载组件 setter 仍触发重渲染）。
    fiber 复用时 reconciler 会重置 ``deleted=False``（setter 闭包捕获的 fiber
    对象在复用时仍有效，deleted 已复位）；Python 引用计数保证 fiber 对象存活
    （闭包持有），deleted 检查仅读布尔字段无风险。
    """

    def _set(value: Any) -> None:
        if getattr(fiber, "deleted", False):
            return  # 已卸载组件 set_state：不排队不触发重渲染
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
    return (hook.state, _make_setter(_current(), hook))


def use_reducer(
    reducer: Callable[[Any, Any], Any],
    initial: Any,
    init: Callable[[Any], Any] | None = None,
) -> tuple[Any, Callable[[Any], None]]:
    """React useReducer 等价物。

    Args:
        reducer: (state, action) -> new_state。
        initial: 初始状态（init 提供时作为 init 参数传入）。
        init: 惰性初始化函数 ``(initial) -> 初始 state``（React useReducer
            第三参，方向1）；提供时仅首渲染调用一次。

    Returns:
        (state, dispatch) 元组。dispatch 接受 action。
    """
    if init is not None:
        # 惰性初始化：initial 作为参数传入 init；经 _next_state_hook 的
        # callable 惰性路径求值（``lambda: init(initial)`` 仅首渲染调用）。
        hook = _next_state_hook(reducer, (lambda: init(initial)))
    else:
        hook = _next_state_hook(reducer, initial)
    return (hook.state, _make_setter(_current(), hook))


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
    """React useEffect 等价物（提交期执行，passive——layout 之后）。

    Args:
        create: 创建函数（挂载或依赖变化时执行，返回销毁函数）。
        deps: 依赖列表；None 表示每次渲染都执行。

    Returns:
        EffectHook 节点（layout=False）。
    """
    hook = _next_hook(EffectHook, create, deps, None, None)
    hook.create = create
    hook.deps = list(deps) if deps is not None else deps
    hook.layout = False
    return hook


def useLayoutEffect(create: Callable[[], Any] | None, deps: list | tuple | None = None) -> EffectHook:
    """React useLayoutEffect 等价物（布局后同步执行，先于 passive useEffect）。

    方向4（独立时序）：修复前为 useEffect 别名——所有 effects 在调和后统一
    提交，无「绘制前」区分。现引入 layout/passive 两阶段提交：
      - layout effects（useLayoutEffect）：布局阶段后**立即同步**执行——用于
        需要测量/同步副作用且须在绘制前完成的场景（React 语义：DOM 变更后
        同步执行、阻塞绘制）；
      - passive effects（useEffect）：layout 之后执行（当前框架无真实「绘制
        后」异步窗口，passive 与 layout 在同一个 reconciler.render 提交期内
        执行，仅先后不同——layout 先、passive 后，与 React 一致）。

    组件可依赖此提交顺序（如 layout effect 写入共享状态、passive effect 读取
    最新值）。保留命名供 React 生态组件移植（hook 签名一致）。

    Args:
        create: 创建函数（挂载或依赖变化时执行，返回销毁函数）。
        deps: 依赖列表；None 表示每次渲染都执行。

    Returns:
        EffectHook 节点（layout=True）。
    """
    hook = _next_hook(EffectHook, create, deps, None, None)
    hook.create = create
    hook.deps = list(deps) if deps is not None else deps
    hook.layout = True
    return hook


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
    # ★ BUG-44（review 方向）：原始类型 str 按值比较（React Object.is 语义——
    #   原始值按值相等）——修复前仅按 ``is`` 引用比较：deps 含 str 时跨帧同值
    #   不同对象（非 intern 字符串，如 spinner 帧字符 ``⠋``、模型名等）→ 依赖
    #   恒变 → ``use_memo``/``use_effect`` 缓存永久失效。str 不可变，值比较安全。
    if ta is str and tb is str:
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


#: Context 唯一标签序号真源（方向3）：单调递增，替代 ``id(ctx)``——
#:   id 复用风险（context 经 ``_cleanup_contexts`` 从注册表移除后可被 GC，
#:   新 context 可能复用旧 id → 标签碰撞，跨 context 污染）。与 ``_HOOK_SEQ``
#: 方案一致（fiber.py InputHook 序号）。
_CTX_SEQ = itertools.count()

#: useId 分配序号真源（React 18 useId 语义）：单调递增，为每个首次挂载的
#: fiber 分配稳定唯一 ID。fiber 复用期间 ID 保持不变（ID 挂在 fiber 上，
#: 复用不重分配）；不同 fiber 永不冲突（序号单调）。
_USE_ID_SEQ = itertools.count()


def useId() -> str:
    """React 18 ``useId`` 等价物（完善 react ink）：返回稳定唯一 ID 字符串。

    同一组件跨渲染返回相同 ID（ID 挂在 fiber 上，fiber 复用不重分配）；
    不同组件返回不同 ID（全局单调递增序号）。格式 ``:r{seq}:``（React
    风格前缀，防与业务字符串冲突）。

    典型用途：a11y 关联（``aria-labelledby`` / ``<label for>``）、组件间
    稳定标识（表单控件 id、测试定位）。与 React 不同：React 18 的 useId
    带 ``:r`` 前缀、双冒号包裹的哈希风格，本实现为简化序号（语义一致：
    稳定 + 唯一）。

    Returns:
        形如 ``:r0:`` 的唯一 ID 字符串。
    """
    fiber = _current()
    fid = getattr(fiber, "_use_id", None)
    if fid is None:
        fid = f":r{next(_USE_ID_SEQ)}:"
        fiber._use_id = fid
    return fid


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
    ctx.tag = f"__ctx_{next(_CTX_SEQ)}__"
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
    # ★ BUG-16：消费 context 后清除 dirty 标记（Provider 值变化经
    #   ``_clear_context_cache_subtree`` 置位；本函数求值即已消费最新值）。
    fiber._context_dirty = False
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
            事件（跳过旧回调路径）；False/异常放行（走旧路径）。也兼容
            React Ink 生态签名 ``(input, key) -> bool``（handler 接受 2+ 参数
            时自动适配——input 为可打印字符串，key 为按键信息字典）。
        is_active: 是否参与输入路由；False 时 hook 不参与（不消费）。

    Returns:
        None（与 react-ink 一致）。
    """
    hook = _next_hook(InputHook, handler, is_active)
    hook.handler = _make_compat_handler(handler)
    hook.is_active = is_active
    return None


#: use_input 兼容包装缓存（handler→包装；仅普通函数缓存，MagicMock 等动态
#: 对象回退每次解析——inspect.signature 开销可接受）
_compat_handler_cache: dict = {}


def _make_compat_handler(handler: Callable) -> Callable:
    """适配 use_input handler 两种签名：``(event)`` 或 ``(input, key)``。

    React Ink 生态组件（ink-select-input/ink-text-input 等）用
    ``(input, key)`` 签名；本框架内建控件用 ``(event)`` 签名（KeyEvent）。
    按 handler 位置参数数量自动适配（>=2 → ``(input, key)`` 双参调用）；
    单参数 handler 原样返回（零回归，零额外开销）。

    缓存：普通函数对象按 ``id`` 缓存（避免每帧 inspect.signature 开销）；
    MagicMock 等动态对象（无稳定 ``__name__`` 或无法签名）不缓存。

    Args:
        handler: 原始 handler。

    Returns:
        包装后的 handler（单参数 handler 原样返回）。
    """
    # MagicMock 等动态对象：不缓存（getattr 自动创建属性会误判命中）
    if getattr(handler, "__name__", None) is None and not isinstance(handler, type):
        return handler
    hid = id(handler)
    cached = _compat_handler_cache.get(hid)
    if cached is not None and cached[0] is handler:
        return cached[1]
    try:
        import inspect as _inspect
        sig = _inspect.signature(handler)
        n = sum(
            1 for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
    except (TypeError, ValueError):
        n = 1
    if n < 2:
        return handler

    def _wrapped(event) -> bool:
        return bool(handler(_event_input(event), _event_key(event)))

    # 仅缓存普通函数（有 __name__）；避免无限增长：缓存 key 为 id，同 id 复用
    # 时覆盖（handler 存活期间 id 稳定；hook 持有 handler 引用）。
    if len(_compat_handler_cache) < 512:
        _compat_handler_cache[hid] = (handler, _wrapped)
    return _wrapped


def _event_input(event) -> str:
    """React Ink (input, key) 的第一参：可打印字符（按键事件为空串）。"""
    if getattr(event, "kind", None) == "char":
        return getattr(event, "char", "") or ""
    return ""


def _event_key(event) -> dict:
    """React Ink (input, key) 的第二参：按键信息字典（完整字段）。

    React Ink v6 key 字段：leftArrow/rightArrow/upArrow/downArrow/return/
    escape/ctrl/shift/tab/backspace/delete/pageDown/pageUp/home/end/meta/
    super/hyper/capsLock/numLock/eventType。super/hyper/capsLock/numLock 需
    kitty keyboard 协议（本框架未实现——恒 False）；eventType 恒 None。
    """
    kind = getattr(event, "kind", "")
    modifier = getattr(event, "modifier", 0) or 0
    keycode = getattr(event, "keycode", 0) or 0
    return {
        "leftArrow": kind == "arrow_left",
        "rightArrow": kind == "arrow_right",
        "upArrow": kind == "arrow_up",
        "downArrow": kind == "arrow_down",
        "return": kind == "enter",
        "escape": kind == "escape",
        "ctrl": kind == "ctrl_key" or modifier in (5, 6),
        "shift": modifier in (2, 4, 6),
        "tab": kind == "tab",
        "backspace": kind == "backspace",
        "delete": kind == "delete",
        "pageDown": kind == "page_down" or (kind == "csi_u" and keycode in (62,)),
        "pageUp": kind == "page_up" or (kind == "csi_u" and keycode in (63,)),
        "home": kind == "home",
        "end": kind == "end",
        "meta": modifier in (3, 6),
        "super": False,
        "hyper": False,
        "capsLock": False,
        "numLock": False,
        "eventType": None,
    }


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


def _make_imperative_cleanup(ref, hook: MemoHook) -> Callable:
    """构造 useImperativeHandle 的 effect create（返回卸载清理函数）。

    卸载清理仅在 ``ref.current`` 仍指向本组件最近一次句柄时置 None——
    deps 变化后旧 destroy 不得清掉新句柄（React 语义：卸载时置 null，
    更新时不清）。
    """

    def _create():
        value = hook.value

        def _destroy():
            if getattr(ref, "current", None) is value:
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
        eff.create = _make_imperative_cleanup(ref, hook)
        if memo_changed:
            eff.deps = (id(ref), id(hook), id(hook.value))
            eff.last_deps = None
    else:
        eff = _next_hook(EffectHook, None, None, None, None)
        eff.create = None
        eff.deps = None
        eff.last_deps = None


# ═══════════════════════════════════════════════════════════
# useApp / useFocus（方向B 步骤10）
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
    control = _app_control or {}

    def _noop(*args, **kwargs):
        return None

    async def _already_flushed():
        return None

    def _flush():
        if _render_flush_fn is not None:
            try:
                return _render_flush_fn()
            except Exception:
                pass
        return _already_flushed()

    def _suspend(callback=None):
        if _suspend_terminal_fn is not None:
            try:
                return _suspend_terminal_fn(callback)
            except Exception:
                pass
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
        return None

    return {
        "exit": control.get("exit") or _noop,
        "clear": control.get("clear") or _noop,
        "waitUntilRenderFlush": _flush,
        "suspendTerminal": _suspend,
    }


def useFocus(options: "bool | dict | None" = None) -> dict:
    """React useFocus 等价物：注册当前 fiber 的 InputHook 焦点标志。

    焦点仲裁：reconciler 构建 input router 时优先仅取 ``focused`` 且
    ``active`` 的 hook；focused 集合为空时回退全部 active hook
    （无焦点仲裁时行为不变，零回归）。与 ``use_input`` 配套使用
    （**先 ``use_input`` 后 ``useFocus``**——P2-7 契约）。

    P2-7：useFocus 经 ``reversed(fiber.hooks)`` 取最近的 InputHook；若用户
    先 useFocus 后 use_input（反序），找不到 InputHook——显式 raise
    HookStateError（编程错误），而非静默 no-op（静默会导致焦点标志丢失或
    命中更早的 hook，行为不可预期）。

    方向4（完善 react ink）：参数兼容 react-ink 对象风格
    ``useFocus({isActive, autoFocus})``——``isActive`` 控制是否参与路由
    （覆盖 use_input 的 is_active），``autoFocus`` 控制焦点标志
    （True=参与焦点优先路由）；也兼容既有 bool 参数（``useFocus(False)``
    等价 ``useFocus({"autoFocus": False})``）。返回 ``{"isFocused": bool}``
    （react-ink 语义；组件可据此条件渲染焦点样式）。

    方向 E（完善 react ink v6）：支持 ``id`` 参数——可聚焦组件收集到焦点
    管理器（``useFocusManager`` 的 focusNext/focusPrevious/focus(id) 可编程
    切换），``autoFocus=True`` 且无激活时自动获得焦点；``isFocused`` 基于
    焦点管理器的 activeId（有焦点管理时仅激活组件 isFocused=True）。

    Args:
        options: bool（既有 API：是否参与焦点路由）或 dict
            （react-ink 风格：``{"isActive": bool, "autoFocus": bool,
            "id": str|None}``）；None 等价 True（默认参与）。

    Returns:
        ``{"isFocused": bool}`` —— 当前 hook 是否参与焦点优先路由。

    Raises:
        HookStateError: 当前 fiber 无任何已注册 InputHook（必须先调用
            ``use_input``）。
    """
    fiber = _current()
    global _focus_active
    if isinstance(options, dict):
        is_active = options.get("isActive", True)
        auto_focus = options.get("autoFocus", True)
        fid = options.get("id")
    else:
        is_active = True
        auto_focus = True if options is None else bool(options)
        fid = None
    # 焦点管理器注册（React Ink v6）：active 组件进入可聚焦列表。
    if is_active:
        if fid is None:
            fid = _resolve_focus_id(fiber)
        _register_focus_id(fid)
        if auto_focus and _focus_active is None:
            _focus_active = fid
    is_focused = bool(is_active and _focus_enabled and fid == _focus_active)
    for hook in reversed(fiber.hooks):
        if isinstance(hook, InputHook):
            if not is_active:
                hook.is_active = False
            hook.focused = is_focused
            return {"isFocused": is_focused}
    raise HookStateError(
        "useFocus 必须在 use_input 之后调用（当前 fiber 未注册 InputHook）"
    )


# ═══════════════════════════════════════════════════════════
# useMeasure（方向8 完善 react ink）
# ═══════════════════════════════════════════════════════════


def useMeasure() -> dict:
    """React Ink ``useMeasure`` 等价物：测量 host 组件的渲染尺寸。

    返回 ``{"ref": ref, "width": int, "height": int}``——``ref`` 绑定到
    host 元素（``h(BOX, {"ref": m["ref"]})``），布局完成后经 reconciler
    将 ``layout_box`` 写入 ``ref.current``；组件经 layout effect 读取尺寸
    并更新 state 触发重渲染。首次渲染返回 (0, 0)（布局未完成），布局后
    一帧返回实际尺寸（与 React Ink 语义一致——useMeasure 需要额外渲染帧）。

    典型用途：容器尺寸自适应布局、条件渲染（尺寸>0 时显示）、将宿主尺寸
    传递给子组件。

    Returns:
        dict：``{"ref": ref, "width": int, "height": int}``。
    """
    ref = use_ref(None)
    size, set_size = use_state((0, 0))

    def _update():
        box = getattr(ref, "current", None)
        if box is None:
            return
        new_size = (getattr(box, "w", 0), getattr(box, "h", 0))
        if new_size != size:
            set_size(new_size)

    # deps=None：每次渲染执行（layout effect 在 reconciler 填充 ref 后提交，
    # 读取最新尺寸；尺寸变化才 set_state 触发重渲染，零额外帧）。
    useLayoutEffect(_update, None)
    return {"ref": ref, "width": size[0], "height": size[1]}


# ═══════════════════════════════════════════════════════════
# useStdin / useStdout / useStderr（完善 react ink）
# ═══════════════════════════════════════════════════════════


def useStdin() -> dict:
    """React useStdin 等价物：返回 stdin 访问。

    Returns:
        dict：``{"stdin": file|None, "isRawModeSupported": bool,
        "setRawMode": callable, "internal_exitOnCtrlC": bool}``——stdin 为
        session 注入的 Input 实例（惰性读取；未注入时 None）；setRawMode 为
        no-op（当前框架无 raw 模式切换，文档注明差异）；isRawModeSupported
        恒 False（与 setRawMode no-op 一致）；internal_exitOnCtrlC 恒 True。
    """

    def _noop(*args, **kwargs):
        return None

    stdin = _stdin_accessor() if _stdin_accessor is not None else None
    return {
        "stdin": stdin,
        "isRawModeSupported": False,
        "setRawMode": _noop,
        "internal_exitOnCtrlC": True,
    }


def useStdout() -> dict:
    """React useStdout 等价物：返回 stdout 访问。

    Returns:
        dict：``{"stdout": file|None, "write": callable}``——stdout 为 session
        注入的渲染器输出流（惰性读取）；write 为 ``(data: str) -> None``
        （直接写流，经输出锁保护由 session 注入方决定；未注入时 no-op）。
    """

    def _noop(*args, **kwargs):
        return None

    stdout = _stdout_accessor() if _stdout_accessor is not None else None
    write = getattr(stdout, "write", _noop)
    return {"stdout": stdout, "write": write}


def useStderr() -> dict:
    """React useStderr 等价物：返回 stderr 访问。

    Returns:
        dict：``{"stderr": file|None, "write": callable}``——stderr 为 session
        注入的 ``sys.__stderr__``（惰性读取）；write 为 ``(data: str) -> None``。
    """

    def _noop(*args, **kwargs):
        return None

    stderr = _stderr_accessor() if _stderr_accessor is not None else None
    write = getattr(stderr, "write", _noop)
    return {"stderr": stderr, "write": write}


# ═══════════════════════════════════════════════════════════
# useSyncExternalStore（React 18 useSyncExternalStore 等价物）
# ═══════════════════════════════════════════════════════════


def useSyncExternalStore(
    subscribe: Callable[[Callable[[], None]], Any],
    get_snapshot: Callable[[], Any],
    get_server_snapshot: Callable[[], Any] | None = None,
) -> Any:
    """React 18 ``useSyncExternalStore`` 等价物（完善 react ink）。

    让组件订阅外部 store（模型/事件源），store 变化时触发组件重渲染并返回
    最新快照。典型用途：组件直接订阅 AppModel / DisplayEventBus / 外部数据源，
    解耦于 props 逐层传递。

    语义：
      - 首次挂载时调用 ``subscribe(listener)`` 订阅（``listener`` 触发组件
        重渲染）；返回的清理函数保存，组件卸载时调用取消订阅。
      - 每次渲染读取 ``get_snapshot()`` 快照并缓存。
      - ``get_server_snapshot`` 参数接受但忽略（终端渲染无服务端/客户端水合
        概念，React 语义中仅 SSR 使用）。

    与 React 差异（文档注明）：无并发渲染特性（tearing 检测/并发快照）——
    本框架单线程渲染，store 变化经 listener 同步触发重渲染，无 tearing 窗口。

    Args:
        subscribe: ``(listener) -> cleanup_fn | None`` 订阅函数。
        get_snapshot: ``() -> snapshot`` 快照读取函数。
        get_server_snapshot: 服务端快照（忽略，保留签名兼容）。

    Returns:
        当前快照值。
    """
    hook = _next_hook(SyncStoreHook, None)
    hook.subscribe = subscribe
    hook.get_snapshot = get_snapshot
    # ★ BUG-38（review 方向）：subscribe 函数身份变化时**重订阅**——修复前
    #   ``subscribed=True`` 短路：新 subscribe 永不调用、旧订阅永不取消（订阅
    #   函数变化后组件持续监听旧 store）。重订阅语义（React）：先清理旧订阅
    #   再订阅新 store。首次挂载（last_subscribe is None）同样走订阅路径。
    if hook.last_subscribe is not subscribe:
        if hook.cleanup is not None:
            try:
                hook.cleanup()
            except Exception:
                _logger.debug("useSyncExternalStore 旧订阅清理异常", exc_info=True)
            hook.cleanup = None
        hook.last_subscribe = subscribe
        hook.subscribed = True
        try:
            cleanup = subscribe(lambda: _schedule())
            hook.cleanup = cleanup if callable(cleanup) else None
        except Exception:
            _logger.debug("useSyncExternalStore 订阅异常", exc_info=True)
            hook.cleanup = None
    else:
        hook.subscribed = True
    try:
        hook.snapshot = get_snapshot()
    except Exception:
        _logger.debug("useSyncExternalStore 快照读取异常", exc_info=True)
    return hook.snapshot


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


# ═══════════════════════════════════════════════════════════
# usePaste（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════


def usePaste(handler: Callable[[str], bool], options: "dict | None" = None) -> None:
    """React Ink ``usePaste`` 等价物：处理粘贴文本。

    粘贴事件（单次输入多字符）到达时调用 ``handler(text)``；返回 True 消费
    事件（阻断 use_input 通道——React Ink 语义：usePaste 与 useInput 独立
    通道，粘贴内容不转发给 useInput handler）。``options["isActive"]`` 控制
    是否参与粘贴路由（默认 True）。

    与 React Ink 差异：本框架的粘贴检测基于「单次输入事件字符数 > 1」
    （终端粘贴为整段到达，普通打字逐字符）——无需 bracketed paste 协议。

    Args:
        handler: 粘贴处理回调 ``(text: str) -> bool``。
        options: ``{"isActive": bool}``（默认 True）。
    """
    is_active = True
    if isinstance(options, dict):
        is_active = options.get("isActive", True)
    hook = _next_hook(PasteHook, handler, is_active)
    hook.handler = handler
    hook.is_active = is_active
    return None


# ═══════════════════════════════════════════════════════════
# useBoxMetrics（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════


def useBoxMetrics(ref) -> dict:
    """React Ink ``useBoxMetrics`` 等价物：返回跟踪元素（``<Box ref>``）的
    布局度量。

    返回 ``{"width", "height", "left", "top", "hasMeasured"}``——布局完成后
    读取 ref 绑定的 LayoutBox（含相对父容器偏移）；``hasMeasured`` 标记是否
    已完成首次测量。首次渲染返回全 0 + hasMeasured=False，布局后一帧返回
    实际值（与 useMeasure 一致需要额外渲染帧）。

    实现基于 ``useLayoutEffect``：布局阶段后读取 ``ref.current``（LayoutBox），
    尺寸/位置变化时 set_state 触发重渲染。ref 未绑定（None）时返回 0。

    Args:
        ref: 指向 ``<Box>`` 的 ref 对象（``use_ref(None)``）。

    Returns:
        dict：``{"width", "height", "left", "top", "hasMeasured"}``。
    """
    size, set_size = use_state((0, 0, 0, 0, False))

    def _update():
        box = getattr(ref, "current", None)
        if box is None:
            new = (0, 0, 0, 0, False)
        else:
            new = (
                getattr(box, "w", 0),
                getattr(box, "h", 0),
                getattr(box, "x", 0),
                getattr(box, "y", 0),
                True,
            )
        if new != size:
            set_size(new)

    useLayoutEffect(_update, None)
    width, height, left, top, has_measured = size
    return {
        "width": width,
        "height": height,
        "left": left,
        "top": top,
        "hasMeasured": has_measured,
    }


# ═══════════════════════════════════════════════════════════
# useWindowSize（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════

#: 当前终端尺寸（columns, rows）——session 注入 accessor 后每帧读取最新值。
_window_size: tuple[int, int] = (80, 24)
#: 窗口尺寸版本号——resize 时递增；useWindowSize 订阅变化触发重渲染。
_window_size_version: int = 0
#: useWindowSize 订阅监听器集合（resize 通知）。
_window_size_listeners: set = set()
#: 窗口尺寸 accessor（session 注入）。
_window_size_accessor: Callable[[], tuple[int, int]] | None = None


def set_window_size_accessor(fn: Callable[[], tuple[int, int]] | None) -> None:
    """注入窗口尺寸访问器（session 调用：``lambda: (columns, rows)``）。"""
    global _window_size_accessor
    _window_size_accessor = fn


def _refresh_window_size() -> None:
    """渲染期刷新窗口尺寸（useWindowSize 调用前）。"""
    global _window_size
    if _window_size_accessor is not None:
        try:
            _window_size = _window_size_accessor()
        except Exception:
            pass


def _subscribe_window_size(listener: Callable[[], None]) -> Callable[[], None]:
    """订阅窗口尺寸变化（useSyncExternalStore subscribe）。"""
    _window_size_listeners.add(listener)
    return lambda: _window_size_listeners.discard(listener)


def _notify_window_size() -> None:
    """通知窗口尺寸变化（session resize 时调用）：触发全部订阅重渲染。"""
    global _window_size_version
    _window_size_version += 1
    for fn in list(_window_size_listeners):
        try:
            fn()
        except Exception:
            pass


def useWindowSize() -> dict:
    """React Ink ``useWindowSize`` 等价物：返回 ``{"columns", "rows"}``。

    终端尺寸变化时自动重渲染（订阅 window size store）。尺寸来源为 session
    注入的 accessor（未注入时返回 (80, 24) 默认值）。

    Returns:
        dict：``{"columns": int, "rows": int}``。
    """
    useSyncExternalStore(_subscribe_window_size, lambda: _window_size_version)
    _refresh_window_size()
    columns, rows = _window_size
    return {"columns": columns, "rows": rows}


# ═══════════════════════════════════════════════════════════
# 焦点管理（React Ink v6 useFocus / useFocusManager）
# ═══════════════════════════════════════════════════════════

#: 可聚焦 id 收集列表（渲染期收集，按渲染顺序——reconciler 每帧渲染前重置）。
_focus_ids: list[str] = []
#: 当前激活的焦点 id（None=无激活）。
_focus_active: str | None = None
#: 全局焦点管理开关（React Ink 默认启用；disableFocus 关闭）。
_focus_enabled: bool = True
#: 自动焦点 id 分配序号（未显式指定 id 的 useFocus）。
_focus_id_seq = itertools.count()


def _reset_focus_ids() -> None:
    """每帧渲染前重置可聚焦 id 收集列表（reconciler.render 调用）。"""
    _focus_ids.clear()


def _register_focus_id(fid: str) -> None:
    """渲染期注册可聚焦 id（useFocus 调用）。"""
    if fid not in _focus_ids:
        _focus_ids.append(fid)


def _resolve_focus_id(fiber: Fiber) -> str:
    """为未指定 id 的 useFocus 分配稳定自动 id（挂在 fiber 上，复用不重分配）。"""
    fid = getattr(fiber, "_focus_id", None)
    if fid is None:
        fid = f"__focus_{next(_focus_id_seq)}__"
        fiber._focus_id = fid
    return fid


def _focus_next() -> None:
    """切换到下一个可聚焦组件（Tab）。React Ink useFocusManager.focusNext。"""
    global _focus_active
    if not _focus_ids:
        return
    if _focus_active is None or _focus_active not in _focus_ids:
        _focus_active = _focus_ids[0]
    else:
        idx = _focus_ids.index(_focus_active)
        _focus_active = _focus_ids[(idx + 1) % len(_focus_ids)]
    _schedule()


def _focus_previous() -> None:
    """切换到上一个可聚焦组件（Shift+Tab）。React Ink useFocusManager.focusPrevious。"""
    global _focus_active
    if not _focus_ids:
        return
    if _focus_active is None or _focus_active not in _focus_ids:
        _focus_active = _focus_ids[-1]
    else:
        idx = _focus_ids.index(_focus_active)
        _focus_active = _focus_ids[(idx - 1) % len(_focus_ids)]
    _schedule()


def _focus_to(fid: str) -> None:
    """切换到指定 id 的组件。React Ink useFocusManager.focus(id)。"""
    global _focus_active
    if fid in _focus_ids:
        _focus_active = fid
        _schedule()


def _focus_enable() -> None:
    """启用全局焦点管理（默认启用）。React Ink useFocusManager.enableFocus。"""
    global _focus_enabled
    if not _focus_enabled:
        _focus_enabled = True
        _schedule()


def _focus_disable() -> None:
    """禁用全局焦点管理；当前激活组件失去焦点。React Ink useFocusManager.disableFocus。"""
    global _focus_enabled, _focus_active
    _focus_enabled = False
    if _focus_active is not None:
        _focus_active = None
        _schedule()


def useFocusManager() -> dict:
    """React Ink ``useFocusManager`` 等价物：返回焦点管理方法。

    Returns:
        dict：``{"enableFocus", "disableFocus", "focusNext", "focusPrevious",
        "focus", "activeId"}``——focusNext/focusPrevious 循环切换
        （Tab/Shift+Tab 由 reconciler 自动路由），focus(id) 聚焦指定组件，
        activeId 为当前聚焦组件的 id（None=无聚焦）。
    """
    return {
        "enableFocus": _focus_enable,
        "disableFocus": _focus_disable,
        "focusNext": _focus_next,
        "focusPrevious": _focus_previous,
        "focus": _focus_to,
        "activeId": _focus_active,
    }


# ═══════════════════════════════════════════════════════════
# useCursor（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════

#: 光标定位回调（session 注入：``(position|None) -> None``）。
_cursor_position_fn: Callable[[Any], None] | None = None


def set_cursor_position_fn(fn: Callable[[Any], None] | None) -> None:
    """注入光标定位回调（session 调用——IME 光标定位）。"""
    global _cursor_position_fn
    _cursor_position_fn = fn


def useCursor() -> dict:
    """React Ink ``useCursor`` 等价物：返回终端光标定位方法。

    ``setCursorPosition({x, y})`` 设置光标位置（相对 Ink 输出顶部/左侧）；
    传 ``None`` 隐藏光标（IME 组合输入场景）。未注入回调时 no-op。

    Returns:
        dict：``{"setCursorPosition": callable}``。
    """

    def _set_cursor_position(position) -> None:
        if _cursor_position_fn is not None:
            try:
                _cursor_position_fn(position)
            except Exception:
                pass

    return {"setCursorPosition": _set_cursor_position}


# ═══════════════════════════════════════════════════════════
# useIsScreenReaderEnabled（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════


def useIsScreenReaderEnabled() -> bool:
    """React Ink ``useIsScreenReaderEnabled`` 等价物：是否启用了屏幕阅读器。

    本框架未接入屏幕阅读器协议，恒返回 False（终端普通模式）。供渲染不同
    输出的条件判断（如屏幕阅读器下输出纯文本而非 ANSI 装饰）。

    Returns:
        bool：恒 False。
    """
    return False


# ═══════════════════════════════════════════════════════════
# useAnimation（React Ink v6 等价物，简化版）
# ═══════════════════════════════════════════════════════════


def useAnimation(options: "dict | None" = None) -> dict:
    """React Ink ``useAnimation`` 等价物（简化版）：返回动画帧信息。

    ``{"frame": int, "timestamp": float}``——frame 为当前动画帧索引
    （``fps * duration`` 内循环或无限循环），timestamp 为当前单调时钟秒。
    基于时间推导（无独立动画驱动线程）：组件依赖返回的 frame 触发重渲染时
    即可获得连续动画效果（配合 session 的动画刷新帧）。

    与 React Ink 差异：React Ink 的 useAnimation 内建动画驱动（帧率精确
    控制 + duration 循环）；本实现基于单调时钟推导帧号，依赖宿主渲染节奏
    （无独立驱动），帧率不精确控制。

    Args:
        options: ``{"fps": int, "duration": float}``——fps 默认 24；
            duration 秒数（>0 时在该周期内循环；0/缺省无限循环）。

    Returns:
        dict：``{"frame": int, "timestamp": float}``。
    """
    import time as _time
    fps = 24
    duration = 0.0
    if isinstance(options, dict):
        try:
            fps = max(1, int(options.get("fps", 24)))
        except (TypeError, ValueError, OverflowError):
            fps = 24
        try:
            duration = max(0.0, float(options.get("duration", 0)))
        except (TypeError, ValueError, OverflowError):
            duration = 0.0
    now = _time.monotonic()
    if duration > 0:
        total_frames = max(1, int(round(duration * fps)))
        frame = int(now * fps) % total_frames
    else:
        frame = int(now * fps)
    return {"frame": frame, "timestamp": now}


# ═══════════════════════════════════════════════════════════
# useApp 扩展（React Ink v6：waitUntilRenderFlush / suspendTerminal）
# ═══════════════════════════════════════════════════════════

#: 渲染 flush 等待回调（session 注入：``() -> None`` 或 ``() -> awaitable``）。
_render_flush_fn: Callable[[], Any] | None = None
#: 终端挂起回调（session 注入：``(callback|None) -> Any``）。
_suspend_terminal_fn: Callable[[Any], Any] | None = None


def set_render_flush_fn(fn: Callable[[], Any] | None) -> None:
    """注入渲染 flush 等待回调（session 调用）。"""
    global _render_flush_fn
    _render_flush_fn = fn


def set_suspend_terminal_fn(fn: Callable[[Any], Any] | None) -> None:
    """注入终端挂起回调（session 调用——editor/子进程流程）。"""
    global _suspend_terminal_fn
    _suspend_terminal_fn = fn


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
    "useId",
    "use_input",
    "use_error_state",
    "memo",
    "forwardRef",
    "useImperativeHandle",
    "useMeasure",
    "usePrevious",
    "useApp",
    "useFocus",
    "useStdin",
    "useStdout",
    "useStderr",
    "useSyncExternalStore",
    "usePaste",
    "useBoxMetrics",
    "useWindowSize",
    "useFocusManager",
    "useCursor",
    "useIsScreenReaderEnabled",
    "useAnimation",
    "set_schedule_callback",
    "set_input_router_callback",
    "set_app_control",
    "set_app_callbacks",
    "set_std_accessors",
    "set_window_size_accessor",
    "set_cursor_position_fn",
    "set_render_flush_fn",
    "set_suspend_terminal_fn",
    "deps_changed",
    "mark_effect_committed",
    "_deps_equal",
    "_reset_focus_ids",
    "_register_focus_id",
    "_resolve_focus_id",
    "_focus_next",
    "_focus_previous",
    "_focus_to",
    "_focus_enable",
    "_focus_disable",
    "_focus_enabled",
    "_focus_ids",
    "_focus_active",
    "_notify_window_size",
    "HookStateError",
]
