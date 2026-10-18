"""渲染层类型治理回归测试（2026-10 第二轮）。

覆盖本轮 mypy 清零过程中修复的运行时边界：

- HTML 块 ``<iframe width=...>`` 不再覆盖行函数的 ``width: int`` 参数；
- 数学公式上下限堆叠在 sup/sub 为 None 时不再崩；
- ANSI 流式预览无 highlight_lines 时缓存键构造不崩。
"""

from __future__ import annotations

from src.renderer.ansi._html_block import open_html_block
from src.renderer.ansi._math_latex import render_math_box
from src.renderer.ansi import AnsiStreamRenderer


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
