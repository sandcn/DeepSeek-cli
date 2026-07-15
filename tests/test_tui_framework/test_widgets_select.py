"""Select 控件测试。

覆盖：渲染、展开/收起、选项导航、键盘事件、回调、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.select import Select


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestSelectBasic:
    """Select 基础属性测试。"""

    def test_default_values(self):
        s = Select()
        assert s.options == []
        assert s.selected_index == 0
        assert s.expanded is False

    def test_with_options(self):
        s = Select(options=["a", "b", "c"])
        assert s.options == ["a", "b", "c"]
        assert s.selected_index == 0
        assert s.selected_option == "a"

    def test_custom_selected_index(self):
        s = Select(options=["a", "b", "c"], selected_index=2)
        assert s.selected_index == 2
        assert s.selected_option == "c"

    def test_selected_index_clamped(self):
        """超出范围的 selected_index 被 clamp。"""
        s = Select(options=["a", "b"], selected_index=5)
        assert s.selected_index == 1  # clamp to max

    def test_negative_selected_index(self):
        """负索引被 clamp 到 0。"""
        s = Select(options=["a", "b"], selected_index=-5)
        assert s.selected_index == 0

    def test_options_setter_clamps_index(self):
        s = Select(options=["a", "b", "c"], selected_index=2)
        s.options = ["x"]
        assert s.selected_index == 0  # clamp
        assert s.selected_option == "x"


# ── 展开/收起 ───────────────────────────────────────────


class TestSelectExpandCollapse:
    """Select 展开/收起测试。"""

    def test_expand_with_enter(self):
        s = Select(options=["a", "b", "c"])
        assert s.expanded is False
        s.handle_key(_key("enter"))
        assert s.expanded is True

    def test_expand_with_space(self):
        s = Select(options=["a", "b", "c"])
        s.handle_key(_key("space"))
        assert s.expanded is True

    def test_collapse_with_enter(self):
        s = Select(options=["a", "b", "c"])
        s.handle_key(_key("enter"))  # expand
        s.handle_key(_key("down"))
        s.handle_key(_key("enter"))  # confirm
        assert s.expanded is False
        assert s.selected_index == 1

    def test_collapse_with_escape_restores(self):
        s = Select(options=["a", "b", "c"], selected_index=0)
        s.handle_key(_key("enter"))  # expand
        s.handle_key(_key("down"))  # move to 1
        s.handle_key(_key("escape"))  # cancel
        assert s.expanded is False
        assert s.selected_index == 0  # restored

    def test_expand_empty_options(self):
        """空选项时展开无效果。"""
        s = Select()
        s.handle_key(_key("enter"))
        assert s.expanded is False


# ── 选项导航 ───────────────────────────────────────────


class TestSelectNavigation:
    """Select 选项导航测试。"""

    def test_up_down_in_collapsed(self):
        """收起状态下也可导航。"""
        s = Select(options=["a", "b", "c"])
        s.handle_key(_key("down"))
        assert s.selected_index == 1
        s.handle_key(_key("down"))
        assert s.selected_index == 2
        s.handle_key(_key("down"))  # 到末尾，不循环
        assert s.selected_index == 2
        s.handle_key(_key("up"))
        assert s.selected_index == 1
        s.handle_key(_key("up"))
        assert s.selected_index == 0

    def test_up_down_in_expanded(self):
        s = Select(options=["a", "b", "c"])
        s.handle_key(_key("enter"))  # expand
        s.handle_key(_key("down"))
        assert s.selected_index == 1
        s.handle_key(_key("up"))
        assert s.selected_index == 0

    def test_navigation_bounds(self):
        """导航边界测试。"""
        s = Select(options=["only"])
        s.handle_key(_key("down"))
        assert s.selected_index == 0
        s.handle_key(_key("up"))
        assert s.selected_index == 0


# ── 回调 ────────────────────────────────────────────────


class TestSelectCallbacks:
    """Select 回调测试。"""

    def test_on_change_triggered(self):
        calls = []

        def on_change(idx, opt):
            calls.append((idx, opt))

        s = Select(options=["a", "b"])
        s.on_change = on_change
        s.handle_key(_key("down"))
        assert calls == [(1, "b")]

    def test_on_change_not_triggered_at_boundary(self):
        """边界处不触发 on_change。"""
        calls = []

        def on_change(idx, opt):
            calls.append(idx)

        s = Select(options=["a", "b"])
        s.on_change = on_change
        s.handle_key(_key("up"))  # already at top
        assert calls == []  # no change

    def test_enter_confirm_triggers_change(self):
        calls = []

        def on_change(idx, opt):
            calls.append(idx)

        s = Select(options=["a", "b"])
        s.on_change = on_change
        s.handle_key(_key("enter"))  # expand
        s.handle_key(_key("down"))  # move to 1 → on_change fires here
        # reset calls to check if enter also triggers
        calls.clear()
        s.handle_key(_key("enter"))  # confirm
        assert calls == [1]  # confirm also triggers on_change


# ── 渲染 ────────────────────────────────────────────────


class TestSelectRender:
    """Select 渲染测试。"""

    def test_render_collapsed(self):
        s = Select(options=["apple", "banana"])
        rendered = s.render()
        assert "apple" in rendered
        assert "▼" in rendered

    def test_render_expanded(self):
        s = Select(options=["apple", "banana"])
        s.handle_key(_key("enter"))
        rendered = s.render()
        assert "apple" in rendered
        assert "banana" in rendered
        assert "▲" in rendered

    def test_render_hidden(self):
        s = Select(options=["a"])
        s.hide()
        assert s.render() == ""

    def test_render_empty_options(self):
        s = Select()
        rendered = s.render()
        assert "无选项" in rendered

    def test_render_selected_indicator(self):
        s = Select(options=["a", "b"])
        s.handle_key(_key("enter"))
        rendered = s.render()
        assert "●" in rendered


# ── 禁用状态 ────────────────────────────────────────────


class TestSelectDisabled:
    """Select 禁用状态测试。"""

    def test_disabled_ignores_key(self):
        s = Select(options=["a", "b"])
        s.disable()
        result = s.handle_key(_key("enter"))
        assert result is False
        assert s.expanded is False

    def test_disabled_ignores_navigation(self):
        s = Select(options=["a", "b"], selected_index=0)
        s.disable()
        s.handle_key(_key("down"))
        assert s.selected_index == 0
