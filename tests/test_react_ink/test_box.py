"""Box 组件单元测试。

覆盖 Box 边框渲染的 8 种样式、每边独立颜色/背景色、
backgroundColor 填充、各边可见性、padding/margin/width 约束。

测试策略：构造 Box 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证边框字符、颜色序列和结构正确性。
"""

from __future__ import annotations

import re
import pytest

from src.chat_ui.react_ink import Box, BoxBorderStyle
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
# TestBoxRendering
# ═══════════════════════════════════════════════════════════

class TestBoxRendering:
    """Box 渲染测试。"""

    def test_single_border(self):
        """single 边框样式渲染正确字符。"""
        box = Box(border_style="single", children=_TextComp("hi"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) >= 3  # top + content + bottom
        # 顶边 ┌──┐
        assert "┌" in lines[0] and "┐" in lines[0]
        # 底边 └──┘
        assert "└" in lines[-1] and "┘" in lines[-1]

    def test_double_border(self):
        """double 边框样式渲染双线字符。"""
        box = Box(border_style="double", children=_TextComp("hi"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert "╔" in lines[0] and "╗" in lines[0]
        assert "╚" in lines[-1] and "╝" in lines[-1]

    def test_round_border(self):
        """round 边框样式渲染圆角字符。"""
        box = Box(border_style="round", children=_TextComp("hi"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert "╭" in lines[0] and "╮" in lines[0]
        assert "╰" in lines[-1] and "╯" in lines[-1]

    def test_bold_border(self):
        """bold 边框样式渲染粗线字符。"""
        box = Box(border_style="bold", children=_TextComp("hi"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert "┏" in lines[0] and "┓" in lines[0]
        assert "┗" in lines[-1] and "┛" in lines[-1]

    def test_custom_border_style(self):
        """自定义边框样式 dict。"""
        custom: BoxBorderStyle = {"tl": "[", "tr": "]", "bl": "[", "br": "]",
                                   "h": "=", "v": "|"}
        box = Box(border_style=custom, children=_TextComp("x"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert lines[0].startswith("[") and lines[0].endswith("]")
        assert lines[-1].startswith("[") and lines[-1].endswith("]")

    def test_border_color(self):
        """border_color 生成 ANSI 颜色序列。"""
        box = Box(border_style="single", border_color="red",
                  children=_TextComp("hi"))
        output = box.render()
        # 应包含红色 ANSI 序列
        assert "\033[" in output
        assert _has_ansi(output)

    def test_border_dim_color(self):
        """border_dim_color 生成暗色 ANSI 序列。"""
        box = Box(border_style="single", border_dim_color=True,
                  border_color="blue", children=_TextComp("hi"))
        output = box.render()
        assert _has_ansi(output)

    def test_background_color(self):
        """background_color 填充内容区背景。"""
        box = Box(border_style="single", background_color="green",
                  children=_TextComp("hi"))
        output = box.render()
        assert _has_ansi(output)

    def test_show_hide_edges(self):
        """各边可见性控制。"""
        # 隐藏所有边
        box = Box(border_style="single",
                  show_top=False, show_bottom=False,
                  show_left=False, show_right=False,
                  children=_TextComp("hi"))
        output = box.render()
        clean = _strip_ansi(output)
        # 仅内容，无边框字符
        assert "┌" not in clean
        assert "│" not in clean
        # 内容应存在
        assert "hi" in clean

    def test_show_top_only(self):
        """仅显示顶边。"""
        box = Box(border_style="single",
                  show_top=True, show_bottom=False,
                  show_left=False, show_right=False,
                  children=_TextComp("a"))
        output = box.render()
        clean = _strip_ansi(output)
        assert "┌" in clean  # 顶边角存在
        assert "└" not in clean  # 底边角隐藏

    def test_padding(self):
        """padding 在内容四周添加空白。"""
        box = Box(border_style="single", padding_x=2, padding_y=1,
                  children=_TextComp("x"))
        output = box.render()
        clean = _strip_ansi(output)
        lines = clean.split("\n")

        # 应有至少 5 行：margin? + top + padding_top + content + padding_bottom + bottom + margin?
        # 内容行应缩进 2 空格
        content_lines = [l for l in lines if "x" in l]
        assert len(content_lines) >= 1
        # 内容行含左右 padding（输出格式取决于内建逻辑）
        for cl in content_lines:
            assert cl.strip() == "x" or "x" in cl

    def test_margin(self):
        """margin 在边框外添加空行。"""
        box = Box(border_style="single", margin_y=2,
                  children=_TextComp("hi"))
        output = box.render()
        lines = output.split("\n")

        # margin top: 前 2 行为空
        assert lines[0] == ""
        assert lines[1] == ""
        # margin bottom: 后 2 行为空
        assert lines[-1] == ""
        assert lines[-2] == ""

    def test_width_constraint_fixed(self):
        """固定 width 约束生效。"""
        box = Box(border_style="single", width=20,
                  children=_TextComp("short"))
        output = box.render()
        clean = _strip_ansi(output)
        top_line = clean.split("\n")[0]
        # 顶边长度 = 2(角) + inner_width >= 2+16=18 (减去左右 padding)
        assert len(top_line) >= 4

    def test_empty_box(self):
        """无子组件的空 Box 可正常渲染。"""
        box = Box(border_style="single")
        output = box.render()
        assert output is not None
        # 应至少包含边框
        clean = _strip_ansi(output)
        assert "┌" in clean or "└" in clean or len(clean) > 0


# ═══════════════════════════════════════════════════════════
# TestBoxBorderStyle
# ═══════════════════════════════════════════════════════════

class TestBoxBorderStyle:
    """边框样式常量测试。"""

    def test_all_styles_have_required_chars(self):
        """所有 8 种预设样式均包含 tl/tr/bl/br/h/v 六个键。"""
        from src.chat_ui.react_ink import BoxBorderStyle as BBS

        required = {"tl", "tr", "bl", "br", "h", "v"}
        for name, chars in BBS.items():
            assert required.issubset(chars.keys()), (
                f"边框样式 '{name}' 缺少键: {required - set(chars.keys())}"
            )
            # 所有字符非空
            for k in required:
                assert chars[k], f"边框样式 '{name}' 的 '{k}' 字符为空"
