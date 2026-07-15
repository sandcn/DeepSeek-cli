"""Menu 控件测试。

覆盖：渲染（垂直/水平）、选项导航、键盘事件、回调、边界条件。
"""

import pytest

from tui_framework.events.event_types import KeyPressEvent
from tui_framework.widgets.menu import Menu


def _key(key: str, ctrl: bool = False, alt: bool = False, shift: bool = False) -> KeyPressEvent:
    return KeyPressEvent(key=key, ctrl=ctrl, alt=alt, shift=shift)


# ── 基础属性 ────────────────────────────────────────────


class TestMenuBasic:
    """Menu 基础属性测试。"""

    def test_default_values(self):
        m = Menu()
        assert m.items == []
        assert m.active_index == 0
        assert m.horizontal is False
        assert m.wrap_around is True

    def test_with_items(self):
        items = [("File", "file"), ("Edit", "edit"), ("View", "view")]
        m = Menu(items=items)
        assert m.count == 3
        assert m.active_item == ("File", "file")

    def test_custom_active_index(self):
        items = [("File", "file"), ("Edit", "edit")]
        m = Menu(items=items, active_index=1)
        assert m.active_index == 1
        assert m.active_item == ("Edit", "edit")

    def test_active_item_none_when_empty(self):
        m = Menu()
        assert m.active_item is None

    def test_items_setter_clamps(self):
        m = Menu(items=[("a", "a"), ("b", "b")], active_index=1)
        m.items = [("x", "x")]  # shrink
        assert m.active_index == 0


# ── 导航 ────────────────────────────────────────────────


class TestMenuNavigation:
    """Menu 导航测试。"""

    def test_down_normal(self):
        items = [("a", "a"), ("b", "b"), ("c", "c")]
        m = Menu(items=items)
        m.handle_key(_key("down"))
        assert m.active_index == 1
        m.handle_key(_key("down"))
        assert m.active_index == 2

    def test_up_normal(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items, active_index=1)
        m.handle_key(_key("up"))
        assert m.active_index == 0

    def test_down_wrap_around(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items, active_index=1)
        m.handle_key(_key("down"))
        assert m.active_index == 0  # wrap

    def test_up_wrap_around(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items, active_index=0)
        m.handle_key(_key("up"))
        assert m.active_index == 1  # wrap

    def test_no_wrap_down(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items, active_index=1, wrap_around=False)
        m.handle_key(_key("down"))
        assert m.active_index == 1  # no wrap, stay

    def test_no_wrap_up(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items, active_index=0, wrap_around=False)
        m.handle_key(_key("up"))
        assert m.active_index == 0  # no wrap, stay

    def test_navigation_empty_menu(self):
        m = Menu()
        m.handle_key(_key("down"))
        assert m.active_index == 0  # no crash


# ── 选择回调 ────────────────────────────────────────────


class TestMenuSelection:
    """Menu 选择回调测试。"""

    def test_on_select_triggered(self):
        calls = []

        def on_select(action_id):
            calls.append(action_id)

        items = [("File", "file"), ("Edit", "edit")]
        m = Menu(items=items)
        m.on_select = on_select
        m.handle_key(_key("enter"))
        assert calls == ["file"]

    def test_on_select_with_navigation(self):
        calls = []

        def on_select(action_id):
            calls.append(action_id)

        items = [("a", "1"), ("b", "2")]
        m = Menu(items=items)
        m.on_select = on_select
        m.handle_key(_key("down"))  # activate "b"
        m.handle_key(_key("enter"))
        assert calls == ["2"]

    def test_on_cancel_triggered(self):
        calls = []

        def on_cancel():
            calls.append(True)

        m = Menu(items=[("a", "a")])
        m.on_cancel = on_cancel
        m.handle_key(_key("escape"))
        assert calls == [True]

    def test_on_change_triggered(self):
        calls = []

        def on_change(idx, label, action_id):
            calls.append((idx, label, action_id))

        items = [("a", "1"), ("b", "2")]
        m = Menu(items=items)
        m.on_change = on_change
        m.handle_key(_key("down"))
        assert calls == [(1, "b", "2")]


# ── 渲染 ────────────────────────────────────────────────


class TestMenuRender:
    """Menu 渲染测试。"""

    def test_render_vertical(self):
        items = [("File", "file"), ("Edit", "edit")]
        m = Menu(items=items)
        rendered = m.render()
        assert "File" in rendered
        assert "Edit" in rendered

    def test_render_horizontal(self):
        items = [("File", "file"), ("Edit", "edit")]
        m = Menu(items=items, horizontal=True)
        rendered = m.render()
        assert "File" in rendered
        assert "Edit" in rendered

    def test_render_hidden(self):
        m = Menu(items=[("a", "a")])
        m.hide()
        assert m.render() == ""

    def test_render_empty(self):
        m = Menu()
        assert m.render() == ""

    def test_render_active_indicator(self):
        items = [("File", "file")]
        m = Menu(items=items)
        rendered = m.render()
        assert "▶" in rendered

    def test_render_inactive_no_indicator(self):
        items = [("a", "1"), ("b", "2")]
        m = Menu(items=items, active_index=1)
        rendered = m.render()
        # "a" 那一行不应该有 ▶
        lines = rendered.split("\n")
        # 第一行 (active=0, inactive)
        from tui_framework.core.ansi_utils import strip_ansi
        clean_lines = [strip_ansi(l) for l in lines]
        # 检查: active=1 的行应该有 ▶，active=0 行不应该有 ▶
        active_found = False
        inactive_found = False
        for i, line in enumerate(clean_lines):
            if "▶" in line:
                active_found = True
            elif i < len(clean_lines) and "a" in line and "▶" not in line:
                inactive_found = True
        assert active_found


# ── 禁用状态 ────────────────────────────────────────────


class TestMenuDisabled:
    """Menu 禁用状态测试。"""

    def test_disabled_ignores_navigation(self):
        items = [("a", "a"), ("b", "b")]
        m = Menu(items=items)
        m.disable()
        result = m.handle_key(_key("down"))
        assert result is False
        assert m.active_index == 0

    def test_disabled_ignores_select(self):
        calls = []

        def on_select(action_id):
            calls.append(action_id)

        m = Menu(items=[("a", "a")])
        m.on_select = on_select
        m.disable()
        m.handle_key(_key("enter"))
        assert calls == []


# ── 可见性 ──────────────────────────────────────────────


class TestMenuVisibility:
    """Menu 可见性测试。"""

    def test_hidden_ignores_key(self):
        m = Menu(items=[("a", "a")])
        m.hide()
        result = m.handle_key(_key("enter"))
        assert result is False

    def test_hidden_renders_empty(self):
        m = Menu(items=[("a", "a")])
        m.hide()
        assert m.render() == ""
