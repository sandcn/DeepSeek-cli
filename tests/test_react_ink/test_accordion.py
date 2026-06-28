"""Accordion 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.accordion import Accordion
from src.chat_ui.components.text import Text

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestAccordionBasicRendering:
    def test_default_open_first_panel(self):
        acc = Accordion(
            items=[("面板1", Text("内容1")), ("面板2", Text("内容2"))],
            default_open=0,
        )
        output = str(acc.render())
        stripped = _strip_ansi(output)
        assert "\u25BC" in stripped  # ▼ (展开)
        assert "\u25B6" in stripped  # ▶ (折叠)
        assert "内容1" in stripped
        assert "内容2" not in stripped

    def test_default_open_second_panel(self):
        acc = Accordion(
            items=[("面板1", Text("内容1")), ("面板2", Text("内容2"))],
            default_open=1,
        )
        output = str(acc.render())
        stripped = _strip_ansi(output)
        assert "内容2" in stripped
        assert "内容1" not in stripped

    def test_all_collapsed(self):
        acc = Accordion(
            items=[("面板1", Text("内容1")), ("面板2", Text("内容2"))],
            default_open=-1,
        )
        output = str(acc.render())
        stripped = _strip_ansi(output)
        assert "\u25B6" in stripped  # ▶ 全部折叠
        assert "内容1" not in stripped

    def test_content_as_string(self):
        acc = Accordion(
            items=[("面板", "字符串内容")],
            default_open=0,
        )
        assert "字符串内容" in _strip_ansi(str(acc.render()))


class TestAccordionOpenIndex:
    def test_open_index_property(self):
        acc = Accordion(
            items=[("A", Text("")), ("B", Text(""))],
            default_open=0,
        )
        assert acc.open_index == 0
        acc.open_index = 1
        assert acc.open_index == 1

    def test_open_index_out_of_range(self):
        acc = Accordion(
            items=[("A", Text(""))],
            default_open=0,
        )
        acc.open_index = 99  # 超出不生效
        assert acc.open_index == 0

    def test_open_index_negative_one(self):
        acc = Accordion(
            items=[("A", Text("")), ("B", Text(""))],
            default_open=0,
        )
        acc.open_index = -1  # 全折叠
        assert acc.open_index == -1


class TestAccordionStyling:
    def test_open_title_bold(self):
        acc = Accordion(items=[("标题", Text(""))], default_open=0)
        codes = _get_ansi_codes(str(acc.render()))
        assert any("1" in c for c in codes), f"展开标题应bold: {codes}"

    def test_collapsed_title_dim(self):
        acc = Accordion(
            items=[("A", Text("")), ("B", Text(""))],
            default_open=0,
        )
        codes = _get_ansi_codes(str(acc.render()))
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"折叠标题应dim: {codes}"


class TestAccordionEdgeCases:
    def test_no_items_returns_empty(self):
        acc = Accordion()
        assert acc.render() == ""

    def test_empty_items_list(self):
        acc = Accordion(items=[])
        assert acc.render() == ""

    def test_default_open_out_of_bounds(self):
        acc = Accordion(items=[("A", Text(""))], default_open=5)
        assert acc._open_index == -1  # 超出范围全折叠

    def test_single_item(self):
        acc = Accordion(items=[("唯一", Text("内容"))], default_open=0)
        stripped = _strip_ansi(str(acc.render()))
        assert "\u25BC" in stripped
        assert "内容" in stripped


class TestAccordionUpdate:
    def test_update_items(self):
        acc = Accordion(items=[("A", Text(""))], default_open=0)
        assert acc.update({"items": [("B", Text(""))]}) is True

    def test_update_default_open(self):
        acc = Accordion(items=[("A", Text("")), ("B", Text(""))], default_open=-1)
        assert acc.update({"default_open": 0}) is True
        assert acc.update({"default_open": 0}) is False

    def test_update_no_change(self):
        acc = Accordion(items=[("A", Text(""))], default_open=0)
        assert acc.update({"default_open": 0}) is False


class TestAccordionRenderVNode:
    def test_render_vnode(self):
        acc = Accordion(items=[("A", Text(""))], default_open=0)
        vnode = acc.render_vnode()
        assert vnode.type == "accordion"
        assert vnode.key == "accordion"
        assert vnode.props.get("open_index") == 0
        assert vnode.props.get("item_count") == 1
