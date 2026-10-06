"""输入回调注册表测试（P2）。

覆盖 ``InputDispatcher._CALLBACK_ATTRS`` / ``register_callback`` /
``get_callback``：统一注册入口、未知名字防御、与既有 ``set_*_callback``
行为一致（同一属性）、映射完整（所有登记回调在实例上存在）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui._input_dispatcher import InputDispatcher


def _make_dispatcher() -> InputDispatcher:
    """构造最小 InputDispatcher（依赖桩——仅验证回调注册表行为）。"""
    return InputDispatcher(
        io=SimpleNamespace(),
        buffer_editor=SimpleNamespace(),
        parser=SimpleNamespace(),
    )


def test_register_and_get_roundtrip():
    """register_callback 写入后 get_callback 读回同一对象。"""
    disp = _make_dispatcher()
    cb = lambda: None  # noqa: E731
    disp.register_callback("interrupt", cb)
    assert disp.get_callback("interrupt") is cb
    # None 清除
    disp.register_callback("interrupt", None)
    assert disp.get_callback("interrupt") is None


def test_unknown_callback_name_raises_keyerror():
    """未知回调名抛 KeyError（防错拼静默失效）。"""
    disp = _make_dispatcher()
    with pytest.raises(KeyError):
        disp.register_callback("no_such_callback", lambda: None)
    with pytest.raises(KeyError):
        disp.get_callback("no_such_callback")


def test_register_matches_setter_attribute():
    """注册表与既有 set_* 方法指向同一属性（行为一致）。"""
    disp = _make_dispatcher()
    cb = lambda: None  # noqa: E731
    disp.set_interrupt_callback(cb)
    assert disp.get_callback("interrupt") is cb
    assert disp._interrupt_callback is cb
    # 反向：register 后 setter 的 getter 读到同一对象
    cb2 = lambda: None  # noqa: E731
    disp.register_callback("interrupt", cb2)
    assert disp._interrupt_callback is cb2


@pytest.mark.parametrize("name", [
    "special_key", "completion", "dismiss_completion", "completion_navigate",
    "auto_completion", "interrupt", "kill_background", "enter_append_history",
    "input_hook_router", "key_pressed", "reverse_search", "active_status",
    "clear_screen", "trace_toggle", "mouse_fallback",
])
def test_all_registered_names_present_on_instance(name):
    """映射登记的所有回调名在构造后的实例上可用（映射与 __init__ 一致）。"""
    disp = _make_dispatcher()
    assert name in InputDispatcher._CALLBACK_ATTRS
    # 读取不抛（__init__ 已初始化对应属性为 None）
    assert disp.get_callback(name) is None


def test_callback_attrs_values_are_initialized_fields():
    """映射值均为实例上已存在的属性（防映射指向未初始化字段）。"""
    disp = _make_dispatcher()
    for name, attr in InputDispatcher._CALLBACK_ATTRS.items():
        assert hasattr(disp, attr), f"{name} → {attr} 未在实例上初始化"
