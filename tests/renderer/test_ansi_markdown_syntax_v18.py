"""ANSI 流式 Markdown 语法扩展与渲染错误修复（第十八批）测试。

本轮修复的渲染错误：

  - **Mermaid 注释行**：``%%`` 注释 / ``%%{init: …}%%`` 指令作为首行时图类型
    判定失败，整图降级为源码展示（且 fallback 头部与正文重复首行）。
  - **引用块显式空行**：``> a`` / ``>`` / ``> b`` 的空引用行丢失 ``│`` 边框
    （引用框在段落之间断裂）。
  - **列表项内代码块流式预览重复**：``committed`` 中的行级 Token 与
    ``peek_pending`` 的未闭合 ``CODE_BLOCK`` 同时渲染，整块代码预览两遍；
    活动行 tail 的列表缩进与统一前缀叠加（3 + 3 空格）。
  - **HTML void 元素关闭标签**：``</br>`` / ``</img>`` / ``</input>`` 等本应
    被忽略，修复前原样字面上屏。
  - **JSON Front Matter 误判**：``{`` 开头的普通多行文本（非法 JSON）被
    当作元信息卡片（含「元信息 (JSON)」头 + 缩进原文）；现回退为普通段落。

本轮新增语法支持：

  - **Pandoc 行内代码属性**：`` `x`{.python} `` / `` `x`{#id} `` /
    `` `x`{key=val}``（类名映射样式，不再泄漏属性原文）以及 raw inline
    格式属性 `` `x`{=latex} ``（内容照常显示）。
  - **Pandoc 独立属性块行**：``{.cls #id key=val}`` 作用于前一块、显示上
    不可见；无前块 / 空行分隔时仍按普通段落显示（不吞内容）。
  - **Mermaid 注释行**（``%%``）参与图类型判定前被跳过。
  - **HTML 标签扩展**：``<datalist>`` / ``<hgroup>`` / ``<map>`` / ``<slot>``
    块级识别；``<rt>`` / ``<rp>`` / ``<hgroup>`` / ``<map>`` / ``<datalist>``
    / ``<slot>`` 行内透明（内容照常显示、标签字面不泄漏）。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.inline import render_inline


def _ansi(src: str, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _stream(src: str, chunk: int = 1, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _preview_frames(src: str, width: int = 70) -> list[list[str]]:
    """逐字符写入 → 每帧的预览行快照（消费式读取）。"""
    r = AnsiStreamRenderer(width=width)
    frames: list[list[str]] = []
    for ch in src:
        r.write(ch)
        r.take_lines()
        frames.append([ln.plain for ln in r.take_preview_lines()])
    r.close()
    r.take_lines()
    return frames


# ══════════════════════════════════════════════════════════
# Mermaid：注释行 + fallback 首行重复
# ══════════════════════════════════════════════════════════

def test_mermaid_leading_comment_renders_diagram():
    """``%%`` 注释作为首行时仍按图类型渲染（不降级为源码展示）。"""
    lines = _ansi("```mermaid\n%% 说明\ngraph TD\nA-->B\n```\n")
    assert not any("\U0001f4ca" in ln for ln in lines), lines
    joined = "\n".join(lines)
    assert "A" in joined and "B" in joined and "\u25bc" in joined


def test_mermaid_directive_comment_skipped():
    """``%%{init: …}%%`` 指令行同样不参与图类型判定。"""
    lines = _ansi(
        "```mermaid\n%%{init: {'theme': 'dark'}}%%\ngraph LR\nX-->Y\n```\n")
    joined = "\n".join(lines)
    assert "%%" not in joined
    assert "X" in joined and "Y" in joined


def test_mermaid_comment_only_is_empty_chart():
    assert _ansi("```mermaid\n%% only comment\n```\n") == ["\U0001f4ca (空图表)"]


def test_mermaid_fallback_first_line_not_duplicated():
    """未知图类型：首行只显示一次（头部），正文从第二行开始。"""
    lines = _ansi("```mermaid\nfoo bar\nbaz\n```\n")
    assert lines[0] == "  \U0001f4ca foo bar"
    assert lines[1] == "  \u2502 baz"
    assert sum(1 for ln in lines if "foo bar" in ln) == 1


def test_mermaid_comment_stream_matches_once():
    src = "```mermaid\n%% c\ngraph TD\nA-->B\n```\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 引用块显式空行
# ══════════════════════════════════════════════════════════

def test_blockquote_blank_line_keeps_prefix():
    assert _ansi("> a\n>\n> b\n") == ["\u2502 a", "\u2502", "\u2502 b"]


def test_blockquote_blank_line_with_space_keeps_prefix():
    assert _ansi("> a\n> \n> b\n") == ["\u2502 a", "\u2502", "\u2502 b"]


def test_nested_blockquote_blank_line_keeps_prefix():
    assert _ansi("> a\n> >\n> b\n") == ["\u2502 a", "\u2502 \u2502", "\u2502 b"]


def test_blockquote_blank_line_stream_matches_once():
    src = "> a\n>\n> b\n"
    assert _stream(src) == _ansi(src)


def test_blockquote_blank_line_does_not_emit_extra_empty_border():
    """显式空行只输出一行边框，不在引用结束后多出空边框行。"""
    lines = _ansi("> a\n>\n> b\n\n尾段\n")
    assert lines == ["\u2502 a", "\u2502", "\u2502 b", "尾段"]


# ══════════════════════════════════════════════════════════
# 列表项内代码块流式预览
# ══════════════════════════════════════════════════════════

def test_list_block_code_preview_not_duplicated():
    """列表项内未闭合代码块：整块只预览一遍（不再 committed + pending 双份）。"""
    src = "1. a\n   ```js\n   x = 1\n   ```\n"
    frames = _preview_frames(src)
    assert frames, "应产生中间预览帧"
    for frame in frames:
        opens = [ln for ln in frame if "```js" in ln]
        assert len(opens) <= 1, frame
        assert not (frame.count("   x = 1") > 1), frame


def test_list_block_code_preview_active_line_indent():
    """列表项内代码块活动行与已换行行缩进一致（不叠加列表前缀）。"""
    r = AnsiStreamRenderer(width=70)
    r.write("1. a\n   ```js\n   x = 1")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview == [
        "   ```js [js]",
        "   x = 1",
    ], preview


def test_list_block_code_stream_final_matches_once():
    src = "1. a\n   ```js\n   x = 1\n   ```\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# HTML void 元素关闭标签
# ══════════════════════════════════════════════════════════

def test_html_void_close_tags_are_ignored():
    assert _ansi("a</br>b\n") == ["ab"]
    assert _ansi("a</img>b\n") == ["ab"]
    assert _ansi("a</input>b\n") == ["ab"]
    assert _ansi("a</wbr>b\n") == ["ab"]
    assert _ansi("a</hr>b\n") == ["ab"]


def test_html_void_close_tag_keeps_other_markup():
    lines = _ansi("**粗**</br>`code`\n")
    assert lines == ["\u7c97code"]


def test_html_void_close_stream_matches_once():
    src = "a</br>**b**</img>c\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# Pandoc 行内代码属性
# ══════════════════════════════════════════════════════════

def test_inline_code_attrs_not_leaked():
    assert _ansi("`x`{.python}\n") == ["x"]
    assert _ansi("`x`{#id}\n") == ["x"]
    assert _ansi("`x`{key=val}\n") == ["x"]


def test_inline_code_attrs_class_style():
    runs = render_inline("`x`{.red}")
    code = [r for r in runs if r.text == "x"]
    assert code and code[0].style is not None
    assert code[0].style.fg == 196


def test_inline_code_raw_format_attribute():
    """Pandoc raw inline 格式属性 `` `x`{=latex} ``：内容照常显示、属性不泄漏。"""
    assert _ansi("`\\alpha`{=latex} 与文本\n") == ["\\alpha 与文本"]


def test_inline_code_bracket_color_syntax_unchanged():
    """``{color:red}`` 是既有花括号着色语法，不是 Pandoc 属性（不被吞）。"""
    assert _ansi("`x`{color:red}\n") == ["x{color:red}"]


def test_inline_code_attrs_stream_matches_once():
    src = "a `x`{.red} b `y`{.blue} c\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# Pandoc 独立属性块行
# ══════════════════════════════════════════════════════════

def test_attribute_block_after_paragraph_hidden():
    assert _ansi("段落\n{.red}\n") == ["段落"]


def test_attribute_block_after_heading_hidden():
    assert _ansi("# 标题\n{.cls #id}\n") == ["标题"]


def test_attribute_block_after_blank_line_stays_text():
    """空行分隔时属性块不属于前一块（Pandoc 语义），按普通段落显示。"""
    assert _ansi("段落\n\n{.red}\n") == ["段落", "{.red}"]


def test_attribute_block_without_previous_block_stays_text():
    assert _ansi("{.red}\n") == ["{.red}"]


def test_attribute_block_color_syntax_not_swallowed():
    """``{color:red}`` 是既有花括号着色语法，不是属性块（内容不丢）。"""
    assert _ansi("段落\n{color:red}\n") == ["段落", "{color:red}"]


def test_attribute_block_after_code_block_hidden():
    src = "```py\nx = 1\n```\n{.cls}\n"
    assert _ansi(src) == ["```py [py]", "x = 1", "```"]


def test_attribute_block_stream_matches_once():
    src = "段落\n{.red}\n\n后续\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# JSON Front Matter 误判
# ══════════════════════════════════════════════════════════

def test_invalid_json_front_matter_falls_back_to_text():
    """``{`` 开头的普通多行文本（非法 JSON）不再误判为元信息卡片。"""
    assert _ansi("{\nhello\n}\n") == ["{", "hello", "}"]


def test_valid_json_front_matter_still_rendered():
    lines = _ansi('{\n  "a": 1\n}\n')
    assert lines[0] == "\u258d 元信息 (JSON)"
    assert "a" in lines[1] and "1" in lines[1]


def test_invalid_json_front_matter_stream_matches_once():
    src = "{\nbad json\n}\n\n后续\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# HTML 标签扩展
# ══════════════════════════════════════════════════════════

def test_html_datalist_block_not_leaked():
    """``<datalist>`` 是隐藏控件：块级识别后不泄漏标签字面。"""
    lines = _ansi("<datalist>\n<option>a</option>\n</datalist>\n")
    assert lines == []


def test_html_hgroup_block_transparent():
    lines = _ansi("<hgroup>\n<h1>T</h1>\n<p>s</p>\n</hgroup>\n")
    assert lines[:2] == ["T", "  s"]
    assert not any("hgroup" in ln for ln in lines)


def test_html_map_block_not_leaked():
    lines = _ansi("<map name=m>\n<area shape=rect>\n</map>\n")
    assert not any("map" in ln or "area" in ln for ln in lines)


def test_html_inline_rt_rp_transparent():
    assert _ansi("<rt>x</rt> <rp>(</rp>\n") == ["x ("]


def test_html_slot_block_not_leaked():
    lines = _ansi("<slot name=s>\n内容\n</slot>\n")
    assert "  内容" in lines
    assert not any("slot" in ln for ln in lines)
