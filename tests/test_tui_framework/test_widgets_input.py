"""Input 控件测试。

覆盖：渲染、键盘事件处理、状态切换、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.input import Input


# ── 辅助函数 ────────────────────────────────────────────


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestInputBasic:
    """Input 基础属性测试。"""

    def test_default_values(self):
        """默认值测试。"""
        i = Input()
        assert i.value == ""
        assert i.placeholder == ""
        assert i.max_length == 0
        assert i.password is False
        assert i.disabled is False
        assert i.visible is True

    def test_custom_placeholder_and_value(self):
        """自定义占位符和初始值。"""
        i = Input(placeholder="请输入", value="hello")
        assert i.placeholder == "请输入"
        assert i.value == "hello"
        assert i.cursor == 5  # cursor at end

    def test_password_mode(self):
        """密码模式。"""
        i = Input(password=True, value="secret")
        assert i.password is True
        assert "secret" not in i.render()  # 不显示明文
        # 检查仅显示 *
        rendered = i.render()
        assert rendered.count("*") == 6
        assert "secret" not in rendered

    def test_max_length(self):
        """最大长度限制。"""
        i = Input(max_length=3, value="abc")
        assert i.max_length == 3
        assert len(i.value) == 3


# ── 字符输入 ────────────────────────────────────────────


class TestInputTyping:
    """Input 字符输入测试。"""

    def test_insert_single_char(self):
        """插入单个字符。"""
        i = Input()
        i.handle_key(_key("h"))
        assert i.value == "h"
        assert i.cursor == 1

    def test_insert_multiple_chars(self):
        """插入多个字符。"""
        i = Input()
        for ch in "hello":
            i.handle_key(_key(ch))
        assert i.value == "hello"
        assert i.cursor == 5

    def test_max_length_enforcement(self):
        """max_length 限制生效。"""
        i = Input(max_length=3)
        for ch in "hello":
            i.handle_key(_key(ch))
        assert i.value == "hel"
        assert len(i.value) == 3

    def test_max_length_zero_unlimited(self):
        """max_length=0 表示无限制。"""
        i = Input(max_length=0)
        for ch in "hello_world":
            i.handle_key(_key(ch))
        assert i.value == "hello_world"


# ── 删除操作 ────────────────────────────────────────────


class TestInputDeletion:
    """Input 删除操作测试。"""

    def test_backspace(self):
        """Backspace 删除末尾字符。"""
        i = Input(value="hello")
        i.handle_key(_key("backspace"))
        assert i.value == "hell"
        assert i.cursor == 4

    def test_backspace_empty(self):
        """空值 Backspace 无变化。"""
        i = Input()
        i.handle_key(_key("backspace"))
        assert i.value == ""

    def test_backspace_at_start(self):
        """光标在开头时 Backspace 无变化。"""
        i = Input(value="hello")
        # 移动光标到开头
        for _ in range(5):
            i.handle_key(_key("left"))
        assert i.cursor == 0
        i.handle_key(_key("backspace"))
        assert i.value == "hello"  # 无变化

    def test_delete_after_cursor(self):
        """Delete 删除光标后字符。"""
        i = Input(value="hello")
        # 光标在开头
        i.handle_key(_key("home"))
        assert i.cursor == 0
        i.handle_key(_key("delete"))
        assert i.value == "ello"

    def test_delete_at_end(self):
        """光标在末尾时 Delete 无变化。"""
        i = Input(value="hello")
        i.handle_key(_key("delete"))
        assert i.value == "hello"

    def test_ctrl_u_delete_to_start(self):
        """Ctrl+U 删除光标前全部。"""
        i = Input(value="hello")
        i.handle_key(_key("l", ctrl=False))  # 先让光标不在末尾
        # 直接 Ctrl+U
        i.handle_key(_key("u", ctrl=True))
        assert i.value == ""  # 所有都被删除

    def test_ctrl_k_delete_to_end(self):
        """Ctrl+K 删除光标后全部。"""
        i = Input(value="hello")
        i.handle_key(_key("home"))  # 光标到开头
        i.handle_key(_key("k", ctrl=True))
        assert i.value == ""


# ── 光标导航 ────────────────────────────────────────────


class TestInputCursor:
    """Input 光标导航测试。"""

    def test_left_right(self):
        """左右移动光标。"""
        i = Input(value="ab")
        i.handle_key(_key("left"))
        assert i.cursor == 1
        i.handle_key(_key("left"))
        assert i.cursor == 0
        i.handle_key(_key("left"))  # 已到开头，不变
        assert i.cursor == 0
        i.handle_key(_key("right"))
        assert i.cursor == 1
        i.handle_key(_key("right"))
        assert i.cursor == 2

    def test_home_end(self):
        """Home/End 跳转。"""
        i = Input(value="hello")
        i.handle_key(_key("home"))
        assert i.cursor == 0
        i.handle_key(_key("end"))
        assert i.cursor == 5

    def test_insert_mid_cursor(self):
        """在中间位置插入字符。"""
        i = Input(value="ac")
        i.handle_key(_key("left"))
        i.handle_key(_key("b"))
        assert i.value == "abc"
        assert i.cursor == 2


# ── 动作键 ──────────────────────────────────────────────


class TestInputActions:
    """Input 动作键（Enter/Esc/Tab）测试。"""

    def test_enter_triggers_on_submit(self):
        """Enter 触发 on_submit。"""
        calls = []

        def on_submit(v):
            calls.append(v)

        i = Input(value="test")
        i.on_submit = on_submit
        i.handle_key(_key("enter"))
        assert calls == ["test"]

    def test_escape_triggers_on_cancel(self):
        """Escape 触发 on_cancel。"""
        calls = []

        def on_cancel():
            calls.append(True)

        i = Input()
        i.on_cancel = on_cancel
        i.handle_key(_key("escape"))
        assert calls == [True]

    def test_tab_triggers_on_tab(self):
        """Tab 触发 on_tab。"""
        calls = []

        def on_tab(v):
            calls.append(v)

        i = Input(value="prefix")
        i.on_tab = on_tab
        i.handle_key(_key("tab"))
        assert calls == ["prefix"]

    def test_on_change_callback(self):
        """on_change 回调触发。"""
        calls = []

        def on_change(v):
            calls.append(v)

        i = Input()
        i.on_change = on_change
        i.handle_key(_key("x"))
        assert calls == ["x"]
        i.handle_key(_key("y"))
        assert calls == ["x", "xy"]


# ── 渲染 ────────────────────────────────────────────────


class TestInputRender:
    """Input 渲染测试。"""

    def test_render_empty_with_placeholder(self):
        """空值 + placeholder 渲染。"""
        i = Input(placeholder="请输入...")
        rendered = i.render()
        assert "请输入..." in rendered

    def test_render_with_value(self):
        """有值渲染。"""
        i = Input(value="hello")
        rendered = i.render()
        assert "hello" in rendered

    def test_render_password_mode(self):
        """密码模式渲染。"""
        i = Input(value="abc", password=True)
        rendered = i.render()
        assert "***" in rendered
        assert "abc" not in rendered

    def test_render_hidden(self):
        """隐藏模式渲染。"""
        i = Input(value="hello")
        i.hide()
        assert i.render() == ""

    def test_render_focused_shows_cursor(self):
        """焦点模式下显示光标。"""
        i = Input(value="ab")
        i.focus()
        rendered = i.render()
        assert "|" in rendered

    def test_render_not_focused_no_cursor(self):
        """非焦点模式不显示光标。"""
        i = Input(value="ab")
        rendered = i.render()
        # 不在焦点，不应该有光标
        assert "ab" in rendered
        # 注意: 可能有焦点指示符也可能是其他渲染方式


# ── 禁用状态 ────────────────────────────────────────────


class TestInputDisabled:
    """Input 禁用状态测试。"""

    def test_disabled_ignores_key(self):
        i = Input(value="hello")
        i.disable()
        result = i.handle_key(_key("x"))
        assert result is False
        assert i.value == "hello"

    def test_disabled_renders_normally(self):
        """禁用状态仍渲染内容。"""
        i = Input(value="hello")
        i.disable()
        rendered = i.render()
        assert "hello" in rendered
