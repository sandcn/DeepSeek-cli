"""hooks 环境族 — useMeasure / useStdin / useStdout / useStderr /
useSyncExternalStore / usePaste / useBoxMetrics / useWindowSize /
useCursor / useIsScreenReaderEnabled / useAnimation + 环境注入函数。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——环境/IO 相关
hooks 独立成模块（终端尺寸、流访问、外部 store 订阅、光标、动画帧），供
session（``set_window_size_accessor``/``set_cursor_position_fn``）、组件库
共享。依赖 ``_hooks_core``（``_next_hook``/``use_ref``/``use_state``/
``useLayoutEffect``/``_schedule``/std 访问器）。

依赖方向：本模块 → _hooks_core / fiber；不反向依赖。
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable

from .fiber import SyncStoreHook, PasteHook
from . import _animation
from ._hooks_core import (
    _next_hook,
    use_ref,
    use_state,
    useLayoutEffect,
    _schedule_ctx,
)
from ._hook_context import current_context
# ★ 会话级状态真源在 ``_hook_context.HookContext``（每渲染会话一个实例）；
#   本模块经 ``_hooks_module._xxx`` 访问，门面按「当前上下文」路由。
from src.tui.ink import hooks as _hooks_module

# ★ logger 名保持 ``src.tui.ink.hooks``（模块拆分后日志命名不变，见
#   _hooks_core.py 注释）。
_logger = logging.getLogger("src.tui.ink.hooks")


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


def set_raw_mode_accessor(
    supported_fn: Callable[[], bool] | None,
    set_fn: Callable[[bool], Any] | None,
    ctx: "HookContext | None" = None,
) -> None:
    """注入 raw 模式能力（session 调用）。

    Args:
        supported_fn: ``() -> bool`` 查询是否支持 raw 模式切换。
        set_fn: ``(enabled: bool) -> bool`` 切换 raw 模式。
        ctx: 目标会话上下文；None 用当前激活上下文。
    """
    target = ctx if ctx is not None else current_context()
    target.raw_mode_supported = supported_fn
    target.raw_mode_callback = set_fn


def useStdin() -> dict:
    """React useStdin 等价物：返回 stdin 访问。

    Returns:
        dict：``{"stdin", "isRawModeSupported", "setRawMode",
        "internal_exitOnCtrlC", "isAnyKeyPressed"}``——stdin 为 session 注入的
        Input 实例（惰性读取；未注入时 None）；setRawMode 转发到 session 注入
        的 raw 模式回调（未注入时 no-op）；isRawModeSupported 反映真实能力
        （session ``set_interactive`` 解析结果）；internal_exitOnCtrlC 恒 True。

    ★ P3-5（review 方向，已修复）：返回对象**身份跨渲染稳定**（``use_ref``
    缓存，字段原地刷新）——官方 React Ink 语义中 useStdin 返回值应稳定
    （``use_memo(deps=[stdin])`` 等按身份比较的消费方才不会每帧 miss）。
    此前每帧新建 dict + 闭包，文件级注释记录为「文档化差异」，现按官方
    语义收敛。
    """
    cached = use_ref(None)
    stdin = _hooks_module._stdin_accessor() if _hooks_module._stdin_accessor is not None else None
    supported_fn = getattr(_hooks_module, "_raw_mode_supported", None)
    try:
        supported = bool(supported_fn()) if supported_fn is not None else False
    except Exception:
        _logger.debug("isRawModeSupported 查询异常", exc_info=True)
        supported = False
    value = cached.current
    if value is None:
        def _set_raw_mode(enabled: bool = True):
            set_fn = getattr(_hooks_module, "_raw_mode_callback", None)
            if set_fn is None:
                return None
            try:
                return set_fn(bool(enabled))
            except Exception:
                _logger.debug("setRawMode 回调异常", exc_info=True)
                return None

        value = {
            "stdin": stdin,
            "isRawModeSupported": supported,
            "setRawMode": _set_raw_mode,
            "internal_exitOnCtrlC": True,
            "isAnyKeyPressed": bool(getattr(_hooks_module, "_any_key_pressed", False)),
        }
        cached.current = value
        return value
    value["stdin"] = stdin
    value["isRawModeSupported"] = supported
    value["isAnyKeyPressed"] = bool(getattr(_hooks_module, "_any_key_pressed", False))
    return value


def useStdout() -> dict:
    """React useStdout 等价物：返回 stdout 访问。

    Returns:
        dict：``{"stdout", "write"}``——stdout 为 session 注入的渲染器输出流
        （惰性读取）；write 直接写流（未注入时 no-op）。

    ★ 返回对象身份跨渲染稳定（``use_ref`` 缓存，字段原地刷新）。
    """
    cached = use_ref(None)
    stdout = _hooks_module._stdout_accessor() if _hooks_module._stdout_accessor is not None else None
    value = cached.current
    if value is None:
        def _write(data: str = "") -> None:
            target = _hooks_module._stdout_accessor() if _hooks_module._stdout_accessor is not None else None
            write = getattr(target, "write", None)
            if write is None:
                return None
            try:
                write(data)
            except Exception:
                _logger.debug("useStdout().write 异常", exc_info=True)
            return None

        value = {"stdout": stdout, "write": _write}
        cached.current = value
        return value
    value["stdout"] = stdout
    return value


def useStderr() -> dict:
    """React useStderr 等价物：返回 stderr 访问。

    Returns:
        dict：``{"stderr", "write"}``——stderr 为 session 注入的
        ``sys.__stderr__``（惰性读取）；write 直接写流。

    ★ 返回对象身份跨渲染稳定（``use_ref`` 缓存，字段原地刷新）。
    """
    cached = use_ref(None)
    stderr = _hooks_module._stderr_accessor() if _hooks_module._stderr_accessor is not None else None
    value = cached.current
    if value is None:
        def _write(data: str = "") -> None:
            target = _hooks_module._stderr_accessor() if _hooks_module._stderr_accessor is not None else None
            write = getattr(target, "write", None)
            if write is None:
                return None
            try:
                write(data)
            except Exception:
                _logger.debug("useStderr().write 异常", exc_info=True)
            return None

        value = {"stderr": stderr, "write": _write}
        cached.current = value
        return value
    value["stderr"] = stderr
    return value


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
    # ★ 多会话隔离（P0 架构修复）：listener 可能在渲染期之外、任意线程被
    #   store 触发——捕获本组件所属会话的上下文，经 ``_schedule_ctx`` 调度
    #   到**本会话**的重渲染（``_schedule()`` 读「当前上下文」，跨线程/跨
    #   会话时会调度到错误的会话或默认上下文）。
    ctx = current_context()

    def _commit_subscribe() -> None:
        # ★ P2-3 修复（review 方向）：订阅/重订阅移入提交阶段（layout
        #   effect）——渲染期仅比较身份（``hook.last_subscribe``），不再在
        #   渲染中途同步调用 subscribe。修复前渲染期直接调用 subscribe：
        #   若 store 在 subscribe 期间同步通知 listener（``_schedule()``），
        #   listener 在渲染中途触发重渲染请求，存在重入风险。提交期渲染
        #   已结束，subscribe 的同步通知经 ``_schedule()`` 调度下帧渲染，
        #   无重入。deps=None 每次渲染提交期执行——内部按 last_subscribe
        #   身份判断是否重订阅；订阅异常置 last_subscribe=None，下帧可重试
        #   （保持原语义）。
        #   ★ P3（review）：删除 ``subscribed`` 死字段（仅写不读，重订阅
        #   判定完全由 ``last_subscribe`` 身份承担）。
        if hook.last_subscribe is not subscribe:
            if hook.cleanup is not None:
                try:
                    hook.cleanup()
                except Exception:
                    _logger.debug("useSyncExternalStore 旧订阅清理异常", exc_info=True)
                hook.cleanup = None
            hook.last_subscribe = subscribe
            try:
                cleanup = subscribe(lambda: _schedule_ctx(ctx))
                hook.cleanup = cleanup if callable(cleanup) else None
            except Exception:
                # ★ P3 修复（review 方向）：订阅抛异常后复位 last_subscribe
                #   （cleanup 已置 None）——下帧重试订阅。修复前 last_subscribe
                #   已更新 → 永不重试，组件永久失去 store 更新。
                _logger.debug("useSyncExternalStore 订阅异常", exc_info=True)
                hook.cleanup = None
                hook.last_subscribe = None

    # ★ P2-3：layout effect 提交期执行订阅（deps=None 每次渲染提交期执行，
    #   内部按 last_subscribe 身份判断重订阅；订阅异常可下帧重试）。
    useLayoutEffect(_commit_subscribe, None)

    try:
        hook.snapshot = get_snapshot()
    except Exception:
        _logger.debug("useSyncExternalStore 快照读取异常", exc_info=True)
    return hook.snapshot


# ═══════════════════════════════════════════════════════════
# usePaste（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════


def usePaste(handler: Callable[[str], Any], options: "dict | None" = None) -> None:
    """React Ink ``usePaste`` 等价物：处理粘贴文本。

    粘贴事件（单次输入多字符）到达时调用 ``handler(text)``。官方语义：存在
    active ``usePaste`` 时粘贴内容**不转发**给 ``useInput``（两条独立通道）——
    因此粘贴事件在存在 active paste 钩子时被直接消费（handler 返回值不参与
    消费判定，官方签名为 ``(text) -> void``）。``options["isActive"]`` 控制
    是否参与粘贴路由（默认 True）。

    与 React Ink 差异：本框架同时支持两条粘贴通道——(1) 终端启用括号粘贴
    模式（``render({interactive})`` / TuiConfig ``bracketed_paste``）时粘贴
    内容以 ``kind="paste"`` 单事件到达（内容含换行/控制字符不被解释为按键）；
    (2) 未协商时回退「单次输入事件字符数 > 1」启发式（终端粘贴为整段到达，
    普通打字逐字符）。

    Args:
        handler: 粘贴处理回调 ``(text: str) -> Any``（返回值忽略）。
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
# 状态（_window_size/_window_size_version/_window_size_listeners/
# _window_size_accessor）为会话级（``HookContext`` 字段）；本模块经
# ``_hooks_module._window_size_*`` 访问（门面按当前上下文路由）。


def set_window_size_accessor(
    fn: Callable[[], tuple[int, int]] | None, ctx: "HookContext | None" = None,
) -> None:
    """注入窗口尺寸访问器（session 调用：``lambda: (columns, rows)``）。

    ``ctx`` 非 None 时写入指定会话上下文；None 时写当前激活上下文。
    """
    target = ctx if ctx is not None else current_context()
    target.window_size_accessor = fn


def _refresh_window_size(ctx: "HookContext | None" = None) -> None:
    """渲染期刷新窗口尺寸（useWindowSize 调用前）。"""
    target = ctx if ctx is not None else current_context()
    if target.window_size_accessor is not None:
        try:
            target.window_size = target.window_size_accessor()
        except Exception:
            # ★ P3-4（review 方向）：不静默吞异常——记 debug 日志（窗口尺寸
            #   访问器异常为环境级降级，不中断渲染，但须可观测）。
            _logger.debug("窗口尺寸访问器异常，回退旧尺寸", exc_info=True)


def _subscribe_window_size(listener: Callable[[], None]) -> Callable[[], None]:
    """订阅窗口尺寸变化（useSyncExternalStore subscribe）。"""
    _hooks_module._window_size_listeners.add(listener)
    return lambda: _hooks_module._window_size_listeners.discard(listener)


def _notify_window_size(ctx: "HookContext | None" = None) -> None:
    """通知窗口尺寸变化（session resize 时调用）：触发全部订阅重渲染。

    ``ctx`` 非 None 时作用于指定会话上下文（session 渲染线程传自己的；
    多会话下各通知各自订阅者）。
    """
    target = ctx if ctx is not None else current_context()
    target.window_size_version += 1
    for fn in list(target.window_size_listeners):
        try:
            fn()
        except Exception:
            # ★ P3-4（review 方向）：不静默吞异常——记 debug 日志（单订阅
            #   回调异常不阻断其余订阅通知，但须可观测）。
            _logger.debug("窗口尺寸订阅回调异常", exc_info=True)


def useWindowSize() -> dict:
    """React Ink ``useWindowSize`` 等价物：返回 ``{"columns", "rows"}``。

    终端尺寸变化时自动重渲染（订阅 window size store）。尺寸来源为 session
    注入的 accessor（未注入时返回 (80, 24) 默认值）。

    ★ 契约（review）：订阅的是 ``_window_size_version``（resize 通知），
    accessor 值仅在渲染期刷新——若 accessor 值变化但未经
    ``_notify_window_size()``（如测试直接改 accessor），本帧返回新值但不会
    自动触发后续重渲染；生产路径 resize 由 session 统一通知。

    Returns:
        dict：``{"columns": int, "rows": int}``。
    """
    useSyncExternalStore(_subscribe_window_size, lambda: _hooks_module._window_size_version)
    _refresh_window_size()
    # ★ P3（review）：解包防御——accessor 返回非二元组/非可迭代（畸形值）时
    #   ``columns, rows = ...`` 抛 ValueError/TypeError；修复前解包在 try 之外
    #   （accessor 本身异常已捕获，但「返回畸形值」未覆盖）→ 回退 (80, 24)。
    try:
        columns, rows = _hooks_module._window_size
        columns = int(columns)
        rows = int(rows)
    except (TypeError, ValueError):
        _logger.debug("窗口尺寸 accessor 返回畸形值，回退 (80, 24)", exc_info=True)
        columns, rows = 80, 24
    return {"columns": columns, "rows": rows}


# ═══════════════════════════════════════════════════════════
# useCursor（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════
# 状态（_cursor_position_fn）为会话级（``HookContext`` 字段，门面按当前
# 上下文路由）。


def set_cursor_position_fn(fn: Callable[[Any], None] | None, ctx: "HookContext | None" = None) -> None:
    """注入光标定位回调（session 调用——IME 光标定位）。

    ``ctx`` 非 None 时写入指定会话上下文；None 时写当前激活上下文。
    """
    target = ctx if ctx is not None else current_context()
    target.cursor_position_fn = fn


def useCursor() -> dict:
    """React Ink ``useCursor`` 等价物：返回终端光标定位方法。

    ``setCursorPosition({x, y})`` 设置光标位置（相对 Ink 输出顶部/左侧）；
    传 ``None`` 隐藏光标（IME 组合输入场景）。未注入回调时 no-op。

    ★ 身份说明（review）：返回 dict 与 ``setCursorPosition`` 闭包**每帧新建**
    （惰性读取注入回调）——``use_memo(deps=[cursor])`` 等按身份比较的消费方
    每帧 miss，须按字段值使用（与 useStdin/useStdout/useStderr 同约定）。

    Returns:
        dict：``{"setCursorPosition": callable}``。
    """
    ctx = current_context()

    def _set_cursor_position(position) -> None:
        fn = ctx.cursor_position_fn
        if fn is not None:
            try:
                fn(position)
            except Exception:
                # ★ P3-4（review 方向）：不静默吞异常——记 debug 日志（光标
                #   定位回调异常为环境级降级，不中断渲染，但须可观测）。
                _logger.debug("光标定位回调异常", exc_info=True)

    return {"setCursorPosition": _set_cursor_position}


# ═══════════════════════════════════════════════════════════
# useIsScreenReaderEnabled（React Ink v6 等价物）
# ═══════════════════════════════════════════════════════════


def useIsScreenReaderEnabled() -> bool:
    """React Ink ``useIsScreenReaderEnabled`` 等价物：是否启用了屏幕阅读器。

    由 ``render({isScreenReaderEnabled: True})`` 注入（``set_screen_reader_enabled``）；
    未启用时恒 False（终端普通模式）。供渲染不同输出的条件判断（如屏幕阅读器
    下输出纯文本而非 ANSI 装饰）。

    Returns:
        bool：是否启用屏幕阅读器模式。
    """
    return bool(getattr(_hooks_module, "_screen_reader_enabled", False))


def set_screen_reader_enabled(enabled: bool, ctx: "HookContext | None" = None) -> None:
    """注入屏幕阅读器开关（``render({isScreenReaderEnabled})`` 调用）。

    ``ctx`` 非 None 时写入指定会话上下文（``render()`` 传本会话上下文，
    退出时还原——修复前写模块级全局，跨会话泄漏）；None 时写当前上下文。
    """
    target = ctx if ctx is not None else current_context()
    target.screen_reader_enabled = bool(enabled)


# ═══════════════════════════════════════════════════════════
# useAnimation（React Ink v7 等价物：官方 {frame,time,delta,reset} + 共享驱动）
# ═══════════════════════════════════════════════════════════


def useAnimation(options: "dict | None" = None) -> dict:
    """React Ink ``useAnimation`` 等价物：驱动动画的帧计时器。

    返回 ``{"frame", "time", "delta", "reset"}``：
      - ``frame``：离散帧计数（每 ``interval`` 毫秒 +1，适合 spinner 索引）；
      - ``time``：自开始/复位以来的累计毫秒（适合正弦波等连续动画）；
      - ``delta``：距上次渲染的毫秒数（适合速度/物理驱动，已考虑渲染节流）；
      - ``reset``：把 frame/time/delta 归零并从当前时刻重新计时。

    多个动画组件**共用一个驱动**（``_animation`` 模块，session 渲染循环每帧
    推进一次）——一轮 tick 只触发一轮渲染，合并动画开销。

    Args:
        options: ``{"interval": int(ms, 默认 100), "isActive": bool(默认 True)}``。
            ``isActive=False`` 时动画停止且返回全 0；重新置 True 时归零重启。

    Returns:
        dict：``{"frame": int, "time": float, "delta": float, "reset": callable}``。
    """
    interval = 100.0
    is_active = True
    if isinstance(options, dict):
        try:
            interval = max(1.0, float(options.get("interval", 100)))
        except (TypeError, ValueError, OverflowError):
            interval = 100.0
        if not math.isfinite(interval):
            interval = 100.0
        is_active = bool(options.get("isActive", True))

    # 订阅共享动画驱动（tick 变化 → 触发重渲染）；hook 顺序恒定（无条件调用）。
    useSyncExternalStore(_animation.subscribe_animation, _animation.animation_snapshot)

    state_ref = use_ref(None)

    def _reset() -> None:
        state_ref.current = None

    if not is_active:
        # 停止态：归零（下次激活时重新计时）
        state_ref.current = None
        return {"frame": 0, "time": 0.0, "delta": 0.0, "reset": _reset}

    now = time.monotonic()
    state = state_ref.current
    if not isinstance(state, dict) or "start" not in state:
        state = {"start": now, "last_time": 0.0}
        state_ref.current = state
    elapsed_ms = max(0.0, (now - state["start"]) * 1000.0)
    delta_ms = max(0.0, elapsed_ms - state["last_time"])
    state["last_time"] = elapsed_ms
    frame = int(elapsed_ms // interval)
    return {"frame": frame, "time": elapsed_ms, "delta": delta_ms, "reset": _reset}


__all__ = [
    "useMeasure",
    "useStdin",
    "useStdout",
    "useStderr",
    "useSyncExternalStore",
    "usePaste",
    "useBoxMetrics",
    "useWindowSize",
    "set_window_size_accessor",
    "_refresh_window_size",
    "_subscribe_window_size",
    "_notify_window_size",
    "set_cursor_position_fn",
    "useCursor",
    "useIsScreenReaderEnabled",
    "set_screen_reader_enabled",
    "useAnimation",
]
