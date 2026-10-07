"""ANSI HTML 渲染增强测试（TUI 流式路径，2026-10 扩展）。

覆盖 ``src/renderer/_html_attrs.py``、``src/renderer/_block_parser.py``（HTML
块结构化）与 ``src/renderer/ansi/_html_block.py`` 的新增能力：

  - 原始内容标签（script/style/template/noscript/canvas/svg/math）内容隐藏；
  - 布局属性（``<center>`` / ``align=center`` / ``style=text-align``）居中；
  - 表单控件（``<input type=checkbox|radio>``）与度量控件
    （``<progress>`` / ``<meter>``）语义化；
  - 空元素单行（``<img>`` / ``<source>`` / ``<track>`` / ``<br>``）语义化，
    且不吞后续内容；
  - ``<pre>`` 语言推断（``class="language-x"``）、``<table>`` 列对齐、
    ``<dl>`` 定义列表、容器内块级文本标签；
  - 行内 HTML（``<font color>`` / ``<small>`` / ``<big>`` / ``<q>`` /
    ``<acronym>`` / ``<wbr>`` / 透明容器 / 行内 script 隐藏）；
  - HTML 实体表扩展、流式分段写入一致性。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _render(md: str, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(md)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _inline(text: str) -> str:
    return "".join(r.text for r in render_inline(text))


# ── 原始内容标签 ──────────────────────────────────────────


def test_script_content_hidden():
    lines = _render("<script>\nvar x = 1;\nfunction f(){}\n</script>\n")
    joined = "\n".join(lines)
    assert "var x" not in joined
    assert "function" not in joined
    assert "script" in joined
    assert any("省略" in ln for ln in lines)


def test_style_content_hidden():
    lines = _render("<style>\n.x { color: red }\n</style>\n")
    joined = "\n".join(lines)
    assert "color: red" not in joined
    assert "省略" in joined


def test_svg_and_canvas_content_hidden():
    for tag, content in (("svg", '<path d="M0 0"/>'), ("canvas", "ctx.fill()")):
        lines = _render(f"<{tag}>\n{content}\n</{tag}>\n")
        joined = "\n".join(lines)
        assert content.split("(")[0].split(" ")[0] not in joined or "省略" in joined
        assert any("省略" in ln for ln in lines)


def test_noscript_hidden_with_count():
    lines = _render("<noscript>\n请启用 JS\n</noscript>\n")
    assert any("已省略 1 行" in ln for ln in lines)


def test_raw_tag_single_line_no_content_leak():
    lines = _render("<style>.a{color:red}</style>\n")
    joined = "\n".join(lines)
    assert "color:red" not in joined


# ── 居中 ──────────────────────────────────────────────────


def test_center_tag_aligns_content():
    lines = _render("<center>\n居中文字\n</center>\n", width=40)
    body = next(ln for ln in lines if "居中文字" in ln)
    assert body != "居中文字"
    assert body.index("居中文字") > 0


def test_div_align_center():
    lines = _render('<div align="center">\n文字\n</div>\n', width=40)
    body = next(ln for ln in lines if "文字" in ln)
    assert body.index("文字") > 0


def test_div_style_text_align_center():
    lines = _render('<div style="text-align: center">\n文字\n</div>\n', width=40)
    body = next(ln for ln in lines if "文字" in ln)
    assert body.index("文字") > 0


def test_center_without_width_is_safe():
    lines = _render("<center>\n文字\n</center>\n", width=0)
    assert any("文字" in ln for ln in lines)


# ── 控件 / 度量 ───────────────────────────────────────────


def test_progress_bar():
    lines = _render('<progress value="70" max="100"></progress>\n')
    bar = next(ln for ln in lines if "70%" in ln)
    assert "\u2588" in bar            # █ 已完成
    assert "\u2591" in bar            # ░ 未完成
    assert "70/100" in bar


def test_meter_bar_color_by_ratio():
    lines = _render('<meter value="0.2" max="1"></meter>\n')
    assert any("20%" in ln for ln in lines)


def test_progress_without_value():
    lines = _render('<progress max="100"></progress>\n')
    assert any("0%" in ln for ln in lines)


def test_input_checkbox_checked():
    lines = _render('<input type="checkbox" checked> 完成\n')
    assert any("\u2611" in ln and "完成" in ln for ln in lines)


def test_input_checkbox_unchecked():
    lines = _render('<input type="checkbox"> 未完成\n')
    assert any("\u2610" in ln and "未完成" in ln for ln in lines)


def test_input_radio_and_button():
    assert any("\u25c9" in ln for ln in _render('<input type="radio" checked>\n'))
    assert any("[ 提交 ]" in ln for ln in _render('<input type="submit" value="提交">\n'))


def test_input_in_form_block():
    lines = _render('<form>\n<input type="checkbox" checked> 选项\n</form>\n')
    assert any("\u2611" in ln and "选项" in ln for ln in lines)


# ── 空元素单行 ────────────────────────────────────────────


def test_img_line_rendered_with_alt_and_size():
    lines = _render('<img src="a.png" alt="示意图" width="10" height="5">\n')
    joined = "\n".join(lines)
    assert "示意图" in joined and "a.png" in joined and "10x5" in joined
    assert "<img" not in joined


def test_img_with_trailing_text_keeps_text():
    lines = _render('<img src="a.png"> 后续说明\n')
    assert any("a.png" in ln and "后续说明" in ln for ln in lines)


def test_source_and_track_lines():
    lines = _render('<source src="a.mp4" type="video/mp4">\n<track src="a.vtt" kind="subtitles">\n')
    joined = "\n".join(lines)
    assert "媒体源" in joined and "字幕轨" in joined
    assert "video/mp4" in joined and "subtitles" in joined
    assert "<source" not in joined and "<track" not in joined


def test_br_line_produces_blank():
    lines = _render("<br>\n")
    assert lines and lines[0].strip() == ""
    assert "<br>" not in "\n".join(lines)


def test_void_line_does_not_swallow_following_content():
    lines = _render("<br>\n后续段落文字\n")
    assert any("后续段落文字" in ln for ln in lines)


def test_meta_silent():
    lines = _render('<meta charset="utf-8">\n正文\n')
    joined = "\n".join(lines)
    assert "<meta" not in joined
    assert any("正文" in ln for ln in lines)


# ── 解析层结构化增强 ──────────────────────────────────────


def test_pre_language_from_class():
    lines = _render('<pre class="language-python">\ndef f():\n    return 1\n</pre>\n')
    assert lines[0].startswith("```python")


def test_pre_language_from_inner_code_tag():
    lines = _render('<pre>\n<code class="language-js">var x = 1;</code>\n</pre>\n')
    assert lines[0].startswith("```js")
    assert "var x = 1;" in lines
    assert not any("<code" in ln for ln in lines)


def test_pre_preserves_indentation_and_decodes_entities():
    lines = _render('<pre class="language-python">\ndef f(x):\n    return x &lt; 2\n</pre>\n')
    assert any(ln == "    return x < 2" for ln in lines)
    assert not any("&lt;" in ln for ln in lines)


def test_html_table_column_align():
    lines = _render('<table>\n<tr><th>名称</th><th align="right">数量</th></tr>\n'
                    '<tr><td>a</td><td style="text-align:right">7</td></tr>\n</table>\n')
    row = next(ln for ln in lines if ln.startswith("\u2502") and "7" in ln)
    assert row.strip().endswith("7 \u2502")


def test_html_dl_definition_list():
    lines = _render("<dl>\n<dt>术语</dt>\n<dd>定义一</dd>\n<dd>定义二</dd>\n</dl>\n")
    joined = "\n".join(lines)
    assert "术语" in joined and "定义一" in joined and "定义二" in joined


def test_html_ol_start_and_li_value():
    lines = _render('<ol start="3">\n<li>第三</li>\n<li>第四</li>\n</ol>\n')
    assert "3. 第三" in lines and "4. 第四" in lines
    lines = _render('<ol>\n<li>一</li>\n<li value="9">九</li>\n<li>十</li>\n</ol>\n')
    assert "1. 一" in lines and "9. 九" in lines and "10. 十" in lines


def test_container_block_text_tags_stripped():
    lines = _render("<div>\n<p>第一段</p>\n<h3>小标题</h3>\n<blockquote>引用</blockquote>\n</div>\n")
    joined = "\n".join(lines)
    assert "第一段" in joined and "小标题" in joined
    assert "<p>" not in joined and "<h3>" not in joined
    assert any(ln.startswith("\u2502") for ln in lines)   # 引用前缀


def test_figure_and_media_labels():
    lines = _render('<video controls>\n<source src="a.mp4">\n</video>\n')
    joined = "\n".join(lines)
    assert "video" in joined and "媒体源" in joined


# ── 实体 ──────────────────────────────────────────────────


def test_extended_html_entities():
    assert _inline("&rarr; &alpha; &hellip;") == "\u2192 \u03b1 \u2026"
    assert _inline("&sum; &infin; &ne;") == "\u2211 \u221e \u2260"
    assert _inline("&ensp;&shy;") == "\u2002\u00ad"


# ── 行内 HTML ─────────────────────────────────────────────


def test_inline_font_color():
    runs = [r for r in render_inline('<font color="red">红</font>') if r.text == "红"]
    assert runs and runs[0].style.fg == 196


def test_inline_font_without_color_transparent():
    assert _inline('<font size="3">文字</font>') == "文字"


def test_inline_span_style_color_hex():
    runs = [r for r in render_inline('<span style="color:#00ff00">绿</span>') if r.text == "绿"]
    assert runs and runs[0].style.fg is not None


def test_inline_small_and_big():
    runs = {r.text: r.style for r in render_inline("<small>小</small><big>大</big>")}
    assert runs["小"].dim
    assert runs["大"].bold


def test_inline_quoted():
    assert _inline("<q>引用</q>") == "\u300c引用\u300d"


def test_inline_acronym_with_title():
    assert _inline('<acronym title="World Wide Web">WWW</acronym>') == "WWW (World Wide Web)"


def test_inline_script_hidden():
    assert _inline("前<script>alert(1)</script>后") == "前后"
    assert _inline("前<style>.a{}</style>后") == "前后"


def test_inline_wbr_invisible():
    assert _inline("a<wbr>b") == "ab"


def test_inline_transparent_containers():
    assert _inline("<center>居中</center>") == "居中"
    assert _inline("<label>标签</label><output>输出</output>") == "标签输出"


# ── 流式一致性 ────────────────────────────────────────────


def test_html_stream_chunked_matches_single_write():
    md = ('<div align="center">\n居中\n</div>\n'
          '<script>\nvar a = 1;\n</script>\n'
          '<pre class="language-py">\nx = 1\n</pre>\n'
          '<progress value="30" max="60"></progress>\n'
          '<input type="checkbox" checked> 勾选\n')
    one = AnsiStreamRenderer(width=64)
    one.write(md)
    one.close()
    whole = [ln.plain for ln in one.take_lines()]

    chunked = AnsiStreamRenderer(width=64)
    for i in range(0, len(md), 5):
        chunked.write(md[i:i + 5])
    chunked.close()
    parts = [ln.plain for ln in chunked.take_lines()]
    assert parts == whole


def test_html_preview_does_not_leak_script_source():
    r = AnsiStreamRenderer(width=64)
    r.write("<script>\nvar secret = 1;\n")
    preview = "\n".join(ln.plain for ln in r.take_preview_lines())
    assert "secret" not in preview
    r.write("</script>\n")
    r.close()


def test_html_block_preview_stable():
    r = AnsiStreamRenderer(width=64)
    r.write("<div>\n第一行\n")
    first = [ln.plain for ln in r.take_preview_lines()]
    second = [ln.plain for ln in r.take_preview_lines()]
    assert first == second
    r.close()
