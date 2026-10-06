"""hooks 门面 — React Ink hooks 全套（use_state / use_effect / ...）。

模块边界（2026-08-05 架构优化）：原单一 hooks.py（1569 行）按 hooks 家族
拆分为独立模块，本文件作为公共门面 re-export 全部函数符号，并**代理**全部
会话级可变状态：

  - ``_hooks_core.py``       — hook 基础设施（_next_hook 模板方法）+ 基础
                               hooks（use_state/use_reducer/use_ref/
                               use_effect/useLayoutEffect/use_memo/
                               use_callback/useId/create_context/use_context）
  - ``_hooks_input.py``      — 输入 hooks（use_input / input router 发布 /
                               (input, key) 双签名适配）
  - ``_hooks_component.py``  — 组件 hooks（useApp/memo/forwardRef/
                               useImperativeHandle/use_error_state/usePrevious
                               + app control/render flush/终端挂起注入）
  - ``_hooks_focus.py``      — 焦点 hooks（useFocus/useFocusManager + 仲裁状态）
  - ``_hooks_env.py``        — 环境 hooks（useMeasure/useStdin/useStdout/
                               useStderr/useSyncExternalStore/usePaste/
                               useBoxMetrics/useWindowSize/useCursor/
                               useIsScreenReaderEnabled/useAnimation + 注入）

★ 状态归属（P0 架构修复，2026-08-16）：会话级可变状态收拢为**可实例化**的
``_hook_context.HookContext``（每个 ``Reconciler`` 一个）——此前为模块级
全局变量，同一进程只能存在一个活跃渲染会话（第二个会话构造即覆盖第一个的
重渲染回调 / router 注入 / app control，多会话串台）。本门面以 PEP 562
``__getattr__`` + 自定义 module class 的 ``__setattr__`` **代理**到「当前
上下文」（``_hook_context.current_context()``，渲染期由 ``Reconciler`` /
``InkSession`` 激活；未激活时回退默认上下文）——``hooks._schedule_callback``
等既有读写契约（测试/外部注入）语义不变，``_hooks_*.py`` 子模块的
``_hooks_module._xxx`` 访问自动路由到正确会话。

依赖方向（单向无环）：
  ``_hook_context`` → 标准库
  ``_hooks_core`` / ``_hooks_input`` / ``_hooks_component`` /
  ``_hooks_focus`` / ``_hooks_env`` → fiber（结构类型）+ 本门面（状态代理）
  ``hooks``（本模块，公共门面）→ 全部

调用期绑定说明（保留原语义）：渲染函数组件期间（reconciler.begin_work），
``use_*`` 读取当前 fiber 栈顶（当前上下文的 ``current_fiber_stack``）。每个
function fiber 在每次渲染前 ``reset_hooks()`` 清零 hook_index，``use_*``
按下标复用上次的 hook 节点（保留状态/引用），从而跨渲染保持状态。
"""

from __future__ import annotations

import sys
import types
import weakref
from typing import Any

from ._hook_context import (
    HookContext,
    current_context,
    default_context,
    pop_context,
    push_context,
)

# ═══════════════════════════════════════════════════════════
# 会话级状态代理（旧下划线名 → HookContext 字段名）
# ═══════════════════════════════════════════════════════════
#: Context 注册表 — **进程级共享**（``create_context`` 是 Context 的*定义*，
#: 与会话无关：模块级调用的 ``create_context`` 必须被任意会话的 provider
#: 渲染找到）。会话级状态只有 ``context_version``（逐 fiber 缓存版本）。
_context_registry: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()

#: 仅状态字段参与代理；函数符号正常落在模块 ``__dict__``（import 绑定）。
_ALIASES = {
    "_current_fiber_stack": "current_fiber_stack",
    "_schedule_callback": "schedule_callback",
    "_context_version": "context_version",
    "_stdin_accessor": "stdin_accessor",
    "_stdout_accessor": "stdout_accessor",
    "_stderr_accessor": "stderr_accessor",
    "_input_router_callback": "input_router_callback",
    "_app_control": "app_control",
    "_render_flush_fn": "render_flush_fn",
    "_suspend_terminal_fn": "suspend_terminal_fn",
    "_cursor_position_fn": "cursor_position_fn",
    "_window_size_accessor": "window_size_accessor",
    "_screen_reader_enabled": "screen_reader_enabled",
    "_focus_enabled": "focus_enabled",
    "_focus_active": "focus_active",
    "_focus_ids": "focus_ids",
    "_focus_id_seq": "focus_id_seq",
    "_window_size": "window_size",
    "_window_size_version": "window_size_version",
    "_window_size_listeners": "window_size_listeners",
    "_any_key_pressed": "any_key_pressed",
    "_animation_tick": "animation_tick",
    "_animation_last_time": "animation_last_time",
    "_animation_listeners": "animation_listeners",
}


class _HooksModule(types.ModuleType):
    """hooks 门面模块类：状态属性读写代理到当前 ``HookContext``。"""

    def __getattr__(self, name):
        target = _ALIASES.get(name)
        if target is not None:
            return getattr(current_context(), target)
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        target = _ALIASES.get(name)
        if target is not None:
            setattr(current_context(), target, value)
            return
        super().__setattr__(name, value)


sys.modules[__name__].__class__ = _HooksModule


def mark_any_key_pressed() -> None:
    """标记任意键已按下（useStdin().isAnyKeyPressed 置位）。

    由 session 注入 InputDispatcher 按键回调调用（每个输入字节分发时触发）；
    置位后保持 True（与 React Ink 语义一致——用于检测用户是否已交互，
    spinner 等据此暂停动画）。
    """
    current_context().any_key_pressed = True


def reset_any_key_pressed() -> None:
    """复位任意键标志（测试/会话复用用）。"""
    current_context().any_key_pressed = False


# ═══════════════════════════════════════════════════════════
# 函数 re-export（实现拆分至 _hooks_* 子模块）
# ═══════════════════════════════════════════════════════════

from ._hooks_core import (
    HookStateError,
    set_schedule_callback,
    set_std_accessors,
    _push_current,
    _pop_current,
    _current,
    _schedule,
    _schedule_ctx,
    _next_hook,
    _next_state_hook,
    _make_setter,
    _clear_fiber_state_queues,
    use_state,
    use_reducer,
    use_ref,
    use_effect,
    useLayoutEffect,
    _object_is,
    _deps_equal,
    deps_changed,
    mark_effect_committed,
    _memo_deps_changed,
    use_memo,
    use_callback,
    useId,
    create_context,
    use_context,
    _bump_context_version,
)
from ._hooks_input import (
    set_input_router_callback,
    _publish_input_router,
    use_input,
    use_fullscreen,
    use_modal,
    _make_compat_handler,
    clear_compat_handler_cache,
    _event_input,
    _event_key,
)
from ._hooks_component import (
    set_app_control,
    set_app_callbacks,
    set_render_flush_fn,
    set_suspend_terminal_fn,
    use_error_state,
    _make_imperative_cleanup,
    forwardRef,
    useImperativeHandle,
    memo,
    useApp,
    usePrevious,
)
from ._hooks_focus import (
    _reset_focus_ids,
    _register_focus_id,
    _resolve_focus_id,
    _clear_focus_active,
    _focus_next,
    _focus_previous,
    _focus_to,
    _focus_enable,
    _focus_disable,
    useFocus,
    useFocusManager,
)
from ._hooks_env import (
    useMeasure,
    useStdin,
    useStdout,
    useStderr,
    useSyncExternalStore,
    usePaste,
    useBoxMetrics,
    useWindowSize,
    set_window_size_accessor,
    _refresh_window_size,
    _subscribe_window_size,
    _notify_window_size,
    set_cursor_position_fn,
    useCursor,
    useIsScreenReaderEnabled,
    set_screen_reader_enabled,
    useAnimation,
)
from ._animation import (
    advance_animation,
    reset_animation_state,
    animation_snapshot,
    has_active_animations,
)

__all__ = [
    # ── 基础 hooks ──
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
    # ── 输入 / 模态 ──
    "use_input",
    "use_fullscreen",
    "use_modal",
    # ── 组件 ──
    "use_error_state",
    "memo",
    "forwardRef",
    "useImperativeHandle",
    "useMeasure",
    "usePrevious",
    "useApp",
    # ── 焦点 ──
    "useFocus",
    "useFocusManager",
    # ── 环境 ──
    "useStdin",
    "useStdout",
    "useStderr",
    "useSyncExternalStore",
    "usePaste",
    "useBoxMetrics",
    "useWindowSize",
    "useCursor",
    "useIsScreenReaderEnabled",
    "useAnimation",
    # ── 会话接线（公开注入点） ──
    "set_schedule_callback",
    "set_input_router_callback",
    "set_app_control",
    "set_app_callbacks",
    "set_std_accessors",
    "set_window_size_accessor",
    "set_cursor_position_fn",
    "set_render_flush_fn",
    "set_suspend_terminal_fn",
    "set_screen_reader_enabled",
    "mark_any_key_pressed",
    "reset_any_key_pressed",
    "deps_changed",
    "mark_effect_committed",
    "clear_compat_handler_cache",
    "HookStateError",
    # ── 会话上下文（多会话隔离） ──
    "HookContext",
    "current_context",
    "default_context",
    "push_context",
    "pop_context",
    # ── 动画驱动 ──
    "advance_animation",
    "reset_animation_state",
    "animation_snapshot",
    "has_active_animations",
]
