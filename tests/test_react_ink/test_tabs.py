"""Tabs 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.tabs import Tabs
from src.chat_ui.components.text import Text

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestTabsBasicRendering:
    def test_single_tab(self):
        tabs = Tabs(items=[("t1", "Home", Text("首页内容"))], active_id="t1")
        output = str(tabs.render())
        stripped = _strip_ansi(output)
        assert "[Home]" in stripped
        assert "首页内容" in stripped

    def test_multiple_tabs_active_first(self):
        tabs = Tabs(
            items=[("a", "TabA", Text("A内容")), ("b", "TabB", Text("B内容"))],
            active_id="a",
        )
        output = str(tabs.render())
        stripped = _strip_ansi(output)
        assert "[TabA]" in stripped
        assert "TabB" in stripped
        assert "A内容" in stripped
        assert "B内容" not in stripped

    def test_active_second_tab(self):
        tabs = Tabs(
            items=[("a", "TabA", Text("ContentA")), ("b", "TabB", Text("ContentB"))],
            active_id="b",
        )
        output = str(tabs.render())
        stripped = _strip_ansi(output)
        assert "ContentB" in stripped
        assert "ContentA" not in stripped  # 非活跃标签内容不显示


class TestTabsActiveStyling:
    def test_active_tab_has_bold_and_blue(self):
        tabs = Tabs(items=[("t1", "Active", Text("")), ("t2", "Inactive", Text(""))], active_id="t1")
        output = str(tabs.render())
        codes = _get_ansi_codes(output)
        # 应同时包含 bold(1) 和 blue(34)
        assert any("1" in c for c in codes), f"应含 bold: {codes}"
        assert any("34" in c for c in codes), f"应含 blue: {codes}"


class TestTabsEdgeCases:
    def test_no_items_returns_empty(self):
        tabs = Tabs()
        output = str(tabs.render())
        assert output == "" or _strip_ansi(output) == ""

    def test_empty_items_list(self):
        tabs = Tabs(items=[], active_id="")
        output = str(tabs.render())
        assert output == ""

    def test_active_id_not_in_items(self):
        tabs = Tabs(items=[("t1", "Tab", Text("内容"))], active_id="nonexistent")
        output = str(tabs.render())
        stripped = _strip_ansi(output)
        assert "Tab" in stripped  # 标签栏仍渲染
        assert "内容" not in stripped  # 无匹配内容

    def test_tab_without_content(self):
        tabs = Tabs(items=[("t1", "Empty", None)], active_id="t1")
        output = str(tabs.render())
        assert "Empty" in _strip_ansi(output)


class TestTabsUpdate:
    def test_update_active_id(self):
        tabs = Tabs(items=[("a", "A", Text("")), ("b", "B", Text(""))], active_id="a")
        assert tabs.update({"active_id": "b"}) is True
        assert tabs.update({"active_id": "b"}) is False

    def test_update_items(self):
        tabs = Tabs(items=[("a", "A", Text(""))], active_id="a")
        assert tabs.update({"items": [("b", "B", Text(""))]}) is True

    def test_update_no_change(self):
        tabs = Tabs(items=[("a", "A", Text(""))], active_id="a")
        assert tabs.update({"active_id": "a"}) is False


class TestTabsRenderVNode:
    def test_render_vnode(self):
        tabs = Tabs(items=[("t1", "Tab1", Text("内容"))], active_id="t1")
        vnode = tabs.render_vnode()
        assert vnode.type == "tabs"
        assert vnode.key == "tabs"
        assert vnode.props.get("active_id") == "t1"
        assert vnode.props.get("tab_count") == 1
