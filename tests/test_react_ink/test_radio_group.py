"""RadioGroup 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.radio_group import RadioGroup

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestRadioGroupBasicRendering:
    def test_options_rendered(self):
        rg = RadioGroup(options=[("a", "选项A"), ("b", "选项B")], selected="a")
        output = str(rg.render())
        assert "选项A" in _strip_ansi(output)
        assert "选项B" in _strip_ansi(output)

    def test_selected_dot_filled(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        output = str(rg.render())
        assert "\u25CF" in _strip_ansi(output)  # ●

    def test_unselected_dot_empty(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        output = str(rg.render())
        assert "\u25CB" in _strip_ansi(output)  # ○


class TestRadioGroupStyling:
    def test_selected_blue_bold(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        codes = _get_ansi_codes(str(rg.render()))
        assert any("34" in c for c in codes), f"应含蓝色(34): {codes}"
        assert any("1" in c for c in codes), f"应含bold(1): {codes}"

    def test_unselected_dim(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        output = str(rg.render())
        # 未选中项应 dim - 需要有 dim code 出现
        # 实际上两个选项，一个选中(bold+blue)，一个未选中(dim)
        codes = _get_ansi_codes(output)
        dim_codes = [c for c in codes if "2" in c and "3" not in c and "4" not in c and "0" not in c]
        assert len(dim_codes) >= 1, f"应含dim(2): {codes}"


class TestRadioGroupLayout:
    def test_vertical_by_default(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        output = str(rg.render())
        # 垂直排列，选项间有换行
        assert "\n" in _strip_ansi(output)

    def test_inline_horizontal(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a", inline=True)
        stripped = _strip_ansi(str(rg.render()))
        lines = stripped.split("\n")
        # horizontal: A 和 B 在同一行
        first_line = lines[0]
        assert "A" in first_line
        assert "B" in first_line


class TestRadioGroupEdgeCases:
    def test_no_options_returns_empty(self):
        rg = RadioGroup()
        assert rg.render() == ""

    def test_empty_options_list(self):
        rg = RadioGroup(options=[], selected="")
        assert rg.render() == ""

    def test_selected_not_in_options(self):
        rg = RadioGroup(options=[("a", "A")], selected="nonexistent")
        output = str(rg.render())
        # 无选中项，全部渲染为未选中
        codes = _get_ansi_codes(output)
        dim_codes = [c for c in codes if "2" in c and "3" not in c and "4" not in c and "0" not in c]
        assert len(dim_codes) >= 1, f"应含dim(2): {codes}"


class TestRadioGroupUpdate:
    def test_update_selected(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        assert rg.update({"selected": "b"}) is True
        assert rg.update({"selected": "b"}) is False

    def test_update_options(self):
        rg = RadioGroup(options=[("a", "A")], selected="a")
        assert rg.update({"options": [("c", "C")]}) is True

    def test_update_inline(self):
        rg = RadioGroup(options=[("a", "A")], selected="a")
        assert rg.update({"inline": True}) is True

    def test_update_no_change(self):
        rg = RadioGroup(options=[("a", "A")], selected="a")
        assert rg.update({"selected": "a"}) is False


class TestRadioGroupRenderVNode:
    def test_render_vnode(self):
        rg = RadioGroup(options=[("a", "A"), ("b", "B")], selected="a")
        vnode = rg.render_vnode()
        assert vnode.type == "radio_group"
        assert vnode.key == "radio_group"
        assert vnode.props.get("selected") == "a"
        assert vnode.props.get("option_count") == 2
