"""ANSI 流式 Markdown 语法扩展（第二批）测试。

覆盖 TUI 流式路径（``AnsiStreamRenderer`` / ``ansi.inline``）新增/增强语法：

  - Front Matter 元信息块（YAML ``---`` / TOML ``+++`` / JSON ``{ }``）：
    识别、键值卡片渲染、未闭合回退、文档开头 ``---`` 分隔线不误判、流式预览
  - 缩进代码块（4 空格 / Tab，CommonMark）：识别与代码块渲染、不中断段落
  - 参考式图片 ``![alt][ref]``、快捷引用 ``[ref]`` / ``[ref][]``
  - 更多 HTML 标签：``<figure>/<figcaption>``、``<dl>/<dt>/<dd>``、
    ``<ul>/<li>``、``<table>``（→ 框线表格）、``<ruby>`` 注音、``<a>`` 链接、
    ``<video>`` 等媒体占位
  - 引用块内块级元素带 ``│`` 前缀（列表 / 代码块 / 表格 / 嵌套引用）
  - 内容丢失修复：无语言围栏首行被吞、``<details>`` 内 Markdown、定义列表多定义
  - 表格表注 ``: 说明`` / ``Table: 说明``
"""

from __future__ import annotations

import io

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _render(src: str, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _live(chunks, width: int = 72):
    r = AnsiStreamRenderer(width=width)
    out = []
    for c in chunks:
        r.write(c)
        out.append(([x.plain for x in r.take_lines()],
                    [x.plain for x in r.take_preview_lines()]))
    r.close()
    return out


def _inline_plain(text: str, ctx=None) -> str:
    return "".join(run.text for run in render_inline(text, ctx=ctx))


# ══════════════════════════════════════════════════════════
# Front Matter
# ══════════════════════════════════════════════════════════


def test_front_matter_yaml_keys_rendered():
    lines = _render("---\ntitle: 我的标题\nauthor: 张三\n---\n\n正文\n")
    assert lines[0] == "\u258d 元信息 (YAML)"
    assert "title" in lines[1] and "我的标题" in lines[1]
    assert "author" in lines[2] and "张三" in lines[2]
    assert "正文" in lines


def test_front_matter_toml():
    lines = _render('+++\ntitle = "x"\n+++\n\n正文\n')
    assert lines[0] == "\u258d 元信息 (TOML)"
    assert "title" in lines[1] and "x" in lines[1]


def test_front_matter_json():
    lines = _render('{\n  "title": "x",\n  "count": 3\n}\n\n正文\n')
    assert lines[0] == "\u258d 元信息 (JSON)"
    joined = "\n".join(lines)
    assert "title" in joined and "x" in joined
    assert "count" in joined and "3" in joined


def test_front_matter_list_values():
    lines = _render("---\ntags:\n  - a\n  - b\n---\n")
    joined = "\n".join(lines)
    assert "a" in joined and "b" in joined


def test_front_matter_hr_not_mistaken_when_followed_by_blank():
    # 文档开头 ``---`` + 空行 → 是分隔线，不是 Front Matter
    lines = _render("---\n\n正文\n")
    assert lines[0].startswith("\u2500")
    assert "正文" in lines
    assert not any("元信息" in ln for ln in lines)


def test_front_matter_unclosed_falls_back():
    # 未闭合的 Front Matter → 回退为普通 Markdown（分隔线 + 正文）
    lines = _render("---\ntitle: x\n正文继续\n")
    assert not any("元信息" in ln for ln in lines)
    assert lines[0].startswith("\u2500")
    assert "正文继续" in "\n".join(lines)


def test_front_matter_stream_preview():
    frames = _live(["---\n", "title: t\n", "---\n"])
    # 首行（定界符暂存）预览为分隔线
    assert frames[0][1][0].startswith("\u2500")
    # 第二行起为元信息预览
    assert any("元信息" in x for x in frames[1][1])
    # 闭合后提交为元信息卡片
    assert any("元信息" in x for x in frames[2][0])


def test_front_matter_only_at_document_start():
    # 非文档开头的 ``---`` 不构成 Front Matter
    lines = _render("正文\n\n---\n\ntitle: x\n")
    assert not any("元信息" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# 缩进代码块
# ══════════════════════════════════════════════════════════


def test_indented_code_block_recognized():
    lines = _render("段落\n\n    代码一\n    代码二\n\n结束\n")
    assert "```" in lines
    assert "代码一" in lines and "代码二" in lines
    assert "结束" in lines


def test_indented_code_does_not_break_paragraph():
    # 缩进行紧跟段落（无空行）→ 作为段落续行，不构成代码块
    lines = _render("段落首行\n    续行\n")
    joined = "\n".join(lines)
    assert "续行" in joined
    assert "```" not in lines


def test_tab_indented_code():
    lines = _render("段落\n\n\tTab代码\n")
    assert "Tab代码" in lines
    assert "```" in lines


def test_indented_code_after_blockquote_list():
    # 引用内列表之后再出现缩进代码（_last_token_type 残留不应阻断识别）
    lines = _render("> - a\n> - b\n\n    代码内容\n")
    assert "```" in lines
    assert "代码内容" in lines


def test_front_matter_list_value_multiline_aligned():
    lines = _render("---\ntags:\n  - a\n  - b\n---\n")
    joined = "\n".join(lines)
    assert "a" in joined and "b" in joined
    cont = [ln for ln in lines if "b" in ln][0]
    # 列表续行带缩进对齐（不裸起于行首）
    assert cont.startswith(" ")


# ══════════════════════════════════════════════════════════
# 引用式图片 / 快捷引用链接
# ══════════════════════════════════════════════════════════


def test_reference_image_resolved():
    lines = _render('![图][img]\n\n[img]: http://x/y.png "图片标题"\n')
    joined = "\n".join(lines)
    assert "图" in joined
    assert "http://x/y.png" in joined


def test_reference_image_unresolved_keeps_placeholder():
    out = _inline_plain("![图][img]")
    assert "图" in out
    assert "[ref:img]" in out


def test_shortcut_reference_link_resolved():
    lines = _render("见 [文档] 说明\n\n[文档]: https://a.b \"T\"\n")
    joined = "\n".join(lines)
    assert "https://a.b" in joined


def test_shortcut_reference_link_unresolved_keeps_brackets():
    assert _inline_plain("见 [foo] 文本") == "见 [foo] 文本"


def test_collapsed_reference_link():
    lines = _render("[doc][]\n\n[doc]: https://a.b\n")
    assert "https://a.b" in "\n".join(lines)


def test_plain_bracket_text_not_broken():
    # 普通方括号文本在无定义时原样保留
    assert _inline_plain("数组 [0] 与 [1] 访问") == "数组 [0] 与 [1] 访问"


# ══════════════════════════════════════════════════════════
# 更多 HTML 标签
# ══════════════════════════════════════════════════════════


def test_html_figure_with_figcaption():
    lines = _render("<figure>\n<img src='a.png' alt='图'>\n<figcaption>图注</figcaption>\n</figure>\n")
    joined = "\n".join(lines)
    assert "figure" in joined
    assert "图注" in joined
    assert "<figcaption>" not in joined


def test_html_dl_dt_dd():
    lines = _render("<dl>\n<dt>术语</dt>\n<dd>定义内容</dd>\n</dl>\n")
    joined = "\n".join(lines)
    assert "术语" in joined and "定义内容" in joined
    assert "<dt>" not in joined and "<dd>" not in joined


def test_html_ul_li():
    lines = _render("<ul>\n<li>项1</li>\n<li>项2</li>\n</ul>\n")
    joined = "\n".join(lines)
    assert "\u2022 项1" in joined
    assert "\u2022 项2" in joined


def test_html_table_converted_to_boxed_table():
    lines = _render("<table>\n<tr><th>A</th><th>B</th></tr>\n<tr><td>1</td><td>2</td></tr>\n</table>\n")
    joined = "\n".join(lines)
    assert "\u250c" in joined and "\u2510" in joined
    assert "A" in joined and "B" in joined


def test_html_table_with_caption():
    lines = _render("<table>\n<caption>表注文字</caption>\n<tr><td>1</td></tr>\n</table>\n")
    joined = "\n".join(lines)
    assert "表注文字" in joined


def test_html_ruby():
    assert _inline_plain("<ruby>漢<rt>かん</rt></ruby>") == "漢(かん)"


def test_html_anchor_keeps_text():
    assert _inline_plain('访问 <a href="http://x.y">链接</a> 结束') == "访问 链接 结束"


def test_html_video_placeholder():
    lines = _render('<video src="a.mp4"></video>\n')
    assert any("video" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# 引用块内块级元素前缀
# ══════════════════════════════════════════════════════════


def test_blockquote_list_gets_prefix():
    lines = _render("> 引用：\n> - 一\n> - 二\n")
    assert "\u2502 \u2022 一" in lines
    assert "\u2502 \u2022 二" in lines


def test_blockquote_code_block_gets_prefix():
    lines = _render("> 代码：\n>\n> ```python\n> x = 1\n> ```\n")
    assert any(ln.startswith("\u2502 ```python") for ln in lines)
    assert any(ln.startswith("\u2502 x = 1") for ln in lines)


def test_blockquote_table_gets_prefix():
    lines = _render("> | A | B |\n> |---|---|\n> | 1 | 2 |\n")
    assert any(ln.startswith("\u2502 \u250c") for ln in lines)
    assert any(ln.startswith("\u2502 \u2502 A") for ln in lines)


def test_blockquote_no_separator_pipe_text_is_paragraph():
    # 引用内多行含 pipe 但无分隔行 → 保持段落语义
    lines = _render("> 甲 | 乙\n> 丙 | 丁\n")
    assert not any("\u250c" in ln for ln in lines)


def test_blockquote_table_keeps_prefix_before_closing_blank_line():
    # 表格在「触发引用关闭的空行」上收尾——仍应带引用前缀
    lines = _render("> 引用：\n> | A | B |\n> |---|---|\n> | 1 | 2 |\n\n之后的段落\n")
    assert any(ln.startswith("\u2502 \u250c") for ln in lines)
    assert "之后的段落" in lines
    assert not any(ln.startswith("\u2502 之后的段落") for ln in lines)


def test_nested_blockquote_depth():
    lines = _render("> 外层\n> > 内层\n")
    assert "\u2502 \u2502 内层" in lines


# ══════════════════════════════════════════════════════════
# 内容丢失修复
# ══════════════════════════════════════════════════════════


def test_nolang_fence_content_preserved():
    lines = _render("```\nplain text\nmore\n```\n")
    assert "plain text" in "\n".join(lines)
    assert "more" in lines


def test_nolang_fence_first_line_not_treated_as_lang():
    lines = _render("```\ncode line one\n```\n")
    joined = "\n".join(lines)
    assert "code line one" in joined


def test_details_body_markdown_rendered():
    lines = _render("<details>\n<summary>标题</summary>\n\n- 项1\n- 项2\n</details>\n")
    joined = "\n".join(lines)
    assert "\u25b6 标题" in joined
    assert "\u2022 项1" in joined
    assert "\u2022 项2" in joined


def test_details_body_code_block():
    lines = _render("<details>\n<summary>s</summary>\n```python\nx = 1\n```\n</details>\n")
    joined = "\n".join(lines)
    assert "x = 1" in joined


def test_definition_list_multiple_definitions():
    lines = _render("术语\n: 定义一\n: 定义二\n")
    # 首条定义带术语前缀，后续定义缩进续行（不再裸显示 ``: 定义二``）
    assert any("术语" in ln for ln in lines)
    assert ": 定义二" not in lines
    assert any("定义二" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# 表格表注
# ══════════════════════════════════════════════════════════


def test_table_caption_colon():
    lines = _render("| A | B |\n|---|---|\n| 1 | 2 |\n: 表注说明\n")
    assert any("表注说明" in ln for ln in lines)
    assert not any(ln.startswith(":") for ln in lines)


def test_table_caption_table_prefix():
    lines = _render("| A |\n|---|\n| 1 |\nTable: 我的表格\n")
    assert any("我的表格" in ln for ln in lines)


# ══════════════════════════════════════════════════════════
# 流式一致性
# ══════════════════════════════════════════════════════════


def test_indented_code_stream_preview_matches_commit():
    frames = _live(["段落\n", "\n", "    代码一\n", "    代码二\n"])
    # 未闭合时预览实时显示代码内容
    assert any("代码一" in x for x in frames[2][1])
    assert any("代码一" in x for x in frames[3][1])
    assert any("代码二" in x for x in frames[3][1])


def test_html_table_stream_commit():
    chunks = ["<table>\n", "<tr><td>1</td><td>2</td></tr>\n", "</table>\n"]
    frames = _live(chunks)
    final = frames[-1][0]
    assert any("\u250c" in ln for ln in final)


# ══════════════════════════════════════════════════════════
# Rich 路径（RenderEngine / IncrementalRenderer）一致性
# ══════════════════════════════════════════════════════════


def _rich(src: str) -> str:
    from src.renderer._incremental import IncrementalRenderer

    buf = io.StringIO()
    r = IncrementalRenderer(_file=buf, show_indicator=False)
    r.write(src)
    r.close()
    return buf.getvalue()


def test_rich_front_matter_rendered():
    out = _rich("---\ntitle: 我的标题\n---\n\n正文\n")
    assert "元信息" in out
    assert "我的标题" in out


def test_rich_table_caption_rendered():
    out = _rich("| A |\n|---|\n| 1 |\n: 表注文字\n")
    assert "表注文字" in out


def test_rich_details_body_rendered():
    out = _rich("<details>\n<summary>标题</summary>\n正文内容\n</details>\n")
    assert "标题" in out
    assert "正文内容" in out


def test_rich_nolang_fence_content_preserved():
    out = _rich("```\nplain text\n```\n")
    assert "plain text" in out


def test_rich_reference_image_resolved():
    out = _rich("![图][img]\n\n[img]: http://x/y.png\n")
    assert "http://x/y.png" in out


def test_html_table_track_tag_not_matched_as_row():
    lines = _render("<table>\n<track src='a.vtt'>\n<tr><td>1</td><td>2</td></tr>\n</table>\n")
    joined = "\n".join(lines)
    assert "\u250c" in joined
    assert "1" in joined and "2" in joined


def test_html_table_aligns_columns():
    lines = _render("<table>\n<tr><th>A</th><th>B</th></tr>\n<tr><td>11</td><td>22</td></tr>\n</table>\n")
    joined = "\n".join(lines)
    assert "\u251c" in joined  # 表头分隔线


def test_empty_table_cells_render():
    lines = _render("| A | B | C |\n|---|---|---|\n| 1 |  | 3 |\n")
    joined = "\n".join(lines)
    assert "\u2502" in joined
    assert "1" in joined and "3" in joined


def test_single_column_table_rendered():
    lines = _render("| K |\n|---|\n| v |\n")
    joined = "\n".join(lines)
    assert "\u250c" in joined
    assert "\u2502 --- \u2502" not in joined
    assert "v" in joined


def test_single_column_table_in_blockquote_has_prefix():
    lines = _render("> | A |\n> |---|\n> | 1 |\n")
    assert any(ln.startswith("\u2502 \u250c") for ln in lines)
    assert any(ln.startswith("\u2502 \u2502 A") for ln in lines)


# ══════════════════════════════════════════════════════════
# TUI 内容块集成（model/apply 真实路径）
# ══════════════════════════════════════════════════════════


def test_tui_content_block_renders_new_syntax():
    from src.tui.app.model import AppModel
    from src.tui.app.apply import _do_content
    from src.tui._const import ContentCmd

    m = AppModel()
    m.width = 80
    _do_content(m, ContentCmd(text="---\ntitle: t\n---\n\n"))
    _do_content(m, ContentCmd(text="> - a\n> - b\n\n"))
    _do_content(m, ContentCmd(text="```\nplain text\n```\n\n"))
    _do_content(m, ContentCmd(text="\n"))
    blk = m.blocks[m.content_block_index]
    text = "\n".join(line.plain for line in blk.lines)
    assert "元信息" in text
    assert "\u2502 \u2022 a" in text
    assert "plain text" in text
