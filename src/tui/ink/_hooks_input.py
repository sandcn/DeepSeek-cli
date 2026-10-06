"""hooks 输入族 — use_input + input router 发布 + React Ink (input, key) 适配。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——输入相关 hooks
独立成模块（use_input 的 InputHook 注册 / router 发布 / 双签名适配），供
reconciler（``_publish_input_router``）/session（``set_input_router_callback``）
与组件库（use_input）共享。依赖 ``_hooks_core``（``_next_hook`` 基础设施）。

依赖方向：本模块 → _hooks_core / fiber；不反向依赖。
"""

from __future__ import annotations

import functools
import logging
from collections import OrderedDict
from typing import Any, Callable

from .fiber import InputHook, FullscreenHook
from ._hooks_core import _next_hook
from ._hook_context import HookContext, current_context

# ★ logger 名保持 ``src.tui.ink.hooks``（模块拆分后日志命名不变，见
#   _hooks_core.py 注释）。
_logger = logging.getLogger("src.tui.ink.hooks")


def set_input_router_callback(cb: Callable[[Any], None] | None, ctx: "HookContext | None" = None) -> None:
    """注入 input router 发布回调（session 注入，消费端接线 InputDispatcher）。

    ★ 多会话隔离（P0 架构修复）：``ctx`` 非 None 时写入指定会话上下文；
    None 时写入当前激活上下文（兼容旧调用契约）。
    """
    target = ctx if ctx is not None else current_context()
    target.input_router_callback = cb


def _publish_input_router(router, ctx: "HookContext | None" = None) -> None:
    """发布 composite input router（reconciler 每帧调用）。

    ``ctx`` 非 None 时使用指定会话上下文的回调（``Reconciler`` 传自己的
    上下文，多会话互不干扰）；None 时用当前激活上下文（兼容旧调用）。
    """
    target = ctx if ctx is not None else current_context()
    cb = target.input_router_callback
    if cb is not None:
        try:
            cb(router)
        except Exception:
            _logger.debug("input router 发布异常", exc_info=True)


def use_fullscreen(is_active: bool = True) -> None:
    """声明当前组件为**模态全屏视图**（独占键盘输入，2026-08-17 通用机制）。

    React Ink 生态无对应物（模态全屏为本框架扩展）。激活期间
    （``is_active=True``）：
      - 组件自身 use_input handler 仍优先消费（导航/关闭等按键）；
      - 全部 use_input **未消费**的事件被 input router **吞掉**（返回 True）
        → InputDispatcher 跳过旧回调路径（buffer_editor 输入缓冲）→ 字符/
        Enter 等不落入输入缓冲（杜绝「打开时看不见的输入」误提交/误编辑）。
        吞掉判定与 hook 注册顺序无关——始终在**全部** use_input handler
        之后（router 实现保证，见 reconciler）。
    ``is_active=False`` 时 hook 不参与路由（零影响——组件非全屏渲染/已关闭）。

    配合 App 全屏视图注册表（``app.FULLSCREEN_VIEWS``，按
    ``model.fullscreen`` 整屏渲染）使用：组件渲染即声明，关闭（Esc/Ctrl+H
    等由组件 use_input 自行处理）后下一帧组件不在树中、hook 自动消失。

    ★ 通用化（2026-08-17）：实现委托 ``use_modal``（模态输入接管与全屏
    无关——底部视图同样需要）；本函数保持历史语义名（全屏视图专用）。

    ★ 窗口约束（2026-08-17 review 方向）：本 hook 经 reconciler 每帧构建
    router 生效——打开/关闭全屏视图后，**当前渲染帧内**（≤1 帧周期 ≈100ms）
    的剩余输入仍沿用旧 router（打开侧：尚未吞掉；关闭侧：仍被吞掉）。属
    渲染循环架构固有窗口（与 user_select/editmsg 等所有状态切换一致），
    下一帧渲染后即收敛；toggle 回调已请求立即重绘以最短化窗口。

    用法（全屏视图组件，与 use_input 同源激活）：
        use_input(_handle, active)
        use_fullscreen(active)

    Args:
        is_active: 是否处于模态全屏激活态（默认 True）。

    Returns:
        None（与 use_input 一致）。
    """
    return use_modal(bool(is_active))


def use_modal(is_active: bool = True) -> None:
    """声明当前组件为**模态视图**（独占键盘输入，通用机制）。

    模态底部视图通用机制（2026-08-17）：user_select 从底部区常规成员
    独立为「模态底部视图」（App ``BOTTOM_VIEWS`` 注册表 + ``model.bottom_view``
    状态驱动）——激活时底部区只渲染该视图、状态栏/输入区不渲染（「打开时
    底部框不显示，弹窗在原来底部框位置独立显示」）；未消费按键必须被吞掉
    （输入区已不渲染，字符/Enter 落入输入缓冲会「看不见地」改变用户输入）。

    语义与 use_fullscreen 完全一致（同一 hook 节点类型——``_next_hook``
    按下标复用须类型一致，二者在同一组件内不可混用同一 hook 位）：
      - 组件自身 use_input handler 仍优先消费（导航/确认/取消等按键）；
      - 全部 use_input **未消费**的事件被 input router **吞掉**（返回 True）
        → InputDispatcher 跳过旧回调路径（buffer_editor 输入缓冲）→ 字符/
        Enter 等不落入输入缓冲；
      - interrupt（Ctrl+C）生产路径不进 router（``_interrupt_routable``
        默认 False）→ 直接中断（工具执行可被 Ctrl+C 中断，弹窗 Ctrl+C
        放行语义保持）。
    ``is_active=False`` 时 hook 不参与路由（零影响——组件非模态渲染/已关闭）。

    用法（底部视图组件，与 use_input 同源激活）：
        use_input(_handle, active)
        use_modal(active)

    Args:
        is_active: 是否处于模态激活态（默认 True）。

    Returns:
        None（与 use_input 一致）。
    """
    hook = _next_hook(FullscreenHook, bool(is_active))
    hook.is_active = bool(is_active)
    return None


def use_input(
    handler: Callable[[Any], bool],
    options: "bool | dict | None" = None,
) -> None:
    """React useInput 等价物（与 react-ink useInput(inputHandler, {isActive}) 对齐）。

    调用形态（兼容新旧两种签名）：
      - ``use_input(handler)``——默认激活；
      - ``use_input(handler, is_active)``——第二参为 bool（旧签名，等价
        ``{"isActive": is_active}``）；
      - ``use_input(handler, {"isActive": bool, "mask": str|None})``——
        React Ink 风格 options 字典。

    options（dict 形态）：
      - ``isActive``（bool，默认 True）：是否参与输入路由；False 时 hook
        不参与（不消费）。
      - ``mask``（str | None，默认 None）：输入掩码（React Ink 生态 password
        语义）——非 None 时，本 hook 收到的可打印输入（kind=="char"）以
        ``mask * len(input)`` 替代后再传给 handler（与 ink-text-input 的
        ``mask.repeat(value.length)`` 显示掩码公式一致）；掩码只影响本 hook
        的输入参数，其他 hook 与事件本身不受影响。典型用途：密码输入防
        handler 接触明文。

    Args:
        handler: 按键处理回调，签名 ``(event) -> bool``——返回 True 表示消费
            事件（跳过旧回调路径）；False/异常放行（走旧路径）。也兼容
            React Ink 生态签名 ``(input, key) -> bool``（handler 接受 2+ 参数
            时自动适配——input 为可打印字符串，key 为按键信息字典）。
        options: bool（旧 is_active）或 dict（``{"isActive", "mask"}`）。

    Returns:
        None（与 react-ink 一致）。
    """
    if isinstance(options, dict):
        is_active = bool(options.get("isActive", True))
        mask = options.get("mask")
    else:
        is_active = True if options is None else bool(options)
        mask = None
    hook = _next_hook(InputHook, handler, is_active, mask)
    hook.handler = _make_compat_handler(handler)
    hook.is_active = is_active
    hook.mask = mask
    return None


#: use_input 兼容包装缓存（**handler 对象** → 包装；仅普通函数缓存，MagicMock
#: 等动态对象回退每次解析——inspect.signature 开销可接受）。
#: ★ 键为 handler 对象本身（架构修复）：此前以 ``id(handler)`` 为键——id 在
#:   handler 被 GC 后可能被新对象复用，若新 handler 恰好命中旧条目则返回**错误
#:   的包装闭包**（P3-1 靠 ``is`` 二次校验兜底，仍依赖评审纪律）。以对象为键
#:   后键持有强引用（原 value tuple 本就强引用 handler，行为等价）——id 复用
#:   风险从根上消失。
#: ★ P2-1（review 方向）：**LRU 淘汰**——访问命中 ``move_to_end``、超上限
#:   ``popitem(last=False)`` 淘汰最久未访问项，防临时闭包（列表推导内 lambda）
#:   无限累积。
_compat_handler_cache: "OrderedDict[Callable, tuple[Callable, Callable]]" = OrderedDict()

#: 兼容包装缓存上限（P2-1 LRU 淘汰阈值；超限淘汰最久未访问项）
_COMPAT_CACHE_MAX = 512


def _cache_get(handler: Callable):
    """读取兼容包装缓存（对象作键；不可哈希/不匹配返回 None）。

    ``cached[0] is handler`` 二次校验：handler 若定义了值语义 ``__eq__``，
    ``dict.get`` 可能命中「相等但非同一对象」的条目——引用不匹配时视作 miss
    （调用方重新计算并覆盖该键）。
    """
    try:
        cached = _compat_handler_cache.get(handler)
    except TypeError:
        return None
    if cached is None or cached[0] is not handler:
        return None
    try:
        _compat_handler_cache.move_to_end(handler)
    except KeyError:
        pass
    return cached[1]


def _cache_put(handler: Callable, wrapped: Callable) -> None:
    """写入兼容包装缓存（不可哈希对象静默跳过——不缓存不影响正确性）。"""
    try:
        hash(handler)
    except TypeError:
        return
    if len(_compat_handler_cache) >= _COMPAT_CACHE_MAX:
        _compat_handler_cache.popitem(last=False)
    try:
        _compat_handler_cache[handler] = (handler, wrapped)
    except TypeError:
        pass


def clear_compat_handler_cache() -> None:
    """清空 use_input 兼容包装缓存（★ P3 review：会话重置入口）。

    缓存为模块级全局（强引用 handler 与包装闭包），跨会话/测试共享——
    提供显式清空入口释放驻留引用（避免长期运行/多次会话后累积持有已卸载
    组件的 handler）。
    """
    _compat_handler_cache.clear()


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
    # MagicMock 等动态对象：不缓存（getattr 自动创建属性会误判命中）。
    # ★ P3（review）：``functools.partial`` / 可调用实例（无 ``__name__``）仍
    #   可经 ``inspect.signature`` 适配双参签名——修复前一律直接返回，双参
    #   partial handler 被按单参调用（key 信息丢失）。partial/实例不缓存
    #   （id 复用风险低但无 __name__ 稳定性保证），仅 MagicMock 等无稳定签名
    #   的动态对象保持既有「直接返回」行为。
    dynamic = getattr(handler, "__name__", None) is None and not isinstance(handler, type)
    if dynamic and not isinstance(handler, functools.partial):
        return handler
    cacheable = not dynamic
    if cacheable:
        cached = _cache_get(handler)
        if cached is not None:
            return cached
    try:
        import inspect as _inspect
        sig = _inspect.signature(handler)
        n = sum(
            1 for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        # ★ P3（review 2026-08-22）：VAR_POSITIONAL（*args）handler 被误判为
        #   单参——*args 能接收 (input, key) 双参，按双参适配（对齐 React Ink 生态
        #   handler 语义；KEYWORD_ONLY 参数难以按位置适配，不在此处理）。
        if any(p.kind == p.VAR_POSITIONAL for p in sig.parameters.values()):
            n = 2
    except (TypeError, ValueError):
        n = 1
    if n < 2:
        # ★ P2（review 2026-08-22）：单参 handler 也写入缓存——修复前直接
        #   return 不写入，每帧重跑 inspect.signature（与注释「零额外开销」
        #   不符）；写入后后续帧命中缓存零签名探测。
        if cacheable:
            _cache_put(handler, handler)
        return handler

    def _wrapped(event) -> bool:
        return bool(handler(_event_input(event), _event_key(event)))

    # 仅缓存普通函数（有 __name__）；P2-1 LRU 淘汰（超上限弹出最久未访问项）。
    if cacheable:
        _cache_put(handler, _wrapped)
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
    # kitty 键盘协议修饰位（-1/负数 = 非 kitty 事件 → 相关字段全 False）
    kitty_bits = getattr(event, "kitty_bits", -1)
    if not isinstance(kitty_bits, int) or kitty_bits < 0:
        kitty_bits = 0
    # ★ P1-1（review 方向）：CSI-u modifier 编码 = 1 + shift*1 + alt*2 +
    #   ctrl*4（Shift=1, Alt=2, Ctrl=4 位标志）——2=Shift, 3=Alt,
    #   4=Shift+Alt, 5=Ctrl, 6=Shift+Ctrl, 7=Alt+Ctrl, 8=Shift+Alt+Ctrl。
    #   修复前 ``meta: modifier in (3, 6)`` 把 6（Shift+Ctrl，无 Alt）误判为
    #   meta 且漏 4/7/8；``ctrl`` 漏 7/8；``shift`` 漏 7/8。现按位语义：
    #   meta=含 Alt 位（3,4,7,8）、ctrl=含 Ctrl 位（5,6,7,8）、
    #   shift=含 Shift 位（2,4,6,8）。
    return {
        "leftArrow": kind == "arrow_left",
        "rightArrow": kind == "arrow_right",
        "upArrow": kind == "arrow_up",
        "downArrow": kind == "arrow_down",
        "return": kind == "enter",
        "escape": kind == "escape",
        # ★ 官方 React Ink：Ctrl+C 的 key 为 {ctrl: true}（exitOnCtrlC=False
        #   时传给 handler）。interrupt 事件（0x03 Ctrl+C / 双 Esc）默认不进
        #   router（生产中断路径不变）；仅 render() 独立会话 exitOnCtrlC=False
        #   时经 ``_interrupt_routable`` 放行进 router——此时 handler 按
        #   ctrl=True 识别 Ctrl+C（与官方语义对齐）。
        # ★ P2（review 修复）：ctrl/shift 兼含 kitty 位掩码——CSI-u Ctrl+字母
        #   （如 ``\x1b[97;5u``）经映射分支重写 kind（home/csi_u）且 modifier
        #   被置 0，仅靠 modifier 会丢失 ctrl 标志；kitty_bits 的 ctrl 位（4）
        #   / shift 位（1）补齐（meta 同理，见下）。
        "ctrl": kind == "ctrl_key" or kind == "interrupt"
                or modifier in (5, 6, 7, 8) or bool(kitty_bits & 0b100),
        "shift": modifier in (2, 4, 6, 8) or bool(kitty_bits & 0b1),
        "tab": kind == "tab",
        "backspace": kind == "backspace",
        "delete": kind == "delete",
        # ★ review 方向（死分支清理）：删除 ``kind == "csi_u" and keycode in
        #   (62,)/(63,)`` 分支——keycode 62/63 是 ``>``/``?`` 的 ASCII 码，
        #   ``_input_parser._dispatch_csi`` 中 modifier=1 时已被
        #   ``32 <= keycode <= 126 → char`` 抢先映射（永不以 csi_u 到达），
        #   modifier!=1 时把 ``\x1b[62;2u``（Shift+'>'）误映射为 PageDown 属
        #   错误行为。PageUp/PageDown 正确来源：CSI-u 增强键盘协议码
        #   （57358/57359，_dispatch_csi 已映射 page_up/page_down）与传统
        #   ``\x1b[5~``/``\x1b[6~``。
        "pageDown": kind == "page_down",
        "pageUp": kind == "page_up",
        "home": kind == "home",
        "end": kind == "end",
        "meta": modifier in (3, 4, 7, 8) or bool(kitty_bits & 0b100000),
        # ★ kitty 键盘协议（React Ink v6/v7）：super/hyper/capsLock/numLock 与
        #   eventType 仅在启用 kitty 协议并收到 CSI-u 扩展序列时可用——从
        #   ``KeyEvent.kitty_bits``（修饰位掩码）与 ``KeyEvent.event_type``
        #   （press/repeat/release）读取；非 kitty 事件保持 False/None（与官方
        #   「仅 kitty 下可用」语义一致）。
        "super": bool(kitty_bits & 0b1000),
        "hyper": bool(kitty_bits & 0b10000),
        "capsLock": bool(kitty_bits & 0b1000000),
        "numLock": bool(kitty_bits & 0b10000000),
        "eventType": getattr(event, "event_type", "") or None,
    }


__all__ = [
    "set_input_router_callback",
    "_publish_input_router",
    "use_input",
    "use_fullscreen",
    "use_modal",
    "_compat_handler_cache",
    "clear_compat_handler_cache",
    "_make_compat_handler",
    "_event_input",
    "_event_key",
]
