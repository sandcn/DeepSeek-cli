"""hooks 核心 — hook 基础设施 + 基础 hooks（state/ref/effect/memo/context/id）。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——hook 调用期
绑定基础设施（``_current_fiber_stack``/``_next_hook`` 模板方法）与基础 hooks
族（use_state/use_reducer/use_ref/use_effect/useLayoutEffect/use_memo/
use_callback/useId/create_context/use_context）保持在本模块（核心，被全部
hooks 家族依赖）；输入/组件/焦点/环境 hooks 分别独立（``_hooks_input`` /
``_hooks_component`` / ``_hooks_focus`` / ``_hooks_env``），``hooks.py``
门面 re-export 全部符号（旧导入路径 ``from src.tui.ink.hooks import ...``
保持不变，测试/外部调用面兼容）。

调用期绑定：渲染函数组件期间（reconciler.begin_work），``use_*`` 读取
当前 fiber 栈顶（``_current_fiber_stack``）。每个 function fiber 在每次
渲染前 ``reset_hooks()`` 清零 hook_index，``use_*`` 按下标复用上次的
hook 节点（保留状态/引用），从而跨渲染保持状态。

``use_effect`` 的 create 函数返回销毁函数；effect 的提交（先销毁后创建）
由 reconciler 在整棵调和完成后执行。

方向3 评估（ClassComponent / ref / forwardRef / 绝对定位，预期不做）：
均无消费方，需引入基础设施（fiber 生命周期 class、ref 传递协议、绝对定位
坐标系）；评估不做（成本高、无消费方、收益低）——useImperativeHandle
评估结论见上文，可追溯。
"""

from __future__ import annotations

import itertools
import logging
import math
from typing import Any, Callable

from .fiber import (
    Fiber,
    StateHook,
    RefHook,
    EffectHook,
    MemoHook,
    Context,
    HookNode,
)

# ★ 模块级可变状态唯一真源在 hooks.py 门面（外部/测试直接读写门面属性——
#   拆分后须保证 ``hooks._current_fiber_stack`` 等最新值一致）。本模块加载期
#   获取部分初始化模块引用，运行期经 ``_hooks_module._xxx`` 属性访问（Python
#   部分初始化模块：加载期不访问属性，运行期已完整——循环 import 安全）。
from src.tui.ink import hooks as _hooks_module

# ★ logger 名保持 ``src.tui.ink.hooks``（模块拆分后日志命名不变——
#   外部 caplog/日志过滤按旧名监听，如 test_schedule_callback_exception_logged）。
_logger = logging.getLogger("src.tui.ink.hooks")


class HookStateError(RuntimeError):
    """hook 状态机异常（编程错误：渲染期外调用 / hook 类型不一致）。

    不参与 ErrorBoundary 捕获——hook 顺序/类型错误视为编程错误，须向
    调用方传播（reconciler 捕获函数组件渲染异常时对 HookStateError 直接
    re-raise，不执行边界降级）。
    """


def set_schedule_callback(cb: Callable[[], None] | None) -> None:
    """注入状态更新重渲染回调。"""
    _hooks_module._schedule_callback = cb


def set_std_accessors(
    stdin_fn: Callable[[], Any] | None,
    stdout_fn: Callable[[], Any] | None,
    stderr_fn: Callable[[], Any] | None,
) -> None:
    """注入 std 流访问器（session 调用；useStdin/useStdout/useStderr 读取）。

    访问器为惰性函数（每帧渲染期调用时取最新流对象——stdin 在
    ``set_input`` 后才注入，stdout 为渲染器流可替换）。
    """
    _hooks_module._stdin_accessor = stdin_fn
    _hooks_module._stdout_accessor = stdout_fn
    _hooks_module._stderr_accessor = stderr_fn


def _push_current(fiber: Fiber) -> None:
    """渲染函数组件前压入当前 fiber（供 reconciler 调用）。"""
    _hooks_module._current_fiber_stack.append(fiber)


def _pop_current() -> None:
    """渲染函数组件结束后弹出当前 fiber（供 reconciler 调用）。"""
    if _hooks_module._current_fiber_stack:
        _hooks_module._current_fiber_stack.pop()


def _current() -> Fiber:
    """读取当前 fiber。"""
    if not _hooks_module._current_fiber_stack:
        raise HookStateError("use_* hook 只能在函数组件渲染期间调用")
    return _hooks_module._current_fiber_stack[-1]


def _schedule() -> None:
    """请求重渲染。"""
    if _hooks_module._schedule_callback is not None:
        try:
            _hooks_module._schedule_callback()
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


def _clear_fiber_state_queues(fiber: Fiber) -> None:
    """清空 fiber 全部 StateHook 的待处理更新队列（fiber 复用时调用）。

    ★ P3 修复（review 方向）：fiber 复用（reconciler 置 ``deleted=False``
    处）时清空 ``hook.queue``——防止陈旧 state queue 被复用渲染应用。残留
    场景：组件本帧 set_state 排队后被删除（``_mark_deleted``）→ queue 未应用
    残留；之后同 key/type 复用该 fiber → ``_next_state_hook`` 把删除前的
    陈旧更新应用到复用后的新渲染（状态回滚到删除前，渲染错误）。
    """
    for hook in fiber.hooks:
        if isinstance(hook, StateHook) and hook.queue is not None:
            hook.queue = None


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
    # ★ P3-3（review 方向）：注册表为 WeakValueDictionary（hooks.py 门面
    #   定义）——Context 不再被外部引用时 GC 自动移除条目（create_context
    #   无显式清理路径；修复前普通 dict 只增不回收）。Context 被外部持有
    #   （模块级/组件级 ctx 变量）时条目存活——与 BUG-18「注册表条目与
    #   Provider 挂载解耦（进程生命周期）」设计一致，挂载/卸载不清理。
    _hooks_module._context_registry[ctx.tag] = ctx
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
    if fiber._context_cache_version == _hooks_module._context_version and ctx.tag in cache:
        return cache[ctx.tag]
    value = ctx.default
    f = fiber.return_
    while f is not None:
        if f.contexts and ctx.tag in f.contexts:
            value = f.contexts[ctx.tag]
            break
        f = f.return_
    cache[ctx.tag] = value
    fiber._context_cache_version = _hooks_module._context_version
    return value


def _bump_context_version() -> None:
    """provider 值变化 → 递增 context 缓存版本号（失效全部逐 fiber 缓存）。

    由 reconciler 在 provider 值变更检测时调用（与子树清缓存配合：
    子树精确清空 + 版本号防御失效；误失效只导致多一次查找，无正确性风险）。
    """
    _hooks_module._context_version += 1


__all__ = [
    "HookStateError",
    "set_schedule_callback",
    "set_std_accessors",
    "_push_current",
    "_pop_current",
    "_current",
    "_schedule",
    "_next_hook",
    "_next_state_hook",
    "_make_setter",
    "_clear_fiber_state_queues",
    "_INIT_PENDING",
    "use_state",
    "use_reducer",
    "use_ref",
    "use_effect",
    "useLayoutEffect",
    "_object_is",
    "_deps_equal",
    "deps_changed",
    "mark_effect_committed",
    "_memo_deps_changed",
    "use_memo",
    "use_callback",
    "_CTX_SEQ",
    "_USE_ID_SEQ",
    "useId",
    "create_context",
    "use_context",
    "_bump_context_version",
]
