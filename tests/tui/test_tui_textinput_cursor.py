"""TextInput 光标渲染回归测试（P0：光标位置字符重复渲染）。

覆盖：光标不在文本末尾时，光标所在字符不应被重复渲染。
修复前 ``after = display[disp_eff:]`` 含 ``display[disp_eff]``，而
``cursor_ch = display[disp_eff]`` 也渲染该字符，导致 ``value="abc"`` 光标在
中间时显示 ``"abcc"``。修复后 ``after`` 从 ``disp_eff+1`` 偏移。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.ink import h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.layout import layout_tree
from src.tui.ink import components as _components
from src.tui.ink.fiber import InputHook
from src.tui.ink.widgets._text_input import TextInput


def _render(rec, root, props, width=80):
    rec.render(root, h(TextInput, props), width, 24)
    layout_tree(root, width)
    return _components.render_frame(root, width)


def _find_input_handler(fiber):
    def _walk(f):
        if f is None:
            return None
        for hook in getattr(f, "hooks", None) or []:
            if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
                return hook.handler
        r = _walk(f.child)
        if r is not None:
            return r
        return _walk(f.sibling)
    return _walk(fiber)


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _frame_text(frame) -> str:
    return "".join("".join(r.text for r in ln.runs) for ln in frame.lines)


def _arraw_left_twice(props):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    _render(rec, root, props)
    handler = _find_input_handler(root.child)
    assert handler is not None, "TextInput 应注册 use_input handler"
    assert handler(_ev("arrow_left")) is True
    assert handler(_ev("arrow_left")) is True
    frame = _render(rec, root, props)
    return _frame_text(frame)


def test_cursor_mid_not_duplicated():
    """光标在 'bc' 之间（cursor=2）时，文本应恰好为 'abc'。"""
    text = _arraw_left_twice({"value": "abc", "focus": True, "showCursor": True})
    assert text == "abc"


def test_cursor_at_end_keeps_placeholder_space():
    """光标在末尾（cursor=3）时显示 'abc '（末尾空格作光标提示）。"""
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    frame = _render(rec, root, {"value": "abc", "focus": True, "showCursor": True})
    assert _frame_text(frame) == "abc "


def test_single_char_cursor_mid():
    """value='x' 光标回到开头（cursor=0）时显示 'x'（光标覆盖单字符）。"""
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    _render(rec, root, {"value": "x", "focus": True, "showCursor": True})
    handler = _find_input_handler(root.child)
    # 初始 cursor=1；home → cursor=0
    assert handler(_ev("home")) is True
    frame = _render(rec, root, {"value": "x", "focus": True, "showCursor": True})
    assert _frame_text(frame) == "x"


def test_mask_cursor_mid_not_duplicated():
    """mask='*' 光标在中间时，'*' 不被重复渲染。"""
    text = _arraw_left_twice({"value": "abc", "mask": "*", "focus": True, "showCursor": True})
    assert text == "***"
