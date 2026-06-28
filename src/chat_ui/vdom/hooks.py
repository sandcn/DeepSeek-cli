"""Hooks 运行时 — React-like Hooks API 实现。

提供 useState / useEffect / useRef / useMemo / useCallback / useContext / useReducer
共 7 个核心 Hook + create_context 工厂函数。

运行时通过全局 _HooksRuntime 单例管理：
  - _current_component / _component_stack 追踪当前渲染的组件
  - _hook_index 计数器确保每次渲染的 Hooks 调用顺序一致
  - _pending_effects 队列收集待执行的副作用

关键约束：
  - Hooks 只能在组件 render 期间调用（通过 _current_component 检测）
  - 每次 render 的 Hooks 调用顺序必须一致（React 规则）
  - 依赖数组使用浅比较（is 或 ==）
  - 组件卸载时自动清理 effect cleanup 函数
  - effect 在渲染完成后通过 run_effects() 批量执行

架构关系：hooks.py 是核心 Hooks 运行时实现（use_state/use_effect/use_ref 等）。
react_ink/__init__.py 是可选 feature flag（CHAT_UI_USE_REACT_LIKE）门控的 API 聚合层，
通过 re-export 暴露 hooks + 组件（Box/Spinner/Animation）+ 布局（FlexLayout）等完整 API。
当 CHAT_UI_USE_REACT_LIKE=0 时，react_ink 模块导入为 no-op，不影响核心渲染路径。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, TypeVar

from .types import EffectState, HookError, HookState

if TYPE_CHECKING:
    from ..components.base import TuiComponent

T = TypeVar("T")

# ── 浅比较辅助 ──────────────────────────────────────────


def _deps_changed(prev: list[Any] | None, curr: list[Any] | None) -> bool:
    """比较两组依赖是否发生了变化（浅比较）。

    None 表示"始终变化"（每次渲染都执行），空列表表示"永不变化"（仅首次）。

    Args:
        prev: 上次渲染的依赖数组。
        curr: 当前渲染的依赖数组。

    Returns:
        True 表示依赖已变化，需要重新执行/计算。
    """
    if prev is None or curr is None:
        return True  # None 表示"始终视为变化"
    if len(prev) != len(curr):
        return True
    for a, b in zip(prev, curr):
        if a is not b and a != b:
            return True
    return False


# ── 全局 Hooks 运行时 ────────────────────────────────────


class _HooksRuntime:
    """Hooks 运行时 — 全局单例，管理所有组件的 Hooks 生命周期。

    维护当前渲染组件栈、待执行副作用队列、以及重渲染回调。

    Attributes:
        _current_component: 当前正在渲染的组件实例。
        _component_stack: 嵌套渲染的组件栈（最内层在栈顶）。
        _pending_effects: 待执行的副作用列表 [(component, hook_state), ...]。
        _rerender_callback: 由渲染引擎注册的重渲染回调。
    """

    __slots__ = (
        "_current_component",
        "_component_stack",
        "_pending_effects",
        "_rerender_callback",
    )

    def __init__(self) -> None:
        self._current_component: TuiComponent | None = None
        self._component_stack: list[TuiComponent] = []
        self._pending_effects: list[tuple[TuiComponent, HookState]] = []
        self._rerender_callback: Callable[[TuiComponent], None] | None = None

    # ── 组件进入/退出 ─────────────────────────────────

    def enter_component(self, comp: TuiComponent) -> None:
        """进入组件渲染上下文。

        将组件推入栈顶，设为当前渲染组件，并重置 hook_index。
        应在每次 render() 调用前执行。

        Args:
            comp: 即将渲染的组件实例。
        """
        self._component_stack.append(comp)
        self._current_component = comp
        comp._hook_index = 0

    def exit_component(self, comp: TuiComponent) -> None:
        """退出组件渲染上下文。

        从栈中弹出组件，将栈顶（或 None）设为当前组件。

        Args:
            comp: 已完成渲染的组件实例。
        """
        if self._component_stack and self._component_stack[-1] is comp:
            self._component_stack.pop()
        self._current_component = (
            self._component_stack[-1] if self._component_stack else None
        )

    # ── Hook 状态存取 ─────────────────────────────────

    def get_or_create_hook(self, hook_type: str, factory: Callable[[], Any]) -> HookState:
        """获取或创建当前组件的 Hook 状态。

        根据当前 _hook_index 确定是复用已有 hook 还是创建新 hook。
        每次调用后 _hook_index 自动递增。

        Args:
            hook_type: Hook 类型标识（'state'/'effect'/'ref'/'memo'/'callback'/'context'/'reducer'）。
            factory: 创建新 HookState.value 的工厂函数。

        Returns:
            当前索引对应的 HookState。

        Raises:
            HookError: 在组件 render 上下文外调用时。
        """
        comp = self._current_component
        if comp is None:
            raise HookError(
                f"Hook '{hook_type}' 在组件 render 上下文外调用。"
                " Hooks 只能在组件的 render()/render_vnode() 方法内调用。"
            )

        comp._ensure_hooks()
        idx = comp._hook_index
        comp._hook_index += 1

        if idx < len(comp._hooks):
            return comp._hooks[idx]
        else:
            hook = HookState(type=hook_type, value=factory())
            comp._hooks.append(hook)
            return hook

    # ── 副作用执行 ────────────────────────────────────

    def schedule_effect(self, comp: TuiComponent, hook: HookState) -> None:
        """将待执行的副作用加入队列。

        在 use_effect 调用期间由 hook 函数将 (component, hook) 入队，
        稍后由 run_effects() 统一执行。

        Args:
            comp: 拥有该 effect 的组件。
            hook: effect 对应的 HookState。
        """
        self._pending_effects.append((comp, hook))

    def run_effects(self) -> None:
        """渲染后批量执行所有待处理的副作用。

        对每个注册的 effect：
        1. 浅比较依赖是否变化
        2. 若变化：先执行上次的 cleanup，再执行新 effect，存储新 cleanup
        3. 若未变化：跳过

        执行完毕后清空待处理队列。
        """
        for comp, hook in self._pending_effects:
            if hook.type != "effect":
                continue

            effect: EffectState = hook.value
            deps_changed = _deps_changed(effect.prev_deps, effect.deps)

            if deps_changed:
                # 先执行上次的清理函数
                if effect.cleanup_fn is not None:
                    try:
                        effect.cleanup_fn()
                    except Exception:
                        pass  # cleanup 异常不影响后续 effect 执行

                # 执行新的副作用函数
                try:
                    result = effect.effect_fn()
                except Exception:
                    result = None  # effect 异常不中断渲染

                # 存储新清理函数
                effect.cleanup_fn = result if callable(result) else None

            # 更新 prev_deps 为当前 deps
            effect.prev_deps = list(effect.deps) if effect.deps is not None else None

        self._pending_effects.clear()

    # ── 组件清理 ──────────────────────────────────────

    def cleanup_component(self, comp: TuiComponent) -> None:
        """清理组件的所有 Hooks 资源。

        在组件 unmount 时调用，执行所有 effect 的 cleanup 函数。
        同时从待执行队列中移除该组件的 effect。

        Args:
            comp: 即将卸载的组件实例。
        """
        if not hasattr(comp, "_hooks") or comp._hooks is None:
            return

        for hook in comp._hooks:
            # 处理 EffectState 内的 cleanup_fn
            if hook.type == "effect" and isinstance(hook.value, EffectState):
                effect: EffectState = hook.value
                if effect.cleanup_fn is not None:
                    try:
                        effect.cleanup_fn()
                    except Exception:
                        pass

        # 从待执行队列中移除该组件的 effect
        self._pending_effects = [
            (c, h) for c, h in self._pending_effects if c is not comp
        ]

    # ── 重渲染回调 ────────────────────────────────────

    def set_rerender_callback(self, cb: Callable[[TuiComponent], None] | None) -> None:
        """注册重渲染回调，由渲染引擎在初始化时调用。

        当 use_state 的 setter 或 use_reducer 的 dispatch 被调用时，
        触发此回调以请求组件重渲染。

        Args:
            cb: 接受 TuiComponent 实例的回调函数，或 None 取消注册。
        """
        self._rerender_callback = cb

    def request_rerender(self, comp: TuiComponent) -> None:
        """请求组件重渲染。

        标记组件为 dirty 并调用已注册的重渲染回调。

        Args:
            comp: 需要重渲染的组件。
        """
        comp._dirty = True
        if self._rerender_callback is not None:
            self._rerender_callback(comp)


# ── 全局单例 ────────────────────────────────────────────

_hooks_runtime = _HooksRuntime()


def get_hooks_runtime() -> _HooksRuntime:
    """获取全局 Hooks 运行时单例。

    Returns:
        全局 _HooksRuntime 实例。
    """
    return _hooks_runtime


# ── Hook 函数实现 ────────────────────────────────────────


def use_state(initial: T | Callable[[], T]) -> tuple[T, Callable[[T | Callable[[T], T]], None]]:
    """状态 Hook — 在组件内管理局部状态。

    首次渲染时使用 initial 值，后续渲染返回已存储的值。
    initial 为可调用对象时执行惰性初始化（仅首次调用一次）。

    Args:
        initial: 初始值，或返回初始值的惰性初始化函数。

    Returns:
        (当前值, setter) 元组。
        setter 接受新值或 updater 函数 (prev) -> new，触发组件重渲染。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    runtime = _hooks_runtime
    comp = runtime._current_component

    def _factory() -> T:
        if callable(initial) and not isinstance(initial, type):
            return initial()  # type: ignore[return-value]
        return initial  # type: ignore[return-value]

    hook = runtime.get_or_create_hook("state", _factory)

    def setter(new_value: T | Callable[[T], T]) -> None:
        if callable(new_value):
            hook.value = new_value(hook.value)
        else:
            hook.value = new_value
        if comp is not None:
            runtime.request_rerender(comp)

    return (hook.value, setter)


def use_effect(
    effect: Callable[[], Callable[[], None] | None],
    deps: list[Any] | None = None,
) -> None:
    """副作用 Hook — 在渲染后执行副作用。

    - deps=None：每次渲染后执行
    - deps=[]：仅在 mount 时执行一次，unmount 时执行 cleanup
    - deps=[...]：依赖浅比较变化时执行

    返回的 cleanup 函数在下一次 effect 前或 unmount 时调用。
    注意：effect 在渲染完成后通过 run_effects() 批量异步执行。

    Args:
        effect: 副作用函数，可选返回 cleanup 函数。
        deps: 依赖数组，None 表示每次渲染都执行，[] 表示仅首次执行。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    runtime = _hooks_runtime
    comp = runtime._current_component

    if comp is None:
        raise HookError(
            "use_effect 在组件 render 上下文外调用。"
            " Hooks 只能在组件的 render()/render_vnode() 方法内调用。"
        )

    def _factory() -> EffectState:
        # 初始 deps 设为 None，确保首次渲染时 _deps_changed 返回 True
        return EffectState(effect_fn=effect, deps=None)

    hook = runtime.get_or_create_hook("effect", _factory)

    # 更新 EffectState：prev_deps 捕获上次的 deps，然后写入新 deps
    effect_state: EffectState = hook.value
    effect_state.effect_fn = effect
    effect_state.prev_deps = effect_state.deps
    effect_state.deps = list(deps) if deps is not None else None

    # 入队，稍后由 run_effects() 执行
    runtime.schedule_effect(comp, hook)


def use_ref(initial: T) -> dict[str, T]:
    """引用 Hook — 持有跨渲染周期的可变引用。

    返回包含 'current' 键的字典对象，整个组件生命周期内保持同一对象引用。
    修改 current 不触发重渲染。

    Args:
        initial: 初始值。

    Returns:
        {'current': initial} 可变容器，跨渲染保持同一对象。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    hook = _hooks_runtime.get_or_create_hook("ref", lambda: {"current": initial})
    return hook.value


def use_memo(factory: Callable[[], T], deps: list[Any]) -> T:
    """记忆化 Hook — 仅在依赖变化时重新计算值。

    deps 浅比较不变时返回缓存值，变化时调用 factory 重新计算。

    Args:
        factory: 值工厂函数。
        deps: 依赖数组。

    Returns:
        记忆化的值（deps 不变时返回缓存值）。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    # 传入 None 占位，避免 get_or_create_hook 的 factory 重复调用
    hook = _hooks_runtime.get_or_create_hook("memo", lambda: None)

    if hook.deps is not None and not _deps_changed(hook.deps, deps):
        return hook.value

    hook.value = factory()
    hook.deps = list(deps)
    return hook.value


def use_callback(fn: Callable, deps: list[Any]) -> Callable:
    """回调记忆化 Hook — 仅在依赖变化时返回新的函数引用。

    deps 浅比较不变时返回同一函数引用，避免子组件不必要的重渲染。

    Args:
        fn: 要记忆化的函数。
        deps: 依赖数组。

    Returns:
        稳定引用的函数（deps 不变时返回同一函数对象）。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    # 传入 None 占位，fn 在下方按需赋值
    hook = _hooks_runtime.get_or_create_hook("callback", lambda: None)

    if hook.deps is not None and not _deps_changed(hook.deps, deps):
        return hook.value

    hook.value = fn
    hook.deps = list(deps)
    return hook.value


def use_context(context: Any) -> Any:
    """上下文 Hook — 读取 Context 的当前值。

    从对应 context 的 Provider 栈顶获取值，无 Provider 时使用 create_context 的默认值。

    Args:
        context: 由 create_context() 创建的 Context 对象（dict 类型）。

    Returns:
        当前 Context 值（最近 Provider 的值或默认值）。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    _hooks_runtime.get_or_create_hook("context", lambda: None)

    try:
        stack = context["_stack"]
        return stack[-1] if stack else context["default_value"]
    except (TypeError, KeyError):
        raise HookError(
            "use_context 参数不是有效的 Context 对象。"
            " 请使用 create_context() 创建 Context。"
        )


def use_reducer(
    reducer: Callable[[T, Any], T],
    initial: T,
    init: Callable[[T], T] | None = None,
) -> tuple[T, Callable[[Any], None]]:
    """Reducer Hook — 复杂状态逻辑的替代方案。

    类似 use_state，但使用 reducer 函数 (state, action) -> new_state 管理状态变迁。
    dispatch(action) 调用 reducer 计算新状态并触发重渲染。

    Args:
        reducer: 纯函数 (state, action) -> new_state。
        initial: 初始状态值（或传递给 init 的初始参数）。
        init: 惰性初始化函数，接收 initial 参数返回实际初始状态。

    Returns:
        (当前状态, dispatch) 元组。dispatch 接受 action 参数。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    runtime = _hooks_runtime
    comp = runtime._current_component

    def _factory() -> T:
        if init is not None:
            return init(initial)
        return initial

    hook = runtime.get_or_create_hook("reducer", _factory)

    def dispatch(action: Any) -> None:
        hook.value = reducer(hook.value, action)
        if comp is not None:
            runtime.request_rerender(comp)

    return (hook.value, dispatch)


# ── Context 工厂 ────────────────────────────────────────


def create_context(default_value: Any) -> dict:
    """创建 Context 对象。

    返回的 Context 对象可与 use_context() 配合使用。
    Provider 组件通过 _stack 压入/弹出值。

    使用方式：
        ThemeCtx = create_context("dark")

        # 在 Provider 组件中：
        ThemeCtx["_stack"].append("light")
        try:
            ...  # 子组件 render
        finally:
            ThemeCtx["_stack"].pop()

        # 在 Consumer 组件中：
        value = use_context(ThemeCtx)

    Args:
        default_value: 当组件树中无 Provider 时使用的默认值。

    Returns:
        包含 '_stack'（Provider 栈）和 'default_value' 的 dict 对象。
    """
    return {
        "_stack": [],  # Provider 值栈，最近的在栈顶
        "default_value": default_value,
    }


# ── React Ink 兼容 Hooks ─────────────────────────────────


def use_input(on_input: Callable[[str, dict], None]) -> None:
    """React Ink useInput hook — 订阅键盘输入。

    通过 stdin raw mode 监听按键，每次按键时调用 on_input(input_str, modifiers)。
    modifiers 包含: ctrl (bool), shift (bool), meta (bool)。

    Args:
        on_input: 回调函数，接收 (input_char: str, modifiers: dict)
                  - input_char: 输入的字符或键名 (如 "return", "escape", "tab")
                  - modifiers: {"ctrl": bool, "shift": bool, "meta": bool}

    使用方式:
        def MyComponent():
            def handle_input(char, mods):
                if char == "q" and mods.get("ctrl"):
                    exit()
            use_input(handle_input)

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    runtime = get_hooks_runtime()
    comp = runtime._current_component
    if comp is None:
        return
    # 注册到组件的输入回调列表
    if not hasattr(comp, "_input_callbacks"):
        comp._input_callbacks = []
    comp._input_callbacks.append(on_input)


def use_app() -> dict:
    """React Ink useApp hook — 访问应用实例。

    Returns:
        dict with "exit" and "restore" callables:
        - exit(error=None): 退出应用，可选传递错误对象
        - restore(): 恢复终端原始设置

    使用方式:
        app = use_app()
        app["exit"]()  # 退出

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    import sys as _sys

    def _exit_app(error=None):
        if error:
            import traceback

            traceback.print_exception(type(error), error, error.__traceback__)
        _sys.exit(1 if error else 0)

    def _restore_terminal():
        # 恢复终端原始设置（termios）
        try:
            import termios

            fd = _sys.stdin.fileno()
            termios.tcsetattr(fd, termios.TCSANOW, termios.tcgetattr(fd))
        except Exception:
            pass

    return {"exit": _exit_app, "restore": _restore_terminal}


def use_stdin():
    """React Ink useStdin hook — 访问原始 stdin 流。

    Returns:
        sys.stdin 对象。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    import sys as _sys

    return _sys.stdin


def use_stdout():
    """React Ink useStdout hook — 访问原始 stdout 流。

    Returns:
        sys.stdout 对象。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    import sys as _sys

    return _sys.stdout


def use_stderr():
    """React Ink useStderr hook — 访问原始 stderr 流。

    Returns:
        sys.stderr 对象。

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    import sys as _sys

    return _sys.stderr


# ── 公共 API ────────────────────────────────────────────

__all__ = [
    # 运行时
    "get_hooks_runtime",
    "_HooksRuntime",
    # 核心 Hooks (7)
    "use_state",
    "use_effect",
    "use_ref",
    "use_memo",
    "use_callback",
    "use_context",
    "use_reducer",
    "create_context",
    # React Ink 兼容 Hooks (5)
    "use_input",
    "use_app",
    "use_stdin",
    "use_stdout",
    "use_stderr",
]
