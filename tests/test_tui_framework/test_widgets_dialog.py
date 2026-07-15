"""Dialog 控件测试。

覆盖：渲染、模态行为、键盘事件、按钮交互、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.button import Button
from tui_framework.widgets.dialog import Dialog


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestDialogBasic:
    """Dialog 基础属性测试。"""

    def test_default_values(self):
        d = Dialog()
        assert d.title == ""
        assert d.content == ""
        assert d.buttons == []
        assert d.modal is True
        assert d.width == 50

    def test_with_title_and_content(self):
        d = Dialog(title="通知", content="操作成功")
        assert d.title == "通知"
        assert d.content == "操作成功"

    def test_with_buttons(self):
        btn = Button(label="OK")
        d = Dialog(title="确认", buttons=[btn])
        assert len(d.buttons) == 1
        assert d.buttons[0] is btn

    def test_widget_inheritance(self):
        d = Dialog()
        assert hasattr(d, "focus")
        assert hasattr(d, "render")


# ── 按钮管理 ────────────────────────────────────────────


class TestDialogButtons:
    """Dialog 按钮管理测试。"""

    def test_add_button(self):
        d = Dialog()
        btn = Button(label="OK")
        d.add_button(btn)
        assert len(d.buttons) == 1

    def test_remove_button(self):
        btn = Button(label="OK")
        d = Dialog(buttons=[btn])
        d.remove_button(btn)
        assert len(d.buttons) == 0

    def test_remove_nonexistent_button(self):
        """移除不存在的按钮不报错。"""
        b1 = Button(label="OK")
        b2 = Button(label="Cancel")
        d = Dialog(buttons=[b1])
        d.remove_button(b2)  # no error
        assert len(d.buttons) == 1

    def test_buttons_setter(self):
        d = Dialog()
        d.buttons = [Button(label="OK"), Button(label="Cancel")]
        assert len(d.buttons) == 2


# ── 键盘事件 ────────────────────────────────────────────


class TestDialogKeyboard:
    """Dialog 键盘事件测试。"""

    def test_escape_triggers_on_close(self):
        calls = []

        def on_close():
            calls.append(True)

        d = Dialog()
        d.on_close = on_close
        d.handle_key(_key("escape"))
        assert calls == [True]

    def test_escape_no_callback(self):
        """无 on_close 回调时 ESC 不报错。"""
        d = Dialog()
        result = d.handle_key(_key("escape"))
        assert result is True  # 事件仍被消费

    def test_enter_triggers_first_button(self):
        calls = []

        def on_click():
            calls.append(True)

        btn = Button(label="OK")
        btn.on_click = on_click
        d = Dialog(buttons=[btn])
        d.handle_key(_key("enter"))
        assert calls == [True]

    def test_enter_no_buttons(self):
        """无按钮时 Enter 不报错。"""
        d = Dialog()
        result = d.handle_key(_key("enter"))
        assert result is False  # 没有按钮，不消费

    def test_disabled_no_key(self):
        d = Dialog()
        d.disable()
        result = d.handle_key(_key("escape"))
        assert result is False


# ── 模态属性 ────────────────────────────────────────────


class TestDialogModal:
    """Dialog 模态属性测试。"""

    def test_default_modal(self):
        d = Dialog()
        assert d.modal is True

    def test_non_modal(self):
        d = Dialog(modal=False)
        assert d.modal is False

    def test_modal_still_handles_escape(self):
        """模态对话框仍然处理 ESC。"""
        calls = []

        def on_close():
            calls.append(True)

        d = Dialog(modal=True)
        d.on_close = on_close
        d.handle_key(_key("escape"))
        assert calls == [True]


# ── 渲染 ────────────────────────────────────────────────


class TestDialogRender:
    """Dialog 渲染测试。"""

    def test_render_contains_title(self):
        d = Dialog(title="确认")
        rendered = d.render()
        assert "确认" in rendered

    def test_render_contains_content(self):
        d = Dialog(title="标题", content="这是内容")
        rendered = d.render()
        assert "这是内容" in rendered

    def test_render_contains_buttons(self):
        d = Dialog(title="标题", buttons=[Button(label="OK"), Button(label="Cancel")])
        rendered = d.render()
        assert "OK" in rendered
        assert "Cancel" in rendered

    def test_render_hidden(self):
        d = Dialog(title="标题")
        d.hide()
        assert d.render() == ""

    def test_render_has_borders(self):
        d = Dialog(title="标题", content="内容")
        rendered = d.render()
        assert "┌" in rendered
        assert "┐" in rendered
        assert "└" in rendered
        assert "┘" in rendered

    def test_render_with_widget_content(self):
        """内容为 Widget 实例时 render 正确。"""
        btn = Button(label="内部按钮")
        d = Dialog(title="对话框", content=btn)
        rendered = d.render()
        assert "内部按钮" in rendered

    def test_render_empty_title(self):
        d = Dialog(title="", content="内容")
        rendered = d.render()
        assert "内容" in rendered

    def test_render_width_setting(self):
        d = Dialog(title="标题", width=30)
        assert d.width == 30

    def test_render_width_minimum(self):
        d = Dialog(title="标题", width=5)
        assert d.width == 10  # minimum

    def test_render_long_title_truncated(self):
        """长标题被截断以适应宽度。"""
        d = Dialog(title="这是一段非常长的标题文本" * 5, width=20)
        rendered = d.render()
        # 不应该崩溃
        assert "┌" in rendered


# ── 可见性 ──────────────────────────────────────────────


class TestDialogVisibility:
    """Dialog 可见性测试。"""

    def test_show_hide(self):
        d = Dialog(title="标题")
        assert d.visible is True
        d.hide()
        assert d.visible is False
        assert d.render() == ""
        d.show()
        assert d.visible is True
        assert d.render() != ""

    def test_hidden_no_key_response(self):
        calls = []

        def on_close():
            calls.append(True)

        d = Dialog(title="标题")
        d.on_close = on_close
        d.hide()
        result = d.handle_key(_key("escape"))
        assert result is False
        assert calls == []
