"""TUI 流式 Markdown 端到端测试（公式 / HTML 增强，2026-10）。

经 ``AppModel`` + ``apply._do_content``（TUI 内容块真实路径，
``AnsiStreamRenderer`` 流式累积 → 内容块行）验证新增的公式与 HTML 渲染在
TUI 里真实生效，且分段流式输入与一次性输入结果一致。
"""

from __future__ import annotations


def _content_lines(text: str, chunks: int = 1, width: int = 80) -> list[str]:
    """经 TUI 内容块路径渲染文本，返回最终块的行文本。"""
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = width
    if chunks <= 1:
        _do_content(m, ContentCmd(text=text))
    else:
        step = max(1, len(text) // chunks)
        for i in range(0, len(text), step):
            _do_content(m, ContentCmd(text=text[i:i + step]))
    _do_content(m, ContentCmd(text="\n"))
    blk = m.blocks[m.content_block_index]
    return [line.plain for line in blk.lines]


def test_tui_math_enhancements_render():
    text = _content_lines(
        "$$\n\\sum_{i=1}^{n} \\frac{1}{i}\n$$\n"
        "$$\n\\begin{array}{c|c} a & b \\\\ \\hline c & d \\end{array}\n$$\n"
        "行内 $\\mathbb{R}^n$ 与 $\\overset{def}{=}$\n"
    )
    joined = "\n".join(text)
    assert "\u2211" in joined            # ∑
    assert "\u2500" in joined            # 分数线 / 表格线
    assert "\U0001D54D" in joined or "\u211d" in joined   # ℝ
    # 行内公式同样二维渲染：``\overset{def}{=}`` 的标注 ``def`` 位于主体上方
    # （不再压缩为 Unicode 上标 ``ᵈᵉᶠ``——这是「行内公式多行渲染」的预期行为）
    assert "def" in joined
    assert "=" in joined


def test_tui_html_enhancements_render():
    text = _content_lines(
        "<script>\nvar secret = 1;\n</script>\n"
        '<progress value="70" max="100"></progress>\n'
        '<input type="checkbox" checked> 完成\n'
        '<pre class="language-python">\ndef f():\n    return 1\n</pre>\n'
        "<dl>\n<dt>术语</dt>\n<dd>定义</dd>\n</dl>\n"
    )
    joined = "\n".join(text)
    assert "secret" not in joined          # 脚本内容隐藏
    assert "70%" in joined                 # 进度条
    assert "\u2611" in joined              # ☑
    assert "```python" in joined           # 语言推断
    assert "术语" in joined and "定义" in joined


def test_tui_stream_chunked_matches_whole():
    md = ("<div>\n<p>段落</p>\n</div>\n"
          "$$\n\\frac{a}{b} + \\begin{cases} x & y \\\\ z & w \\end{cases}\n$$\n"
          '<img src="a.png" alt="图">\n')
    whole = _content_lines(md, chunks=1)
    chunked = _content_lines(md, chunks=12)
    assert chunked == whole
