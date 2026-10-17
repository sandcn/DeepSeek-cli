"""TUI 流式 Markdown 渲染缺陷 / 语法扩展回归（第八批）。

覆盖本轮修复：

  - **容器前缀顺序**：列表与引用可相互嵌套，固定「列表 → 引用」顺序会把
    「列表内引用」渲染成 ``│   ``（应为 ``  │ ``）。解析器写入 ``prefix_stack``
    （外 → 内），渲染层按真实嵌套顺序拼接。
  - **定义列表**：术语行内 Markdown（``**Term**`` / `` `code` ``）此前原样
    泄漏；``Term1\\nTerm2\\n: def`` 的多术语形态此前只取最后一行。
  - **参考式链接定义目标**：``[x]: <https://e.com/a b>`` 的尖括号目标被按
    空白截断。
  - **HTML**：``<pre><code>…</code></pre>`` 的 ``</code>`` 行渲染为空行；
    行内 ``<input type=checkbox>`` 整标签消失；行内 ``<img>`` 忽略
    title/width/height。
  - **行内语法扩展**：``{color:#rrggbb}``、``{.smallcaps}``、行首开启的
    ``~~a~~b`` / ``==a==b`` / ``++a++b`` / ``||a||b``、Obsidian 块 ID
    ``^id`` 与嵌入 ``![[…]]``。
  - **预览快照稳定性**：预览内容未变化时 ``take_preview_lines`` 返回同一
    列表对象（TUI 侧免复制、use_memo 命中）。
"""
from __future__ import annotations

import pytest

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════

def _one_shot(src: str, width: int = 80):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _stream(src: str, chunk: int = 3, width: int = 80):
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _plain(src: str) -> str:
    return "".join(run.text for run in render_inline(src))


def _style(src: str):
    runs = [run for run in render_inline(src) if run.text.strip()]
    assert runs
    return runs[0].style


# ══════════════════════════════════════════════════════════
# 容器前缀顺序（列表 / 引用互相嵌套）
# ══════════════════════════════════════════════════════════

def test_list_inside_blockquote_prefix_order():
    assert _one_shot("> - a\n>   ```py\n>   x\n>   ```\n") == [
        "\u2502 \u2022 a",
        "\u2502   ```py [py]",
        "\u2502   x",
        "\u2502   ```",
    ]


def test_blockquote_inside_list_prefix_order():
    assert _one_shot("- a\n  > b\n  > ```py\n  > x\n  > ```\n") == [
        "\u2022 a",
        "  \u2502 b",
        "  \u2502 ```py [py]",
        "  \u2502 x",
        "  \u2502 ```",
    ]


def test_blockquote_inside_list_table_prefix_order():
    lines = _one_shot(
        "- a\n  > | x | y |\n  > |---|---|\n  > | 1 | 2 |\n")
    assert lines[0] == "\u2022 a"
    assert lines[1] == "  \u2502 \u250c\u2500\u2500\u2500\u252c\u2500\u2500\u2500\u2510"
    assert all(ln.startswith("  \u2502 ") for ln in lines[1:])


def test_nested_quote_inside_list_prefix_order():
    assert _one_shot("- a\n  > - b\n  >   - c\n") == [
        "\u2022 a",
        "  \u2502 \u2022 b",
        "  \u2502   \u25e6 c",
    ]


def test_list_block_preview_keeps_quote_prefix_and_reaches_final():
    src = "- a\n  > b\n  > ```py\n  > x\n  > ```\n"
    r = AnsiStreamRenderer(width=60)
    r.write(src)
    committed = [ln.plain for ln in r.take_lines()]
    preview = [ln.plain for ln in r.take_preview_lines()]
    r.close()
    final = committed + [ln.plain for ln in r.take_lines()]
    assert committed == ["\u2022 a"]
    assert preview == final[len(committed):] == [
        "  \u2502 b",
        "  \u2502 ```py [py]",
        "  \u2502 x",
        "  \u2502 ```",
    ]


def test_list_item_with_block_content_keeps_marker():
    assert _one_shot("- ```\n  x\n  ```\n") == [
        "\u2022 ",
        "  ```",
        "  x",
        "  ```",
    ]
    assert _one_shot("- > quote\n") == ["\u2022 ", "  \u2502 quote"]
    assert _one_shot("1. | a |\n   |---|\n   |1|\n")[0] == "1. "


# ══════════════════════════════════════════════════════════
# Pandoc multiline table
# ══════════════════════════════════════════════════════════

_MULTILINE = (
    "--------------------------\n"
    " H1          H2\n"
    "-----------  ---------------\n"
    " A           B\n"
    "\n"
    " C           D\n"
    "--------------------------\n"
)


def test_multiline_table_basic():
    lines = _one_shot(_MULTILINE)
    assert lines[0].startswith("\u250c")
    assert "\u2502 H1 \u2502 H2 \u2502" in lines
    assert any("A" in ln and "B" in ln for ln in lines)
    assert any("C" in ln and "D" in ln for ln in lines)
    assert lines[-1].startswith("\u2514")


def test_multiline_table_stream_matches_once():
    assert _stream(_MULTILINE) == _one_shot(_MULTILINE)


def test_multiline_table_multiline_cell():
    src = (
        "--------------------------\n"
        " H1          H2\n"
        "-----------  ---------------\n"
        " A           first line\n"
        "             second line\n"
        "--------------------------\n"
    )
    lines = _one_shot(src)
    joined = "\n".join(lines)
    assert "first line" in joined and "second line" in joined


def test_multiline_table_fallback_keeps_long_hr_and_paragraph():
    src = (
        "--------------------------\n"
        "just a paragraph\n"
        "--------------------------\n"
    )
    lines = _one_shot(src)
    assert sum(1 for ln in lines if ln.startswith("\u2500")) == 2
    assert "just a paragraph" in lines


def test_multiline_table_in_blockquote_and_list():
    quote = _one_shot(
        "> --------------------------\n"
        ">  H1          H2\n"
        "> -----------  ---------------\n"
        ">  A           B\n"
        "> --------------------------\n"
    )
    assert quote[0].startswith("\u2502 \u250c")
    assert all(ln.startswith("\u2502 ") for ln in quote)

    item = _one_shot(
        "- item\n\n"
        "  --------------------------\n"
        "   H1          H2\n"
        "  -----------  ---------------\n"
        "   A           B\n"
        "  --------------------------\n"
    )
    assert item[0] == "\u2022 item"
    assert any(ln.startswith("  \u250c") for ln in item)


def test_nested_admonition_body_has_no_extra_quote_prefix():
    lines = _one_shot("> > [!NOTE] t\n> > body\n")
    assert lines[0] == "\u25a0 NOTE t"
    assert lines[1].strip() == "body"
    assert "\u2502" not in lines[1]


def test_nested_admonition_body_nested_quote_preserved():
    lines = _one_shot("> [!NOTE] t\n> > body\n")
    assert lines[0] == "\u25a0 NOTE t"
    assert lines[1] == "    \u2502 body"


# ══════════════════════════════════════════════════════════
# 定义列表
# ══════════════════════════════════════════════════════════

def test_definition_term_inline_markdown():
    lines = _one_shot("**Term** and `code`\n: def\n")
    assert lines[0] == "Term and code: def"
    assert "**" not in lines[0] and "`" not in lines[0]


def test_definition_multiple_terms():
    assert _one_shot("T1 *a*\nT2 **b**\n: def\n") == [
        "T1 a",
        "T2 b: def",
    ]


def test_definition_multiple_terms_stream_matches_once():
    src = "T1 *a*\nT2 **b**\n: def\n: second\n"
    assert _stream(src) == _one_shot(src)


# ══════════════════════════════════════════════════════════
# 参考式链接定义目标（尖括号 / URL 内空格）
# ══════════════════════════════════════════════════════════

def test_reference_definition_angle_destination():
    lines = _one_shot("[x]\n\n[x]: <https://e.com/a b>\n")
    assert "https://e.com/a b" in lines[0]
    assert any("[x] https://e.com/a b" in ln for ln in lines)
    assert not any("b>" in ln for ln in lines)


def test_reference_definition_next_line_angle_destination():
    lines = _one_shot("[x]\n\n[x]:\n   <https://e.com/a b> \"T\"\n")
    assert "https://e.com/a b" in lines[0]
    assert any("\"T\"" in ln for ln in lines)


def test_inline_link_target_multiline():
    lines = _one_shot("[a](\nhttps://e.com\n\"T\"\n)\n")
    assert lines == ['a "T"']


# ══════════════════════════════════════════════════════════
# HTML
# ══════════════════════════════════════════════════════════

def test_html_pre_code_close_tag_does_not_add_blank_line():
    lines = _one_shot(
        '<pre class="language-python"><code>x = 1\nif x:\n    print(x)\n'
        '</code></pre>\n')
    assert lines == [
        "```python [python]",
        "x = 1",
        "if x:",
        "    print(x)",
        "```",
    ]


def test_inline_input_renders_checkbox():
    assert _plain('before <input type="checkbox" checked> after') == "before \u2611 after"
    assert _plain('before <input type="checkbox"> after') == "before \u2610 after"
    assert _plain('before <input type="radio"> after') == "before \u25cb after"


def test_inline_img_attributes():
    assert _plain(
        'before <img src="a.png" alt="A" title="T" width="10" height="20"> after'
    ) == 'before \U0001f5bc\ufe0f A (a.png =10x20) "T" after'


def test_inline_media_tags_keep_source():
    assert _plain('a <audio src="a.mp3"></audio> b') == "a \u266a a.mp3 b"
    assert _plain('a <iframe src="x"></iframe> b') == "a \u29c9 x b"
    assert _plain('a <object data="x"></object> b') == "a \u29c9 x b"
    assert _plain('a <source src="x" type="video/webm"> b') == \
        "a \u25c6 [video/webm] x b"
    assert _plain('a <track src="x" kind="captions"> b') == \
        "a \u25c6 [captions] x b"


# ══════════════════════════════════════════════════════════
# 行内语法扩展
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src,expected", [
    ("{color:#ff8800}x{color}", "x"),
    ("{color:#f80}y{color}", "y"),
    ("{color:red}z{color}", "z"),
])
def test_color_hex_supported(src, expected):
    assert _plain(src) == expected


def test_smallcaps_span_style():
    style = _style("[Small Caps]{.smallcaps}")
    assert style is not None and style.bold


@pytest.mark.parametrize("src,expected", [
    ("~~a~~b", "ab"),
    ("==a==b", "ab"),
    ("++a++b", "ab"),
    ("||a||b", "\u2588b"),
])
def test_span_opener_at_line_start(src, expected):
    assert _plain(src) == expected


@pytest.mark.parametrize("src", [
    "a~~b, c~~d",
    "x==y==z",
    "mode==\"change\"",
    "a++ 与 b++",
    "a||b || c||d",
])
def test_word_operator_spans_preserved(src):
    assert _plain(src) == src


def test_block_id_hidden_at_line_end():
    assert _plain("Paragraph text ^block-1").strip() == "Paragraph text"


def test_block_id_does_not_hide_inline_caret():
    assert _plain("E = mc^2") == "E = mc^2"
    assert _plain("y ^not id z") == "y ^not id z"


def test_wikilink_embed():
    assert _plain("![[Note]]") == "Note"
    assert _plain("![[Note|Shown]]") == "Shown"
    image = _plain("![[image.png]]")
    assert "\U0001f5bc" in image and "image.png" in image


# ══════════════════════════════════════════════════════════
# 预览快照稳定性（性能）
# ══════════════════════════════════════════════════════════

def test_preview_snapshot_identity_without_changes():
    r = AnsiStreamRenderer(width=80)
    r.write("paragraph **bold**")
    first = r.take_preview_lines()
    second = r.take_preview_lines()
    assert first is second
    r.close()


def test_preview_snapshot_changes_with_content():
    r = AnsiStreamRenderer(width=80)
    r.write("hello")
    first = r.take_preview_lines()
    r.write("\n\nworld")
    second = r.take_preview_lines()
    assert first is not second
    r.close()


def test_preview_snapshot_cleared_after_close():
    r = AnsiStreamRenderer(width=80)
    r.write("paragraph")
    r.take_preview_lines()
    r.close()
    assert r.take_preview_lines() == []


# ══════════════════════════════════════════════════════════
# 流式 / 一次性一致性
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("src", [
    "- a\n  > b\n  > ```py\n  > x\n  > ```\n",
    "T1 *a*\nT2 **b**\n: def\n",
    "<pre class=\"language-python\"><code>x = 1\n</code></pre>\n",
    "before <input type=\"checkbox\" checked> after\n",
    "~~a~~b ==c==d\n",
    "text ^block-1\n\n![[Note]] ![[image.png]]\n",
    "[a](\nhttps://e.com\n\"T\"\n)\n",
])
def test_stream_matches_once_for_new_syntax(src):
    assert _stream(src) == _one_shot(src)


# ══════════════════════════════════════════════════════════
# Pandoc 前置表注（表格上方的 ``Table: 说明``）
# ══════════════════════════════════════════════════════════

TABLE_CAPTION_PIPE = "Table: Demo table\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"

TABLE_CAPTION_GRID = (
    "Table: Grid demo\n\n"
    "+---+---+\n| a | b |\n+===+===+\n| 1 | 2 |\n+---+---+\n"
)

TABLE_CAPTION_MULTILINE = (
    "Table: Multi demo\n"
    "-------------------------------------------------------------\n"
    " Name     Age\n"
    "-------- -------\n"
    " Alice     30\n"
    "\n"
    " Bob       40\n"
    "-------------------------------------------------------------\n"
)


def test_table_caption_before_pipe_table():
    lines = _one_shot(TABLE_CAPTION_PIPE)
    assert lines[0].startswith("┌")
    assert "Demo table" in lines[-1]
    # 表注与表格之间的空行不应渲染为额外空行
    assert "" not in lines


def test_table_caption_before_grid_table():
    lines = _one_shot(TABLE_CAPTION_GRID)
    assert lines[0].startswith("┌")
    assert "Grid demo" in lines[-1]


def test_table_caption_before_simple_table_keeps_columns():
    lines = _one_shot(
        "Table: Simple demo\n\n  Right     Left\n-------    ------\n     12    12\n")
    assert "Right" in lines[1]
    assert "Left" in lines[1]
    assert "Simple demo" in lines[-1]


def test_table_caption_before_multiline_table():
    lines = _one_shot(TABLE_CAPTION_MULTILINE)
    assert any("Alice" in line for line in lines)
    assert any("Bob" in line for line in lines)
    assert "Multi demo" in lines[-1]


def test_table_caption_without_table_renders_as_paragraph():
    assert _one_shot("Table: just text\n\nsome paragraph\n") == [
        "just text", "some paragraph"]


def test_table_caption_before_heading_keeps_order():
    assert _one_shot("Table: x\n# H\n") == ["x", "H"]


def test_table_caption_before_list_keeps_order():
    assert _one_shot("Table: x\n- item\n") == ["x", "• item"]


def test_table_caption_inside_paragraph_is_plain_text():
    assert _one_shot("hello\nTable: x\n") == ["hello", "Table: x"]


def test_table_caption_after_table_still_works():
    lines = _one_shot("| a | b |\n|---|---|\n| 1 | 2 |\n\n: below\n")
    assert "below" in lines[-1]


def test_table_caption_visible_in_stream_preview():
    r = AnsiStreamRenderer(width=80)
    r.write("Table: Demo table\n")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("Demo table" in line for line in preview)
    r.close()


@pytest.mark.parametrize("src", [
    TABLE_CAPTION_PIPE,
    TABLE_CAPTION_GRID,
    TABLE_CAPTION_MULTILINE,
])
def test_table_caption_stream_matches_once(src):
    assert _stream(src) == _one_shot(src)


# ══════════════════════════════════════════════════════════
# Pandoc example list（``(@)`` / ``(@label)``）
# ══════════════════════════════════════════════════════════

def test_example_list_auto_numbers():
    assert _one_shot("(@) first\n(@) second\n") == ["1. first", "2. second"]


def test_example_list_label_keeps_stable_number():
    assert _one_shot("(@a) one\n(@) two\n(@a) again\n") == [
        "1. one", "2. two", "1. again"]


def test_example_list_nested_in_bullet():
    assert _one_shot("- a\n  (@) x\n") == ["• a", "  1. x"]


def test_example_list_requires_trailing_space():
    assert _one_shot("(@)x\n") == ["(@)x"]


def test_example_list_preview_classification():
    r = AnsiStreamRenderer(width=80)
    r.write("(@) first item")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert any("1. first item" in line for line in preview)
    r.close()


# ══════════════════════════════════════════════════════════
# Pandoc 链接 / 图片属性块
# ══════════════════════════════════════════════════════════

def test_link_attrs_do_not_leak_text():
    assert _one_shot("[text](url){.cls}\n")[0] == "text"
    assert _one_shot("[text](url){#id key=val}\n")[0] == "text"


def test_link_attrs_on_reference_link():
    lines = _one_shot("[ref][1]{.c}\n\n[1]: http://x\n")
    assert lines[0].startswith("ref")


def test_image_single_side_size():
    assert "=100" in _one_shot("![alt](a.png){width=100}\n")[0]
    assert "=x20" in _one_shot("![alt](a.png){height=20}\n")[0]


def test_reference_image_attrs_size():
    lines = _one_shot("![alt][1]{width=50% height=20}\n\n[1]: a.png\n")
    assert "=50x20" in lines[0]


def test_plain_braces_not_treated_as_attrs():
    assert _one_shot("a {not attrs} b\n") == ["a {not attrs} b"]
