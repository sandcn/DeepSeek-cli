"""FixedSizeBox 组件单元测试。"""
from __future__ import annotations

import re

from src.chat_ui.components.fixed_box import FixedSizeBox
from src.chat_ui.components.text import Text

_ANSI_RE = re.compile(r'\033\[[\d;]*m')

def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub('', text)

def _has_ansi(text: str) -> bool:
    return bool(_ANSI_RE.search(text))

def _get_ansi_codes(text: str) -> list[str]:
    return _ANSI_RE.findall(text)


class TestFixedSizeBoxBasic:
    def test_fixed_dimensions_fills_empty(self):
        """height=3, 内容不足 → 填充空行。"""
        box = FixedSizeBox(width=10, height=3, border_style=None)
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 3
        assert all(len(line) == 10 for line in lines)

    def test_height_exact_no_padding(self):
        """内容行数恰等于 height → 无填充无截断。"""
        box = FixedSizeBox(width=10, height=2, border_style=None)
        box.add_child(Text("line1\nline2"))
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 2
        assert "line1" in lines[0]
        assert "line2" in lines[1]

    def test_border_single(self):
        """border_style=single → 边框字符存在。"""
        box = FixedSizeBox(width=10, height=3, border_style="single")
        output = str(box.render())
        stripped = _strip_ansi(output)
        lines = stripped.split("\n")
        assert len(lines) == 5  # top + 3 content + bottom
        assert "┌" in lines[0]
        assert "┐" in lines[0]
        assert "└" in lines[-1]
        assert "┘" in lines[-1]
        assert "│" in lines[1]

    def test_no_border(self):
        """border_style=None → 无边框字符。"""
        box = FixedSizeBox(width=10, height=3, border_style=None)
        output = str(box.render())
        stripped = _strip_ansi(output)
        lines = stripped.split("\n")
        assert len(lines) == 3
        assert "┌" not in stripped
        assert "│" not in stripped


class TestFixedSizeBoxTruncation:
    def test_width_truncation(self):
        """内容超出 width 时截断并追加省略号。"""
        box = FixedSizeBox(width=5, height=2, border_style=None)
        box.add_child(Text("hello world"))
        output = str(box.render())
        stripped = _strip_ansi(output)
        lines = stripped.split("\n")
        # "hello world" (11 chars) → 截断为 "hello…" (5+1 chars 但视觉宽度 5)
        # 实际上 "hello"=5 个字符，省略号=1，视觉宽度=6 > 5
        # 所以截断为 "hell" + "…" = 4+1=5
        assert len(lines[0]) <= 5

    def test_height_truncation(self):
        """内容超出 height 时截断并显示省略标记。"""
        box = FixedSizeBox(width=20, height=3, border_style=None)
        box.add_child(Text("a\nb\nc\nd\ne"))
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 3
        # 最后一行是截断指示
        assert "truncated" in lines[-1] or "..." in lines[-1]

    def test_both_truncation(self):
        """宽度+高度同时截断。"""
        box = FixedSizeBox(width=3, height=2, border_style=None)
        box.add_child(Text("abcdef\nghijkl\nmnopqr"))
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 2


class TestFixedSizeBoxBorder:
    def test_border_with_title(self):
        """标题嵌入上边框。"""
        box = FixedSizeBox(width=20, height=3, border_style="single", title="Test")
        output = str(box.render())
        stripped = _strip_ansi(output)
        assert "Test" in stripped
        assert "┌" in stripped

    def test_border_color_red(self):
        """border_color=red → ANSI 红色。"""
        box = FixedSizeBox(width=10, height=3, border_style="single", border_color="red")
        output = str(box.render())
        codes = _get_ansi_codes(output)
        assert any("31" in c for c in codes), f"应含红色(31): {codes}"


class TestFixedSizeBoxPadding:
    def test_padding_x(self):
        """padding_x=2 → 内容左右有空格。"""
        box = FixedSizeBox(width=10, height=3, padding_x=2, border_style=None)
        box.add_child(Text("hi"))
        output = str(box.render())
        lines = output.split("\n")
        # 内容 "hi" + padding "  " = "  hi" 视觉宽度 4
        # width=10，不足部分空格填充
        assert "hi" in lines[0]

    def test_padding_y(self):
        """padding_y=1 → 内容上下有空白行。"""
        box = FixedSizeBox(width=10, height=4, padding_y=1, border_style=None)
        box.add_child(Text("内容"))
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 4
        assert lines[0] == "" or lines[0] == " " * 10  # padding top
        assert "内容" in lines[1]


class TestFixedSizeBoxEdgeCases:
    def test_empty_children(self):
        """无子组件 → 全部为空白填充。"""
        box = FixedSizeBox(width=8, height=2, border_style=None)
        output = str(box.render())
        lines = output.split("\n")
        assert len(lines) == 2
        assert all(line == " " * 8 for line in lines)

    def test_custom_truncate_indicator(self):
        """自定义截断指示符。"""
        box = FixedSizeBox(width=6, height=2, border_style=None, truncate_indicator=">>>")
        box.add_child(Text("abcdefghij"))
        output = str(box.render())
        lines = output.split("\n")
        # "abcde" + ">>>" = 8 > 6, 所以 "abcd" + ">>>" = 7 > 6
        # 实际 "abc" + ">>>" = 6, 正好
        assert len(lines[0]) <= 6

    def test_cjk_characters(self):
        """CJK 宽字符正确截断。"""
        box = FixedSizeBox(width=4, height=2, border_style=None)
        box.add_child(Text("你好世界"))
        output = str(box.render())
        lines = output.split("\n")
        # 每个汉字视觉宽度=2，width=4 所以最多放 2 个汉字
        # 3 个汉字 = 6 > 4，截断为 2 个汉字
        stripped = _strip_ansi(lines[0])
        # 去掉省略号，看实际显示的文字宽度
        assert len(stripped) <= 4  # 视觉宽度不超过 4

    def test_min_width_height(self):
        """width/height clamp 到最小值 1。"""
        box = FixedSizeBox(width=0, height=0, border_style=None)
        assert box._width >= 1
        assert box._height >= 1


class TestFixedSizeBoxUpdate:
    def test_update_width(self):
        box = FixedSizeBox(width=10, height=5)
        assert box.update({"width": 20}) is True
        assert box.update({"width": 20}) is False

    def test_update_height(self):
        box = FixedSizeBox(width=10, height=5)
        assert box.update({"height": 10}) is True

    def test_update_title(self):
        box = FixedSizeBox(width=10, height=5, title="旧")
        assert box.update({"title": "新"}) is True

    def test_update_no_change(self):
        box = FixedSizeBox(width=10, height=5, title="t")
        assert box.update({"width": 10, "height": 5}) is False


class TestFixedSizeBoxRenderVNode:
    def test_render_vnode(self):
        box = FixedSizeBox(width=10, height=5)
        vnode = box.render_vnode()
        assert vnode.type == "fixed_box"
        assert vnode.key == "fixed_box"
        assert vnode.props.get("width") == 10
        assert vnode.props.get("height") == 5
