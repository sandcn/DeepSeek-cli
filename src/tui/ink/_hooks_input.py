"""hooks 输入族 — use_input + input router 发布 + React Ink (input, key) 适配。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——输入相关 hooks
独立成模块（use_input 的 InputHook 注册 / router 发布 / 双签名适配），供
reconciler（``_publish_input_router``）/session（``set_input_router_callback``）
与组件库（use_input）共享。依赖 ``_hooks_core``（``_next_hook`` 基础设施）。

依赖方向：本模块 → _hooks_core / fiber；不反向依赖。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .fiber import InputHook
from ._hooks_core import _next_hook
# ★ 模块级可变状态唯一真源在 hooks.py 门面（见 _hooks_core.py 注释）。
from src.tui.ink import hooks as _hooks_module

# ★ logger 名保持 ``src.tui.ink.hooks``（模块拆分后日志命名不变，见
#   _hooks_core.py 注释）。
_logger = logging.getLogger("src.tui.ink.hooks")


def set_input_router_callback(cb: Callable[[Any], None] | None) -> None:
    """注入 input router 发布回调（session 注入，消费端接线 InputDispatcher）。"""
    _hooks_module._input_router_callback = cb


def _publish_input_router(router) -> None:
    """发布 composite input router（reconciler 每帧调用）。"""
    if _hooks_module._input_router_callback is not None:
        try:
            _hooks_module._input_router_callback(router)
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
        "meta": modifier in (3, 6),
        "super": False,
        "hyper": False,
        "capsLock": False,
        "numLock": False,
        "eventType": None,
    }


__all__ = [
    "set_input_router_callback",
    "_publish_input_router",
    "use_input",
    "_compat_handler_cache",
    "_make_compat_handler",
    "_event_input",
    "_event_key",
]
