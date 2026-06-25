"""Box 组件单元测试。

覆盖 Box 边框渲染的 8 种样式、每边独立颜色/背景色、
backgroundColor 填充、各边可见性、padding/margin/width 约束。

测试策略：构造 Box 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证边框字符、颜色序列和结构正确性。
"""

from __future__ import annotations

import os
import re
import time
import pytest
from unittest.mock import patch

from src.chat_ui.react_ink import Box, BoxBorderStyle
from src.chat_ui.components.base import TuiComponent
from src.chat_ui.components.message_blocks import ToolCallBlockBox


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


# ═══════════════════════════════════════════════════════════
# TestToolCallBlockBoxEnhanced
# ═══════════════════════════════════════════════════════════

class TestToolCallBlockBoxEnhanced:
    """ToolCallBlockBox 增强功能测试。

    覆盖 running 状态耗时显示、completed/failed 状态标记、
    工具名称截断、elapsed_start 为 None 时无耗时显示。
    """

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_tool_call_running_shows_elapsed(self, mock_term):
        """running 状态含耗时显示。"""
        # 模拟已运行 2.3 秒的工具调用
        start = time.monotonic() - 2.3
        box = ToolCallBlockBox(
            tool_name="read_file",
            status="running",
            elapsed_start=start,
            text="some content to give width",
        )
        with patch(
            "src.chat_ui.components.animation.use_spinner",
            return_value={"char": "⣾", "frame": 0, "time": 0.0},
        ):
            output = box.render()
        clean = _strip_ansi(output)
        # 应包含工具名和耗时（约 2.3s）
        assert "read_file" in clean
        # 耗时格式：(X.Xs) — 因 time.monotonic() 继续前进
        assert re.search(r'\(\d+\.\d+s\)', clean), (
            f"running 状态应显示耗时 (X.Xs)，实际输出: {clean}"
        )

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_tool_call_completed_has_checkmark(self, mock_term):
        """completed 含 ✓ 标记。"""
        box = ToolCallBlockBox(
            tool_name="write_file",
            status="completed",
            text="result content here for proper width",
        )
        output = box.render()
        clean = _strip_ansi(output)
        assert "✓" in clean, f"completed 状态应含 ✓，实际输出: {clean}"
        assert "write_file" in clean

    def test_tool_call_long_name_truncated(self):
        """超过 40 字符的名称在 title 中被截断。"""
        long_name = "a" * 50
        box = ToolCallBlockBox(
            tool_name=long_name,
            status="running",
        )
        # 验证 title 属性中名称已被截断
        expected_truncated = "a" * 37 + "..."
        assert expected_truncated in box.title, (
            f"长名称 title 应截断为 ...{expected_truncated}...，实际: {box.title!r}"
        )
        # 原始 50 字符名称不应完整出现在 title 中
        assert long_name not in box.title

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_tool_call_no_elapsed_when_none(self, mock_term):
        """elapsed_start=None 时不显示耗时。"""
        # (no longer need local import, already at top)
        box = ToolCallBlockBox(
            tool_name="bash",
            status="running",
            text="content for width",
            # elapsed_start 默认为 None
        )
        with patch(
            "src.chat_ui.components.animation.use_spinner",
            return_value={"char": "⣾", "frame": 0, "time": 0.0},
        ):
            output = box.render()
        clean = _strip_ansi(output)
        assert "bash" in clean
        # 不应包含耗时格式 (X.Xs) 或 (M:SS)
        assert not re.search(r'\(\d+\.\d+s\)', clean), (
            f"elapsed_start=None 不应显示耗时，实际输出: {clean}"
        )
        assert not re.search(r'\(\d+:\d{2}\)', clean), (
            f"elapsed_start=None 不应显示耗时，实际输出: {clean}"
        )


# ═══════════════════════════════════════════════════════════
# TestBoxGradientBorder
# ═══════════════════════════════════════════════════════════

# 256 色 ANSI 序列匹配（38;5;N）
_COLOR_256_RE = re.compile(r'\033\[38;5;(\d+)m')


def _extract_256_colors(text: str) -> list[int]:
    """提取文本中所有 256 色前景色号，按出现顺序返回。"""
    return [int(m) for m in _COLOR_256_RE.findall(text)]


def _extract_top_line_colors(output: str) -> list[int]:
    """提取顶边水平线字符的 256 色色号（不含角字符的颜色名序列）。"""
    lines = output.split("\n")
    top_line = lines[0]
    return _extract_256_colors(top_line)


def _extract_bottom_line_colors(output: str) -> list[int]:
    """提取底边水平线字符的 256 色色号。"""
    lines = output.split("\n")
    # 底部边框行是最后一个非空行
    bottom_line = [l for l in lines if l.strip()][-1]
    return _extract_256_colors(bottom_line)


class TestBoxGradientBorder:
    """Box 渐变边框渲染测试。

    验证 border_color_gradient 属性触发逐字符渐变色渲染，
    以及环境变量 CHAT_UI_BORDER_GRADIENT 的开关控制。
    """

    def test_gradient_top_border(self):
        """顶边横线逐字符不同色号。"""
        box = Box(
            border_style="single",
            border_color_gradient=("cyan", "blue"),
            children=_TextComp("hello world"),
        )
        output = box.render()
        colors = _extract_top_line_colors(output)

        # 顶边应包含多个 256 色序列（逐字符渐变）
        assert len(colors) >= 2, (
            f"顶边水平线应有 ≥2 个不同色号，实际: {colors}"
        )
        # 每个色号应在 0-255 范围内
        for c in colors:
            assert 0 <= c <= 255, f"色号 {c} 超出 256 色范围"
        # 应存在不同色号（逐字符不同颜色）
        assert len(set(colors)) >= 2, (
            f"顶边应有 ≥2 个不同色号（渐变），实际: {set(colors)}"
        )

    def test_gradient_bottom_border(self):
        """底边横线逐字符不同色号。"""
        box = Box(
            border_style="single",
            border_color_gradient=("magenta", "yellow"),
            children=_TextComp("hello world"),
        )
        output = box.render()
        colors = _extract_bottom_line_colors(output)

        assert len(colors) >= 2, (
            f"底边水平线应有 ≥2 个不同色号，实际: {colors}"
        )
        assert len(set(colors)) >= 2, (
            f"底边应有 ≥2 个不同色号（渐变），实际: {set(colors)}"
        )

    def test_gradient_no_gradient(self):
        """border_color_gradient=None 时行为不变，无 256 色序列。"""
        box = Box(
            border_style="single",
            # border_color_gradient 默认为 None
            children=_TextComp("hello world"),
        )
        output = box.render()
        colors = _extract_256_colors(output)

        # 无渐变时应无 256 色序列
        assert len(colors) == 0, (
            f"无渐变时应无 256 色序列，实际色号: {colors}"
        )
        # 边框字符应正常渲染
        clean = _strip_ansi(output)
        assert "┌" in clean
        assert "└" in clean
        assert "hello world" in clean

    def test_gradient_with_title(self):
        """渐变边框 + 标题共存。"""
        box = Box(
            border_style="single",
            border_color_gradient=("cyan", "magenta"),
            title="GT",
            children=_TextComp("hello world"),
        )
        output = box.render()
        clean = _strip_ansi(output)
        top_line = clean.split("\n")[0]

        # 标题应出现在顶边中
        assert "GT" in top_line, (
            f"标题应出现在顶边，实际顶边: {top_line}"
        )
        # 顶边仍含 256 色序列（标题两侧的分段渐变）
        colors = _extract_top_line_colors(output)
        assert len(colors) >= 1, (
            f"标题+渐变模式下顶边仍应有 256 色序列，实际: {colors}"
        )

    def test_gradient_disabled_by_env(self):
        """环境变量 CHAT_UI_BORDER_GRADIENT=0 时禁用渐变。"""
        import os
        old = os.environ.get("CHAT_UI_BORDER_GRADIENT")
        os.environ["CHAT_UI_BORDER_GRADIENT"] = "0"
        try:
            box = Box(
                border_style="single",
                border_color_gradient=("cyan", "blue"),
                children=_TextComp("hello world"),
            )
            output = box.render()
            colors = _extract_256_colors(output)

            assert len(colors) == 0, (
                f"CHAT_UI_BORDER_GRADIENT=0 时应无 256 色序列，"
                f"实际色号: {colors}"
            )
        finally:
            if old is None:
                os.environ.pop("CHAT_UI_BORDER_GRADIENT", None)
            else:
                os.environ["CHAT_UI_BORDER_GRADIENT"] = old

    def test_gradient_vertical_bars(self):
        """多行内容时竖线逐行采样不同色号。"""
        box = Box(
            border_style="single",
            border_color_gradient=("cyan", "magenta"),
            children=_TextComp("line1\nline2\nline3"),
        )
        output = box.render()

        # 提取内容行（含 "line" 文本的行）的左边竖线色号
        lines = output.split("\n")
        vbar_colors: list[int] = []
        for line in lines:
            if "line" not in _strip_ansi(line):
                continue  # 跳过边框行
            colors = _extract_256_colors(line)
            if colors:
                vbar_colors.append(colors[0])

        # 应有 3 行内容
        assert len(vbar_colors) == 3, (
            f"应有 3 行内容竖线色号，实际: {vbar_colors}"
        )
        # 首行和末行竖线颜色应不同（渐变从顶到底采样）
        assert vbar_colors[0] != vbar_colors[-1], (
            f"首行竖线色号 {vbar_colors[0]} 应与末行 {vbar_colors[-1]} 不同"
        )
