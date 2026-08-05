"""hooks 焦点族 — useFocus / useFocusManager + 焦点仲裁状态。

模块边界（2026-08-05 架构优化）：从 ``ink/hooks.py`` 拆分——焦点管理
（可聚焦 id 收集 / 全局激活 / 编程切换）独立成模块，供 reconciler
（``_reset_focus_ids``/``_focus_previous``/``_focus_next``）、组件库
（useFocus/useFocusManager）、Tab/Shift+Tab 路由共享。依赖 ``_hooks_core``
（``_current``/``_schedule``/``HookStateError``）。

依赖方向：本模块 → _hooks_core / fiber；不反向依赖。
"""

from __future__ import annotations

from .fiber import Fiber, InputHook
from ._hooks_core import (
    _current,
    _schedule,
    HookStateError,
)
# ★ 模块级可变状态唯一真源在 hooks.py 门面（见 _hooks_core.py 注释）。
from src.tui.ink import hooks as _hooks_module


# ═══════════════════════════════════════════════════════════
# 焦点管理（React Ink v6 useFocus / useFocusManager）
# ═══════════════════════════════════════════════════════════
# 状态（_focus_enabled/_focus_active/_focus_ids/_focus_id_seq）唯一真源在
# hooks.py 门面；本模块经 ``_hooks_module._focus_*`` 访问最新值。


def _reset_focus_ids() -> None:
    """每帧渲染前重置可聚焦 id 收集列表（reconciler.render 调用）。"""
    _hooks_module._focus_ids.clear()


def _register_focus_id(fid: str) -> None:
    """渲染期注册可聚焦 id（useFocus 调用）。"""
    if fid not in _hooks_module._focus_ids:
        _hooks_module._focus_ids.append(fid)


def _resolve_focus_id(fiber: Fiber) -> str:
    """为未指定 id 的 useFocus 分配稳定自动 id（挂在 fiber 上，复用不重分配）。"""
    fid = getattr(fiber, "_focus_id", None)
    if fid is None:
        fid = f"__focus_{next(_hooks_module._focus_id_seq)}__"
        fiber._focus_id = fid
    return fid


def _focus_next() -> None:
    """切换到下一个可聚焦组件（Tab）。React Ink useFocusManager.focusNext。"""
    focus_ids = _hooks_module._focus_ids
    focus_active = _hooks_module._focus_active
    if not focus_ids:
        return
    if focus_active is None or focus_active not in focus_ids:
        _hooks_module._focus_active = focus_ids[0]
    else:
        idx = focus_ids.index(focus_active)
        _hooks_module._focus_active = focus_ids[(idx + 1) % len(focus_ids)]
    _schedule()


def _focus_previous() -> None:
    """切换到上一个可聚焦组件（Shift+Tab）。React Ink useFocusManager.focusPrevious。"""
    focus_ids = _hooks_module._focus_ids
    focus_active = _hooks_module._focus_active
    if not focus_ids:
        return
    if focus_active is None or focus_active not in focus_ids:
        _hooks_module._focus_active = focus_ids[-1]
    else:
        idx = focus_ids.index(focus_active)
        _hooks_module._focus_active = focus_ids[(idx - 1) % len(focus_ids)]
    _schedule()


def _focus_to(fid: str) -> None:
    """切换到指定 id 的组件。React Ink useFocusManager.focus(id)。"""
    if fid in _hooks_module._focus_ids:
        _hooks_module._focus_active = fid
        _schedule()


def _focus_enable() -> None:
    """启用全局焦点管理（默认启用）。React Ink useFocusManager.enableFocus。"""
    if not _hooks_module._focus_enabled:
        _hooks_module._focus_enabled = True
        _schedule()


def _focus_disable() -> None:
    """禁用全局焦点管理；当前激活组件失去焦点。React Ink useFocusManager.disableFocus。"""
    _hooks_module._focus_enabled = False
    if _hooks_module._focus_active is not None:
        _hooks_module._focus_active = None
        _schedule()


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
        if auto_focus and _hooks_module._focus_active is None:
            _hooks_module._focus_active = fid
    is_focused = bool(
        is_active and _hooks_module._focus_enabled and fid == _hooks_module._focus_active
    )
    for hook in reversed(fiber.hooks):
        if isinstance(hook, InputHook):
            if not is_active:
                hook.is_active = False
            hook.focused = is_focused
            return {"isFocused": is_focused}
    raise HookStateError(
        "useFocus 必须在 use_input 之后调用（当前 fiber 未注册 InputHook）"
    )


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
        "activeId": _hooks_module._focus_active,
    }


__all__ = [
    "_reset_focus_ids",
    "_register_focus_id",
    "_resolve_focus_id",
    "_focus_next",
    "_focus_previous",
    "_focus_to",
    "_focus_enable",
    "_focus_disable",
    "useFocus",
    "useFocusManager",
]
