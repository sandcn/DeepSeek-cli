"""ANSI HTML 渲染增强测试（第二批，TUI 流式路径）。

覆盖本轮 ``src/renderer/ansi/_html_block.py``、``src/renderer/_block_helpers.py``、
``src/renderer/inline_nodes.py``、``src/renderer/_inline_html.py`` 的增强：

  - 块级控件语义：``<button>`` / ``<select>/<option>`` / ``<textarea>`` /
    ``<object>`` / ``<iframe src>`` / ``<audio|video src>``；
  - 媒体源 ``srcset`` 支持；
  - 单行嵌套列表（``<ul><li>…</li></ul>``）结构化；
  - ``<svg>`` 的 ``<title>`` / ``<desc>`` 提取；
  - 行内 ``<progress>`` / ``<meter>`` 进度条；
  - ``<details open>`` 展开图标；
  - 同行多空元素的递归渲染（不吞后续内容）。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _render(md: str, width: int = 78) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _text(md: str, width: int = 78) -> str:
    return "\n".join(_render(md, width))


def _inline(text: str) -> str:
    return "".join(r.text for r in render_inline(text))


# ── 表单控件 ─────────────────────────────────────────────


def test_button_renders_bracket_label():
    out = _text("<button>提交</button>\n")
    assert "[ 提交 ]" in out
    assert "<button>" not in out


def test_button_disabled_note():
    out = _text('<button disabled>提交</button>\n')
    assert "[ 提交 ]" in out
    assert "禁用" in out


def test_button_inside_form():
    out = _text("<form>\n<button>确定</button>\n</form>\n")
    assert "[ 确定 ]" in out


def test_select_renders_options():
    out = _text("<select><option>甲</option><option selected>乙</option></select>\n")
    assert "下拉列表" in out
    assert "○ 甲" in out
    assert "◉ 乙" in out
    assert "<option>" not in out


def test_textarea_renders_content_and_rows():
    out = _text('<textarea rows="4" placeholder="请输入">正文</textarea>\n')
    assert "文本域" in out
    assert "4 行" in out
    assert "正文" in out


def test_object_renders_data_and_type():
    out = _text('<object data="a.pdf" type="application/pdf"></object>\n')
    assert "对象" in out
    assert "a.pdf" in out
    assert "application/pdf" in out


def test_iframe_renders_src_and_dimensions():
    out = _text('<iframe src="https://example.com" width="600" height="400"></iframe>\n')
    assert "内嵌" in out
    assert "https://example.com" in out
    assert "600×400" in out


def test_audio_and_video_show_src():
    out = _text('<audio controls src="a.mp3"></audio>\n<video src="b.mp4"></video>\n')
    assert "a.mp3" in out
    assert "b.mp4" in out


# ── 媒体源 ───────────────────────────────────────────────


def test_source_srcset_first_candidate():
    out = _text('<video>\n<source srcset="a.webp 1x, b.webp 2x" type="image/webp">\n</video>\n')
    assert "a.webp" in out
    assert "b.webp" not in out


def test_source_and_img_same_line_both_rendered():
    out = _text('<picture><source srcset="a.webp"><img src="a.png" alt="图"></picture>\n')
    assert "a.webp" in out
    assert "a.png" in out
    assert "图" in out


# ── 单行嵌套列表 ─────────────────────────────────────────


def test_single_line_unordered_list_in_container():
    out = _text("<article>\n<ul><li>一</li><li>二</li></ul>\n</article>\n")
    assert "• 一" in out
    assert "• 二" in out
    assert "<li>" not in out


def test_single_line_ordered_list_numbering():
    out = _text('<ol start="3"><li>甲</li><li>乙</li></ol>\n')
    assert "3. 甲" in out
    assert "4. 乙" in out


def test_single_line_list_keeps_inline_format():
    out = _text("<ul><li><b>粗</b>体</li></ul>\n")
    assert "粗体" in out
    assert "<b>" not in out


def test_nested_single_line_list():
    out = _text("<ul><li>外<ul><li>内</li></ul></li></ul>\n")
    assert "外" in out
    assert "内" in out


# ── SVG 标题 ─────────────────────────────────────────────


def test_svg_title_extracted():
    out = _text("<svg width=\"10\" height=\"10\">\n<title>架构图</title>\n<circle r=\"2\"/>\n</svg>\n")
    assert "架构图" in out
    assert "circle" not in out


def test_svg_desc_extracted():
    out = _text("<svg>\n<title>T</title>\n<desc>说明文字</desc>\n</svg>\n")
    assert "T" in out
    assert "说明文字" in out


# ── 行内进度控件 ─────────────────────────────────────────


def test_inline_progress_bar():
    out = _inline('进度 <progress value="70" max="100"></progress> 结束')
    assert "█" in out and "░" in out
    assert "70%" in out
    assert "进度" in out and "结束" in out


def test_inline_meter_bar():
    out = _inline('<meter value="0.25"></meter>')
    assert "25%" in out


def test_inline_progress_in_paragraph_render():
    out = _text('进度 <progress value="50" max="100"></progress> 完成\n')
    assert "50%" in out
    assert "完成" in out


# ── 折叠块 ───────────────────────────────────────────────


def test_details_without_open_uses_collapsed_icon():
    out = _text("<details>\n<summary>标题</summary>\n正文\n</details>\n")
    assert "▶ 标题" in out


def test_details_with_open_uses_expanded_icon():
    out = _text("<details open>\n<summary>标题</summary>\n正文\n</details>\n")
    assert "▼ 标题" in out


# ── 其它 ─────────────────────────────────────────────────


def test_colgroup_and_col_silent():
    out = _text('<table>\n<colgroup><col span="2"></colgroup>\n<tr><td>a</td></tr>\n</table>\n')
    assert "colgroup" not in out
    assert "a" in out


def test_progress_block_still_renders():
    out = _text('<progress value="30" max="100"></progress>\n')
    assert "30%" in out


def test_existing_html_behaviour_unchanged():
    out = _text("<div>\n  <b>粗体</b> 与 &amp; 实体\n</div>\n")
    assert "粗体" in out
    assert "&" in out
    assert "<b>" not in out
