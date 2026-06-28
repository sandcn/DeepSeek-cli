"""Panel 组件单元测试。

覆盖 Panel 组件的 header/body/footer 三段式渲染、
颜色加粗、自定义 border_style、空 children、update() 变更检测。

测试策略：构造 Panel 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证边框字符、颜色序列和内容正确性。
"""

from __future__ import annotations

import re
import pytest

from src.chat_ui.components.panel import Panel
from src.chat_ui.components.base import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _TextComp(TuiComponent):
    """简单文本子组件，返回固定内容。"""

    def __init__(self, text: str = "hello"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


# ANSI 转义序列匹配
_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _has_ansi(text: str) -> bool:
    """检查文本是否含 ANSI 序列。"""
    return bool(_ANSI_RE.search(text))


# ═══════════════════════════════════════════════════════════
# TestPanelRendering
# ═══════════════════════════════════════════════════════════

class TestPanelRendering:
    """Panel 渲染测试。"""

    def test_body_only(self):
        """仅 body（无 header/footer）渲染边框包裹内容。"""
        panel = Panel(children=[_TextComp("hello")])
        output = panel.render()
        clean = _strip_ansi(output)

        # 内容应在边框内
        assert "hello" in clean

        # 应包含 single 边框字符
        lines = clean.split("\n")
        assert len(lines) >= 3  # top + content + bottom
        assert "┌" in lines[0] and "┐" in lines[0]
        assert "└" in lines[-1] and "┘" in lines[-1]

        # 不应含有 header/footer 区域
        assert "Title" not in clean

    def test_header_plus_body(self):
        """header + body 渲染标题行和边框内容。"""
        panel = Panel(header="Title", children=[_TextComp("content")])
        output = panel.render()
        clean = _strip_ansi(output)

        # header 文本应在输出中
        assert "Title" in clean
        # body 内容应在输出中
        assert "content" in clean
        # 边框应存在
        assert "┌" in clean and "└" in clean

    def test_body_plus_footer(self):
        """body + footer 渲染边框内容和底部行。"""
        panel = Panel(footer="Footer", children=[_TextComp("content")])
        output = panel.render()
        clean = _strip_ansi(output)

        # footer 文本应在输出中
        assert "Footer" in clean
        # body 内容应在输出中
        assert "content" in clean
        # 边框应存在
        assert "┌" in clean and "└" in clean

    def test_header_body_footer_full(self):
        """header + body + footer 三段完整渲染。"""
        panel = Panel(
            header="H", footer="F",
            children=[_TextComp("B")],
        )
        output = panel.render()
        clean = _strip_ansi(output)

        assert "H" in clean
        assert "B" in clean
        assert "F" in clean
        # 边框应存在
        assert "┌" in clean and "└" in clean

    def test_header_footer_color_bold(self):
        """header/footer 颜色加粗生成 ANSI 序列。"""
        panel = Panel(
            header="Header",
            header_color="blue",
            header_bold=True,
            footer="Footer",
            footer_color="red",
            footer_bold=True,
            children=[_TextComp("body")],
        )
        output = panel.render()

        # 应包含 ANSI 序列（颜色 + 加粗）
        assert _has_ansi(output)

        # header 部分应含蓝色粗体 ANSI
        # footer 部分应含红色粗体 ANSI
        clean = _strip_ansi(output)
        assert "Header" in clean
        assert "Footer" in clean
        assert "body" in clean

    def test_custom_border_style(self):
        """自定义 border_style 使用不同边框字符。"""
        panel = Panel(
            border_style="double",
            children=[_TextComp("x")],
        )
        output = panel.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # double 边框字符
        assert "╔" in lines[0] and "╗" in lines[0]
        assert "╚" in lines[-1] and "╝" in lines[-1]
        assert "x" in clean

    def test_empty_children(self):
        """空 children 渲染空边框（不崩溃）。"""
        panel = Panel()
        output = panel.render()

        # 不应抛异常，应返回字符串
        assert isinstance(output, str)
        assert output  # 非空（至少包含边框字符）

        clean = _strip_ansi(output)
        # 边框应存在
        assert "┌" in clean or "└" in clean

    def test_empty_children_with_header_footer(self):
        """空 children + header/footer 正常渲染。"""
        panel = Panel(header="H", footer="F")
        output = panel.render()
        clean = _strip_ansi(output)

        assert "H" in clean
        assert "F" in clean
        # 边框应存在
        assert "┌" in clean and "└" in clean


# ═══════════════════════════════════════════════════════════
# TestPanelUpdate
# ═══════════════════════════════════════════════════════════

class TestPanelUpdate:
    """Panel update() 变更检测测试。"""

    def test_update_no_change_returns_false(self):
        """相同 props 不触发变更。"""
        panel = Panel(
            header="H", footer="F",
            header_color="blue", header_bold=True,
            children=[_TextComp("x")],
        )
        result = panel.update({
            "header": "H",
            "footer": "F",
            "header_color": "blue",
            "header_bold": True,
        })
        assert result is False

    def test_update_header_change_returns_true(self):
        """header 变更时返回 True。"""
        panel = Panel(header="Old", children=[_TextComp("x")])
        result = panel.update({"header": "New"})
        assert result is True

    def test_update_footer_change_returns_true(self):
        """footer 变更时返回 True。"""
        panel = Panel(footer="Old", children=[_TextComp("x")])
        result = panel.update({"footer": "New"})
        assert result is True

    def test_update_color_change_returns_true(self):
        """header_color 变更时返回 True。"""
        panel = Panel(header="H", header_color="blue", children=[_TextComp("x")])
        result = panel.update({"header_color": "red"})
        assert result is True

    def test_update_bold_change_returns_true(self):
        """header_bold 变更时返回 True。"""
        panel = Panel(header="H", header_bold=False, children=[_TextComp("x")])
        result = panel.update({"header_bold": True})
        assert result is True

    def test_update_border_style_change_returns_true(self):
        """border_style 变更时返回 True。"""
        panel = Panel(border_style="single", children=[_TextComp("x")])
        result = panel.update({"border_style": "double"})
        assert result is True

    def test_update_box_props_change_returns_true(self):
        """透传 box_props 变更时返回 True。"""
        panel = Panel(children=[_TextComp("x")], padding_x=1)
        result = panel.update({"padding_x": 3})
        assert result is True
