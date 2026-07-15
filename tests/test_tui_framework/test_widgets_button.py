"""Button 控件测试。

覆盖：渲染、键盘事件、状态切换、回调、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.button import Button, BUTTON_STYLES


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestButtonBasic:
    """Button 基础属性测试。"""

    def test_default_values(self):
        b = Button()
        assert b.label == ""
        assert b.style == "secondary"
        assert b.disabled is False

    def test_custom_label_and_style(self):
        b = Button(label="OK", style="primary")
        assert b.label == "OK"
        assert b.style == "primary"

    def test_disabled_init(self):
        b = Button(label="OK", disabled=True)
        assert b.disabled is True

    def test_widget_inheritance(self):
        b = Button()
        assert hasattr(b, "focus")
        assert hasattr(b, "render")


# ── 交互行为 ────────────────────────────────────────────


class TestButtonInteraction:
    """Button 交互测试。"""

    def test_space_triggers_on_click(self):
        calls = []

        def on_click():
            calls.append(True)

        b = Button(label="OK")
        b.on_click = on_click
        result = b.handle_key(_key("space"))
        assert result is True
        assert calls == [True]

    def test_enter_triggers_on_click(self):
        calls = []

        def on_click():
            calls.append(True)

        b = Button(label="OK")
        b.on_click = on_click
        result = b.handle_key(_key("enter"))
        assert result is True
        assert calls == [True]

    def test_disabled_no_on_click(self):
        calls = []

        def on_click():
            calls.append(True)

        b = Button(label="OK", disabled=True)
        b.on_click = on_click
        result = b.handle_key(_key("enter"))
        assert result is False
        assert calls == []

    def test_disabled_no_space(self):
        calls = []

        def on_click():
            calls.append(True)

        b = Button(label="OK", disabled=True)
        b.on_click = on_click
        result = b.handle_key(_key("space"))
        assert result is False
        assert calls == []

    def test_other_key_ignored(self):
        b = Button(label="OK")
        result = b.handle_key(_key("a"))
        assert result is False

    def test_enable_disable_cycle(self):
        b = Button()
        assert b.disabled is False
        b.disable()
        assert b.disabled is True
        b.enable()
        assert b.disabled is False


# ── 渲染 ────────────────────────────────────────────────


class TestButtonRender:
    """Button 渲染测试。"""

    def test_render_contains_label(self):
        b = Button(label="OK")
        rendered = b.render()
        assert "OK" in rendered

    def test_render_format(self):
        """渲染格式: [ OK ]。"""
        b = Button(label="OK")
        rendered = b.render()
        # 去掉 ANSI 序列后检查格式
        from tui_framework.core.ansi_utils import strip_ansi
        clean = strip_ansi(rendered)
        assert clean == "[ OK ]"

    def test_render_hidden(self):
        b = Button(label="OK")
        b.hide()
        assert b.render() == ""

    def test_render_empty_label(self):
        b = Button(label="")
        rendered = b.render()
        from tui_framework.core.ansi_utils import strip_ansi
        clean = strip_ansi(rendered)
        assert "[  ]" in clean

    def test_render_disabled(self):
        b = Button(label="OK", disabled=True)
        rendered = b.render()
        # disabled 应该使用 muted 颜色
        assert "\033[38;5;237m" in rendered

    def test_render_focused(self):
        b = Button(label="OK")
        b.focus()
        rendered = b.render()
        from tui_framework.core.ansi_utils import strip_ansi
        clean = strip_ansi(rendered)
        assert "OK" in clean


# ── 样式变体 ────────────────────────────────────────────


class TestButtonStyles:
    """Button 样式变体测试。"""

    def test_all_styles_exist(self):
        """所有预设样式均存在。"""
        expected = {"primary", "secondary", "danger", "warning", "info", "muted"}
        assert set(BUTTON_STYLES.keys()) >= expected

    def test_primary_style(self):
        b = Button(label="OK", style="primary")
        rendered = b.render()
        assert "\033[38;5;41m" in rendered  # 绿色

    def test_danger_style(self):
        b = Button(label="OK", style="danger")
        rendered = b.render()
        assert "\033[38;5;196m" in rendered  # 红色

    def test_invalid_style_fallback(self):
        """未知样式回退到 secondary。"""
        b = Button(label="OK", style="nonexistent")
        rendered = b.render()
        assert "\033[38;5;242m" in rendered  # 中灰 (secondary)

    def test_muted_style(self):
        b = Button(label="OK", style="muted")
        rendered = b.render()
        assert "\033[38;5;237m" in rendered


# ── 可见性 ──────────────────────────────────────────────


class TestButtonVisibility:
    """Button 可见性测试。"""

    def test_show_hide(self):
        b = Button(label="OK")
        assert b.visible is True
        b.hide()
        assert b.visible is False
        assert b.render() == ""
        b.show()
        assert b.visible is True
        assert "OK" in b.render()

    def test_hidden_ignores_key(self):
        called = []

        def on_click():
            called.append(True)

        b = Button(label="OK")
        b.on_click = on_click
        b.hide()
        result = b.handle_key(_key("enter"))
        assert result is False
        assert called == []
