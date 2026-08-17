"""hooks 门面 — React Ink hooks 全套（use_state / use_effect / ...）。

模块边界（2026-08-05 架构优化）：原单一 hooks.py（1569 行）按 hooks 家族
拆分为独立模块，本文件作为公共门面 re-export 全部函数符号 + **持有全部
模块级可变状态（唯一真源）**：

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

★ 状态归属设计（PEP 562 权衡后收敛）：模块级可变状态（``_current_fiber_stack``
等）**全部定义在本门面模块**——子模块（``_hooks_*.py``）加载期
``from src.tui.ink import hooks`` 获取部分初始化模块引用，运行期经
``hooks._xxx`` 属性访问最新值。原因：外部/测试契约直接读写门面属性
（``hooks._current_fiber_stack = [fiber]`` 注入、``hooks._app_control``
读取）——状态若留在子模块，门面静态 import 复制旧引用/赋值不转发，契约
失效。门面为状态唯一真源后，读写天然一致。

依赖方向（单向无环）：
  ``_hooks_core`` / ``_hooks_input`` / ``_hooks_component`` /
  ``_hooks_focus`` / ``_hooks_env`` → fiber（结构类型）+ 本门面（状态）
  ``hooks``（本模块，公共门面）→ 全部

调用期绑定说明（保留原语义）：渲染函数组件期间（reconciler.begin_work），
``use_*`` 读取当前 fiber 栈顶（``_current_fiber_stack``）。每个 function
fiber 在每次渲染前 ``reset_hooks()`` 清零 hook_index，``use_*`` 按下标复用
上次的 hook 节点（保留状态/引用），从而跨渲染保持状态。
"""

from __future__ import annotations

import itertools
import weakref
from typing import Any, Callable, List

from .fiber import Fiber

# ═══════════════════════════════════════════════════════════
# 模块级可变状态（唯一真源；子模块经 ``hooks._xxx`` 访问最新值）
# ═══════════════════════════════════════════════════════════

# 渲染期当前 fiber 栈（渲染线程单线程，模块级栈即可）
_current_fiber_stack: List[Fiber] = []
# 状态更新后触发重渲染的回调（session 注入）
_schedule_callback: Callable[[], None] | None = None
# context 注册表（create_context → reconciler provider host 消费）
# ★ P3-3（review 方向）：**WeakValueDictionary**——create_context 写入后无
#   清理路径，普通 dict 只增不回收（无限增长）。弱引用方案：Context 不再被
#   外部引用（仅注册表持有）时 GC 自动移除条目。**评估注**：Context 被外部
#   持有（模块级/组件级 ctx 变量）时 WeakValueDictionary 不回收——这是合理
#   语义（外部持有即调用方负责生命周期），且与 BUG-18「注册表条目与 Provider
#   挂载解耦（进程生命周期）」设计对齐——挂载/卸载不清理，仅 GC 自然回收。
_context_registry: "weakref.WeakValueDictionary[str, Any]" = weakref.WeakValueDictionary()
# context 缓存版本号（provider 值变化时递增；use_context 命中校验）
_context_version: int = 0
# std 流访问器（session 注入；useStdin/useStdout/useStderr 读取）
_stdin_accessor: Callable[[], Any] | None = None
_stdout_accessor: Callable[[], Any] | None = None
_stderr_accessor: Callable[[], Any] | None = None
# input router 注入回调（session 注入；reconciler 每帧发布 composite router）
_input_router_callback: Callable[[Any], None] | None = None
# app control（session 注入：{"exit": fn, "clear": fn}；useApp 读取）
_app_control: dict | None = None
# 渲染 flush 等待回调（session 注入）
_render_flush_fn: Callable[[], Any] | None = None
# 终端挂起回调（session 注入）
_suspend_terminal_fn: Callable[[Any], Any] | None = None
# 焦点管理状态（useFocus/useFocusManager）
_focus_enabled: bool = True
_focus_active: str | None = None
_focus_ids: list[str] = []
_focus_id_seq = itertools.count()
# 窗口尺寸状态（useWindowSize）
_window_size: tuple[int, int] = (80, 24)
_window_size_version: int = 0
_window_size_listeners: set = set()
_window_size_accessor: Callable[[], tuple[int, int]] | None = None
# 光标定位回调（session 注入；useCursor）
_cursor_position_fn: Callable[[Any], None] | None = None
# 任意键已按下标志（useStdin().isAnyKeyPressed——React Ink 语义：用户曾
# 按过任意键后恒 True，不复位；session 经 InputDispatcher 注入置位回调）
_any_key_pressed: bool = False


def mark_any_key_pressed() -> None:
    """标记任意键已按下（useStdin().isAnyKeyPressed 置位）。

    由 session 注入 InputDispatcher 按键回调调用（每个输入字节分发时触发）；
    置位后保持 True（与 React Ink 语义一致——用于检测用户是否已交互，
    spinner 等据此暂停动画）。
    """
    global _any_key_pressed
    _any_key_pressed = True


def reset_any_key_pressed() -> None:
    """复位任意键标志（测试/会话复用用）。"""
    global _any_key_pressed
    _any_key_pressed = False

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
    useAnimation,
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
    "useId",
    "use_input",
    "use_fullscreen",
    "use_modal",
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
    "mark_any_key_pressed",
    "reset_any_key_pressed",
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
    # ── P3-2 补全（review 方向）：内部 re-export 符号补全 __all__——与门面
    #    import 列表一致（``from src.tui.ink.hooks import *`` 可获取全部符号，
    #    含内部基础设施）。修复前 __all__ 未列全，通配导入漏掉
    #    _next_hook/_current/_push_current/_pop_current/_schedule/
    #    _make_setter 等（外部/测试按名导入虽不受影响，但 * 导入契约不完整）。
    "_push_current",
    "_pop_current",
    "_current",
    "_schedule",
    "_next_hook",
    "_next_state_hook",
    "_make_setter",
    "_clear_fiber_state_queues",
    "_object_is",
    "_memo_deps_changed",
    "_bump_context_version",
    "_publish_input_router",
    "_make_compat_handler",
    "_event_input",
    "_event_key",
    "_make_imperative_cleanup",
    "_clear_focus_active",
    "_refresh_window_size",
    "_subscribe_window_size",
    "HookStateError",
]
