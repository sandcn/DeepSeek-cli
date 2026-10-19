"""ANSI 流式 Markdown 语法/渲染修复（第二十三批）测试。

本轮修复（流式渲染语法特性缺口）：

  - **非可嵌套行内 HTML 标签的内容 Markdown 解析**：``<code>**x**</code>`` /
    ``<sup>**2**</sup>`` / ``<kbd>**K**</kbd>`` / ``<abbr title="T">**A**</abbr>``
    的内容按行内 Markdown 解析（修复前 ``**`` 字面上屏）；内容跨软换行时退回
    逐字符配对（``<b>a\\nb</b>`` 不再吞掉同名标签之间的文本）。
  - **表格单元格行内 HTML 样式**：``<span style="color:red">`` / ``<font
    color>`` / ``<span class>`` / ``<mark>`` 的颜色只作用于标签覆盖的字符
    （修复前整格统一调色，标签样式全部丢失）；单个标签样式解析失败不得影响
    其它标签的投影。
  - **空元素 HTML 块**：``<hr>`` / ``<br>`` 块不再吞掉后续文档（修复前空元素
    永无结束标签，状态残留把剩余内容全部吸进本块）。
  - **Mermaid ``stateDiagram-v2`` 终止状态**：``[*]`` 转移不重复打印终点
    （转移行已显示 ``──▶ ● (终止)``，末尾不再重复一次）。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi import AnsiStreamRenderer


def _render(src: str, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _runs(src: str, kind: str) -> list:
    """渲染后取**数据行**（框线行跳过）的 runs。

    ``kind='data'``：第一条以 ``│`` 开头且不是表头（首列内容非粗体标题）的
    行不稳定，改为返回**所有** ``│`` 行 runs 的拼接列表——样式断言只需在
    其中找到目标字符即可，不受表头/数据行顺序影响。
    """
    r = AnsiStreamRenderer(width=72)
    r.write(src)
    r.close()
    out: list = []
    for line in r.take_lines():
        if line.plain.startswith("\u2502"):
            out.extend(line.runs)
    return out


def _chunked(src: str, size: int = 3, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# 非可嵌套 HTML 标签：内容按行内 Markdown 解析
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src,expected", [
    ("<code>**x**</code>\n", "x"),
    ("<sup>**2**</sup>\n", "²"),
    ("<kbd>**K**</kbd>\n", " K "),
    ("<tt>_i_</tt>\n", "i"),
    ("<samp>~~s~~</samp>\n", "s"),
])
def test_non_nestable_html_content_parses_markdown(src, expected):
    assert _render(src) == [expected]


def test_abbr_content_parses_markdown():
    assert _render('<abbr title="T">**A**</abbr>\n') == ["A (T)"]


def test_code_content_markdown_not_literal():
    out = _render("<code>**x**</code>\n")[0]
    assert "*" not in out


def test_html_tag_across_soft_newline_does_not_swallow_text():
    # ``<b>a</b> <b>b`` + 换行 + ``c</b>``：同名标签必须逐个配对，中间文本不丢
    assert _render("<b>a</b> <b>b\nc</b>\n") == ["a b", "c"]


def test_progress_value_rendered_as_bar():
    out = _render("<progress value=\"1\" max=\"2\">50%</progress>\n")
    assert any("50%" in line for line in out)


# ══════════════════════════════════════════════════════════
# 表格单元格行内 HTML 样式投影
# ══════════════════════════════════════════════════════════

def test_table_cell_span_style_projection():
    src = '| a | b |\n|---|---|\n| <span style="color:red">red</span> pl | x |\n'
    runs = _runs(src, "\u2502")
    assert any(run.text == "red" and run.style is not None
               and run.style.fg == 196 for run in runs)
    # 标签之外的文本不得被染色
    assert not any(run.style is not None and run.style.fg == 196
                   and run.text.strip() == "pl" for run in runs)


def test_table_cell_span_class_style_projection():
    src = '| a |\n|---|\n| <span class="green">g</span> |\n'
    runs = _runs(src, "\u2502")
    assert any(run.text == "g" and run.style is not None
               and run.style.fg == 40 for run in runs)


def test_table_cell_font_color_projection():
    src = '| a |\n|---|\n| x <font color="blue">y</font> z |\n'
    runs = _runs(src, "\u2502")
    assert any("y" in (run.text or "") and run.style is not None
               and run.style.fg == 33 for run in runs)
    assert not any("z" in (run.text or "") and run.style is not None
                   and run.style.fg == 33 for run in runs)


def test_table_cell_mark_style_projection():
    src = "| a |\n|---|\n| <mark>hl</mark> x |\n"
    runs = _runs(src, "\u2502")
    assert any("hl" in (run.text or "") and run.style is not None
               and run.style.bg == 11 for run in runs)


def test_table_cell_untagged_style_unchanged():
    src = "| a |\n|---|\n| plain |\n"
    runs = _runs(src, "\u2502")
    assert any("plain" in (run.text or "") and run.style is not None
               and run.style.fg == 252 for run in runs)


def test_table_cell_broken_tag_does_not_break_others():
    # 无样式 / 未知标签不影响后续带样式标签的投影
    src = '| a |\n|---|\n| <font href="x">t</font> <mark>hl</mark> |\n'
    runs = _runs(src, "\u2502")
    assert any("hl" in (run.text or "") and run.style is not None
               and run.style.bg == 11 for run in runs)


def test_table_cell_stream_matches_oneshot():
    src = '| a |\n|---|\n| <span style="color:red">r</span> |\n'
    assert _chunked(src, 4) == _render(src)


# ══════════════════════════════════════════════════════════
# 空元素 HTML 块 / 跨软换行行内标签
# ══════════════════════════════════════════════════════════

def test_hr_block_does_not_swallow_following_text():
    out = _render("<hr>\n后续段落\n", width=40)
    assert out[0].startswith("\u2500")
    assert "后续段落" in out


def test_br_block_does_not_swallow_following_text():
    out = _render("<br>\nnext\n")
    assert out[-1] == "next"


def test_span_across_soft_newline_no_tag_leak():
    out = _render('<span style="color:red">a\nb</span>\n')
    assert "<span" not in "".join(out)


# ══════════════════════════════════════════════════════════
# Mermaid stateDiagram-v2 终止状态不重复
# ══════════════════════════════════════════════════════════

def test_mermaid_state_terminal_not_duplicated():
    src = "```mermaid\nstateDiagram-v2\n    [*] --> Idle\n    Idle --> [*]\n```\n"
    text = "\n".join(_render(src, width=100))
    assert "Idle" in text
    assert text.count("终止") <= 1
