"""Card 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.card import Card
from src.chat_ui.components.text import Text

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestCardBasicRendering:
    def test_title_rendered(self):
        card = Card(title="卡片标题")
        output = str(card.render())
        assert "卡片标题" in _strip_ansi(output)

    def test_footer_rendered(self):
        card = Card(title="标题", footer="页脚文字")
        output = str(card.render())
        assert "页脚文字" in _strip_ansi(output)

    def test_title_bold(self):
        card = Card(title="粗体标题", bold_title=True)
        output = str(card.render())
        codes = _get_ansi_codes(output)
        assert any("1" in c for c in codes), f"标题应加粗: {codes}"

    def test_children_rendered_via_add_child(self):
        card = Card(title="卡片")
        card.add_child(Text("子内容"))
        output = str(card.render())
        assert "子内容" in _strip_ansi(output)

    def test_no_title_no_children(self):
        card = Card()
        output = str(card.render())
        assert output == ""


class TestCardEdgeCases:
    def test_empty_title(self):
        card = Card(title="", footer="页脚")
        output = str(card.render())
        assert "页脚" in _strip_ansi(output)

    def test_empty_footer(self):
        card = Card(title="标题")
        output = str(card.render())
        assert "标题" in _strip_ansi(output)

    def test_invalid_border_style_falls_back(self):
        card = Card(title="测试", border_style="invalid")
        assert card._border_style == "none"


class TestCardUpdate:
    def test_update_title(self):
        card = Card(title="旧")
        assert card.update({"title": "新"}) is True
        assert card.update({"title": "新"}) is False

    def test_update_footer(self):
        card = Card(title="标题")
        assert card.update({"footer": "新页脚"}) is True

    def test_update_border_style(self):
        card = Card(title="标题")
        assert card.update({"border_style": "solid"}) is True

    def test_update_bold_title(self):
        card = Card(title="标题", bold_title=True)
        assert card.update({"bold_title": False}) is True

    def test_update_no_change(self):
        card = Card(title="标题", footer="页脚")
        assert card.update({"title": "标题", "footer": "页脚"}) is False


class TestCardRenderVNode:
    def test_render_vnode(self):
        card = Card(title="测试")
        vnode = card.render_vnode()
        assert vnode.type == "card"
        assert vnode.key == "card"
        assert vnode.props.get("title") == "测试"
