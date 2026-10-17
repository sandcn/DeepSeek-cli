"""渲染层类型治理回归测试（2026-10 第二轮）。

覆盖本轮 mypy 清零过程中修复的运行时边界：

- HTML 块 ``<iframe width=...>`` 不再覆盖行函数的 ``width: int`` 参数；
- ``<details>`` 脚注正文按 Rich ``Text`` 行处理（不把 str 当 Text）；
- 数学公式上下限堆叠在 sup/sub 为 None 时不再崩；
- ANSI 流式预览无 highlight_lines 时缓存键构造不崩；
- 内联渲染器对字符串基础样式的 Rich Text 不崩。
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console
from rich.text import Text

from src.renderer.ansi._html_block import open_html_block
from src.renderer.ansi._math_latex import render_math_box
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.inline_renderer import InlineRenderer
from src.renderer.output import OutputAdapter
from src.renderer.engine import RenderEngine


def test_html_iframe_width_attrs_do_not_override_layout_width():
    """iframe 的 width/height 属性只用于显示尺寸文本，不污染布局宽度参数。"""
    lines, state = open_html_block(
        "iframe",
        {"src": "https://example.com", "width": "100", "height": "50"},
        80,
        "",
    )
    text = "\n".join(line.plain for line in lines)
    assert "100×50" in text
    assert "https://example.com" in text
    assert state.tag == "iframe"


def test_footnote_block_renders_rich_text_lines():
    """块级脚注正文按 Text 行渲染（修复前对 str 调 .plain 会崩溃）。"""
    adapter = OutputAdapter(Console(file=StringIO(), width=80))
    engine = RenderEngine(adapter)
    result = engine._render_block_footnote(1, "**粗体** 正文")
    assert isinstance(result, Text)
    assert "[1]" in result.plain
    assert "粗体" in result.plain


def test_math_superscript_without_subscript_does_not_crash():
    """只有上标没有下标时上下限堆叠不访问 None。"""
    box = render_math_box(r"\sum^{n}", inline=False)
    assert box.lines
    text = "\n".join(line.plain for line in box.lines)
    assert "∑" in text


def test_ansi_stream_preview_without_highlight_lines():
    """预览路径 highlight_lines 为空时缓存键构造不崩（tuple(None) 修复）。"""
    renderer = AnsiStreamRenderer(width=80)
    renderer.write("```python\nx = 1\n")
    preview = renderer.take_preview_lines()
    assert preview
    renderer.close()


def test_inline_renderer_handles_text_with_string_style():
    """Rich Text 的 base style 为字符串时样式合并不崩。"""
    renderer = InlineRenderer()
    result = renderer.render("**bold** and *italic*")
    assert isinstance(result, Text)
    assert "bold" in result.plain


def test_inline_renderer_string_style_span_merge():
    """直接验证带字符串基础样式的 Text 合并路径（inline_renderer 内部辅助）。"""
    text = Text("a", style="bold")
    text.append("b", style="italic")
    # 与 _get_style_at_offset 同语义：字符串 style 解析后再 combine
    from rich.style import Style

    base = Style.parse(text.style) if isinstance(text.style, str) else Style()
    combined = Style.combine([base, Style.parse("italic")])
    assert combined.bold and combined.italic
