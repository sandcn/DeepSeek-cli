"""ListView 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.list_view import ListView

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestListViewBasicRendering:
    def test_simple_items(self):
        lv = ListView(items=[{"label": "A"}, {"label": "B"}, {"label": "C"}])
        output = str(lv.render())
        stripped = _strip_ansi(output)
        assert "A" in stripped
        assert "B" in stripped
        assert "C" in stripped

    def test_empty_items_returns_empty(self):
        lv = ListView()
        assert lv.render() == ""

    def test_empty_items_list(self):
        lv = ListView(items=[])
        assert lv.render() == ""


class TestListViewSelection:
    def test_item_selected_blue_bold(self):
        lv = ListView(items=[{"label": "A", "selected": True}, {"label": "B"}])
        output = str(lv.render())
        codes = _get_ansi_codes(output)
        assert any("34" in c for c in codes), f"选中项应蓝色(34): {codes}"
        assert any("1" in c for c in codes), f"选中项应加粗(1): {codes}"

    def test_selected_string_match(self):
        lv = ListView(
            items=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
            selected="B",
        )
        output = str(lv.render())
        codes = _get_ansi_codes(output)
        # B 是选中项，应有蓝色
        assert any("34" in c for c in codes), f"selected 匹配应有蓝色(34): {codes}"

    def test_selected_no_match(self):
        lv = ListView(
            items=[{"label": "A"}, {"label": "B"}],
            selected="NONEXISTENT",
        )
        output = str(lv.render())
        codes = _get_ansi_codes(output)
        # 无匹配项 → 可能无 ANSI（纯文本）
        # 或仅有重置码，检查不含 34
        blue_codes = [c for c in codes if "34" in c]
        assert len(blue_codes) == 0, f"无匹配不应蓝色(34): {codes}"


class TestListViewNumbers:
    def test_show_numbers(self):
        lv = ListView(
            items=[{"label": "A"}, {"label": "B"}, {"label": "C"}],
            show_numbers=True,
        )
        output = str(lv.render())
        stripped = _strip_ansi(output)
        assert "1." in stripped
        assert "2." in stripped
        assert "3." in stripped

    def test_custom_prefix(self):
        lv = ListView(items=[{"label": "A", "prefix": "●"}])
        output = str(lv.render())
        assert "●" in _strip_ansi(output)


class TestListViewDivider:
    def test_show_divider(self):
        lv = ListView(
            items=[{"label": "A"}, {"label": "B"}],
            show_divider=True,
        )
        output = str(lv.render())
        stripped = _strip_ansi(output)
        # 分割线字符 ── (U+2500)
        assert "\u2500" in stripped


class TestListViewGroup:
    def test_group_header(self):
        lv = ListView(items=[
            {"label": "A", "group": "水果"},
            {"label": "B", "group": "水果"},
        ])
        output = str(lv.render())
        stripped = _strip_ansi(output)
        assert "水果" in stripped
        assert "──" in stripped

    def test_group_header_dim_by_default(self):
        lv = ListView(items=[{"label": "A", "group": "组1"}])
        output = str(lv.render())
        codes = _get_ansi_codes(output)
        # dim = 2
        has_dim = any("2" in c and "3" not in c and "0" not in c for c in codes) or any(
            ";2" in c or "2;" in c for c in codes
        )
        assert has_dim, f"默认分组标题应dim: {codes}"

    def test_group_header_bold(self):
        lv = ListView(
            items=[{"label": "A", "group": "组1"}],
            group_header_style="bold",
        )
        output = str(lv.render())
        codes = _get_ansi_codes(output)
        assert any("1" in c for c in codes), f"bold分组标题应加粗: {codes}"


class TestListViewCombined:
    def test_group_with_numbers_and_divider(self):
        lv = ListView(
            items=[
                {"label": "A", "group": "X"},
                {"label": "B", "group": "X"},
                {"label": "C"},
            ],
            show_numbers=True,
            show_divider=True,
        )
        output = str(lv.render())
        stripped = _strip_ansi(output)
        assert "1." in stripped
        assert "2." in stripped
        assert "3." in stripped
        assert "──" in stripped
        assert "\u2500" in stripped

    def test_mixed_selected_and_prefix(self):
        lv = ListView(items=[
            {"label": "A", "prefix": ">"},
            {"label": "B", "selected": True},
            {"label": "C", "group": "G"},
        ])
        output = str(lv.render())
        stripped = _strip_ansi(output)
        assert ">" in stripped
        assert "G" in stripped


class TestListViewUpdate:
    def test_update_items(self):
        lv = ListView(items=[{"label": "A"}])
        assert lv.update({"items": [{"label": "B"}]}) is True
        assert lv.update({"items": [{"label": "B"}]}) is False

    def test_update_selected(self):
        lv = ListView(items=[{"label": "A"}], selected="A")
        assert lv.update({"selected": "B"}) is True

    def test_update_show_numbers(self):
        lv = ListView(items=[{"label": "A"}])
        assert lv.update({"show_numbers": True}) is True

    def test_update_no_change(self):
        lv = ListView(items=[{"label": "A"}], selected="A")
        assert lv.update({"selected": "A"}) is False


class TestListViewRenderVNode:
    def test_render_vnode(self):
        lv = ListView(items=[{"label": "A"}, {"label": "B"}])
        vnode = lv.render_vnode()
        assert vnode.type == "list_view"
        assert vnode.key == "list_view"
        assert vnode.props.get("item_count") == 2
