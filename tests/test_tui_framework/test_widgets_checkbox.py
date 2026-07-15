"""Checkbox 控件测试。

覆盖：渲染、勾选切换、键盘事件、回调、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.checkbox import Checkbox


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestCheckboxBasic:
    """Checkbox 基础属性测试。"""

    def test_default_values(self):
        cb = Checkbox()
        assert cb.label == ""
        assert cb.checked is False
        assert cb.disabled is False

    def test_custom_label_and_checked(self):
        cb = Checkbox(label="同意", checked=True)
        assert cb.label == "同意"
        assert cb.checked is True

    def test_widget_inheritance(self):
        cb = Checkbox()
        assert hasattr(cb, "focus")
        assert hasattr(cb, "blur")


# ── 勾选切换 ────────────────────────────────────────────


class TestCheckboxToggle:
    """Checkbox 勾选切换测试。"""

    def test_toggle_unchecked_to_checked(self):
        cb = Checkbox()
        cb.toggle()
        assert cb.checked is True

    def test_toggle_checked_to_unchecked(self):
        cb = Checkbox(checked=True)
        cb.toggle()
        assert cb.checked is False

    def test_check(self):
        cb = Checkbox()
        cb.check()
        assert cb.checked is True
        cb.check()  # 幂等
        assert cb.checked is True

    def test_uncheck(self):
        cb = Checkbox(checked=True)
        cb.uncheck()
        assert cb.checked is False
        cb.uncheck()  # 幂等
        assert cb.checked is False

    def test_property_setter(self):
        cb = Checkbox()
        cb.checked = True
        assert cb.checked is True
        cb.checked = False
        assert cb.checked is False

    def test_truthy_values_coerced(self):
        """checked setter 将值转为 bool。"""
        cb = Checkbox()
        cb.checked = 1
        assert cb.checked is True
        cb.checked = 0
        assert cb.checked is False


# ── 键盘事件 ────────────────────────────────────────────


class TestCheckboxKeyboard:
    """Checkbox 键盘事件测试。"""

    def test_space_toggles(self):
        cb = Checkbox()
        result = cb.handle_key(_key("space"))
        assert result is True
        assert cb.checked is True

    def test_enter_toggles(self):
        cb = Checkbox(checked=True)
        result = cb.handle_key(_key("enter"))
        assert result is True
        assert cb.checked is False

    def test_other_key_ignored(self):
        cb = Checkbox()
        result = cb.handle_key(_key("a"))
        assert result is False
        assert cb.checked is False

    def test_disabled_no_toggle(self):
        cb = Checkbox()
        cb.disable()
        result = cb.handle_key(_key("space"))
        assert result is False
        assert cb.checked is False


# ── 回调 ────────────────────────────────────────────────


class TestCheckboxCallbacks:
    """Checkbox 回调测试。"""

    def test_on_change_called_on_toggle(self):
        calls = []

        def on_change(checked):
            calls.append(checked)

        cb = Checkbox()
        cb.on_change = on_change
        cb.toggle()
        assert calls == [True]
        cb.toggle()
        assert calls == [True, False]

    def test_on_change_not_called_when_no_change(self):
        calls = []

        def on_change(checked):
            calls.append(checked)

        cb = Checkbox()
        cb.on_change = on_change
        cb.check()  # False → True
        assert calls == [True]
        calls.clear()
        cb.check()  # True → True (no change)
        assert calls == []

    def test_on_change_called_on_key(self):
        calls = []

        def on_change(checked):
            calls.append(checked)

        cb = Checkbox()
        cb.on_change = on_change
        cb.handle_key(_key("space"))
        assert calls == [True]


# ── 渲染 ────────────────────────────────────────────────


class TestCheckboxRender:
    """Checkbox 渲染测试。"""

    def test_render_unchecked(self):
        cb = Checkbox(label="选项")
        rendered = cb.render()
        assert "选项" in rendered
        assert "✓" not in rendered

    def test_render_checked(self):
        cb = Checkbox(label="选项", checked=True)
        rendered = cb.render()
        assert "选项" in rendered
        assert "✓" in rendered

    def test_render_hidden(self):
        cb = Checkbox(label="x")
        cb.hide()
        assert cb.render() == ""

    def test_render_empty_label(self):
        cb = Checkbox()
        rendered = cb.render()
        from tui_framework.core.ansi_utils import strip_ansi
        clean = strip_ansi(rendered)
        # 应有 [ ] 或 [✓] 结构
        assert "[" in clean
        assert "]" in clean

    def test_render_disabled(self):
        cb = Checkbox(label="x", checked=True)
        cb.disable()
        rendered = cb.render()
        assert "\033[38;5;237m" in rendered  # muted

    def test_render_checked_color(self):
        cb = Checkbox(label="x", checked=True)
        rendered = cb.render()
        assert "\033[38;5;41m" in rendered  # success green


# ── 可见性 ──────────────────────────────────────────────


class TestCheckboxVisibility:
    """Checkbox 可见性测试。"""

    def test_show_hide(self):
        cb = Checkbox(label="x")
        assert cb.visible is True
        cb.hide()
        assert cb.visible is False
        assert cb.render() == ""
        cb.show()
        assert cb.visible is True
        assert cb.render() != ""

    def test_hidden_ignores_key(self):
        cb = Checkbox(label="x")
        cb.hide()
        result = cb.handle_key(_key("space"))
        assert result is False
        assert cb.checked is False
