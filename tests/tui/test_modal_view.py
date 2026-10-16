"""模态视图通用骨架测试（P2）。

覆盖 ``src/tui/app/_modal_view.py``：统一关闭键判定、不可见占位元素、
模态声明入口（全屏 / 底部）。骨架供 config_view / plugin_view / trace_view /
trace_tools_view / user_select / editmsg_select 复用。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.app._modal_view import (
    empty_modal_frame,
    is_modal_close_key,
    use_modal_scope,
)
from src.tui.ink import TEXT, h
from src.tui.ink.reconciler import Reconciler


# ═══════════════════════════════════════════════════════════
# is_modal_close_key
# ═══════════════════════════════════════════════════════════


def test_escape_is_close_key():
    assert is_modal_close_key(SimpleNamespace(kind="escape", char="")) is True


def test_ctrl_h_is_close_key():
    """Ctrl+H 解析为 ctrl_key + char ``\\x08``（与 Backspace 的 0x7f 区分）。"""
    assert is_modal_close_key(SimpleNamespace(kind="ctrl_key", char="\x08")) is True


def test_other_keys_not_close_key():
    assert is_modal_close_key(SimpleNamespace(kind="char", char="a")) is False
    assert is_modal_close_key(SimpleNamespace(kind="enter", char="")) is False
    assert is_modal_close_key(SimpleNamespace(kind="backspace", char="")) is False
    # ctrl_key 但非 \x08（如 Ctrl+L）不是关闭键
    assert is_modal_close_key(SimpleNamespace(kind="ctrl_key", char="\x0c")) is False
    # 缺失属性（畸形事件）安全返回 False
    assert is_modal_close_key(SimpleNamespace()) is False


# ═══════════════════════════════════════════════════════════
# empty_modal_frame
# ═══════════════════════════════════════════════════════════


def test_empty_modal_frame_is_empty_text_element():
    """不可见占位为零高度空 TEXT（保持 fiber 树结构稳定）。"""
    el = empty_modal_frame()
    assert el.type == TEXT
    assert el.props.get("children") == ""
    assert not el.children


# ═══════════════════════════════════════════════════════════
# use_modal_scope（组件上下文）
# ═══════════════════════════════════════════════════════════


def _FullscreenComp(props):
    use_modal_scope(bool(props.get("visible")), fullscreen=True)
    return h(TEXT, {"children": "fs"})


def _BottomComp(props):
    use_modal_scope(bool(props.get("visible")), fullscreen=False)
    return h(TEXT, {"children": "bt"})


def _find_hook(fiber, cls_name: str):
    def _walk(f):
        if f is None:
            return None
        for hook in getattr(f, "hooks", None) or []:
            if type(hook).__name__ == cls_name:
                return hook
        r = _walk(f.child)
        if r is not None:
            return r
        return _walk(f.sibling)
    return _walk(fiber)


def _render(component, props):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(component, props), 80, 24)
    return rec, root


def test_use_modal_scope_fullscreen_registers_fullscreen_hook():
    """fullscreen=True → FullscreenHook（模态全屏输入接管）。"""
    rec, root = _render(_FullscreenComp, {"visible": True})
    hook = _find_hook(root.child, "FullscreenHook")
    assert hook is not None
    assert hook.is_active is True


def test_use_modal_scope_bottom_registers_modal_hook_inactive():
    """fullscreen=False → use_modal 节点；visible=False 时 hooks 无条件注册但不激活。"""
    rec, root = _render(_BottomComp, {"visible": False})
    hook = _find_hook(root.child, "FullscreenHook")
    assert hook is not None
    assert hook.is_active is False


def test_use_modal_scope_toggles_without_hook_order_error():
    """visible 翻转（False→True）不违反 Rules of Hooks（hook 无条件注册）。"""
    rec, root = _render(_FullscreenComp, {"visible": False})
    # 同一 root 再次渲染（visible=True）——不应抛 hook 顺序异常
    rec.render(root, h(_FullscreenComp, {"visible": True}), 80, 24)
    hook = _find_hook(root.child, "FullscreenHook")
    assert hook is not None and hook.is_active is True
