"""ANSI 流式 Markdown 语法扩展与渲染错误修复（第十九批）测试。

本轮修复的渲染错误：

  - **``<strike>`` 标签字面泄漏**：旧式 HTML 删除线标签未进入行内标签表，
    ``a <strike>x</strike> b`` 把标签原文与内容一起上屏。
  - **行内出现的块级 HTML 标签泄漏**：``前文 <ul><li>x</li></ul> 后文`` /
    ``<p>`` / ``<table>`` / ``<h1>`` / ``<details>`` 等标签在行内位置原样
    上屏（块级位置由块解析器处理，不受影响）。
  - **定义列表正文为块级结构时结构错乱**：``: - a`` 的后续缩进行被当成
    新的列表、``: ``` `` 泄漏围栏并重复闭合、``: | a | b |`` 被当作表格候选
    且冒号进入表头单元格（渲染出 ``│ : │ a │``）。
  - **fenced div 标题引号**：``::: warning "标题"`` 的引号原样上屏。
  - **列表项内块级容器的标记行预览重复**：``- ``` `` 的 ``•`` 标记行既作为
    committed 行输出、又在预览中重渲染一次（流式期间显示两个 ``•``）。
  - **块级容器收集期间内容延迟发射**：``_maybe_end_list_context`` 在容器
    收集期间提前清空列表上下文，``_handle_list_block_line`` 无法刷出已收集
    内容——表格/代码延迟到文档结束才发射、与后续块顺序颠倒。
  - **活动行预览残留原文**：未闭合的图片/链接目标（``![alt](u``）闭合后，
    增量快路径把 ``)`` 当纯文本追加到上一帧「未解析原文」上，预览持续显示
    原文（``![粗体 图片](a.png`` 形态）直到整行重渲。

本轮新增语法支持：

  - **Pandoc 自动编号有序列表**：``#. a`` / ``#) a``（编号随列表位置递增，
    显式编号项重置后续自动编号）。
  - **Pandoc 定义标记 ``~``**：``Term`` / ``~ def`` 与 ``: def`` 同义。
  - **定义列表块级正文**：定义正文可以是列表 / 代码块 / 表格 / 引用等块级
    结构（按定义内容列缩进渲染，流式预览同样可见）。
  - **行内 HTML 删除线 ``<strike>``**、行内 ``<pre>``（代码样式）、行内
    块级标签透明化（内容保留、标签不泄漏）、``<h1>~<h6>`` 行内按粗体呈现。
  - **背景色**：``<span style="background-color:#ff0">`` /
    ``<font bgcolor="red">``。
  - **图片 alt 行内标记剥离**：``![**粗** 图](u)`` 的 alt 显示为 ``粗 图``。
  - **Emoji 短代码补全**：keycap（``:one:`` / ``:keycap_ten:``）、旗帜
    （``:cn:`` / ``:us:``…）、常用表情与文件图标；``:hash:`` / ``:asterisk:``
    的名字字符集补上 ``#`` / ``*``（表内条目此前无法解析）。
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


def _runs(src: str):
    return render_inline(src)


def _preview(src: str, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    return [ln.plain for ln in r.take_preview_lines()]


# ══════════════════════════════════════════════════════════
# 列表项内块级容器：标记行预览不重复
# ══════════════════════════════════════════════════════════

def test_list_block_marker_not_duplicated_in_preview():
    """列表项内容整体是块级容器时，``•`` 标记行只出现一次。

    修复前标记行既作为 committed 行输出，又在 ``_list_block_preview`` 中
    重渲染一次（流式期间预览里重复出现一个 ``•``）。
    """
    r = AnsiStreamRenderer(width=60)
    r.write("- ```\n  x\n")
    committed = [ln.plain for ln in r.take_lines()]
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert committed == ["\u2022 "]
    assert "\u2022 " not in preview
    assert any("```" in ln for ln in preview)


# ══════════════════════════════════════════════════════════
# Emoji 短代码补全（keycap / 旗帜 / 常用表情）
# ══════════════════════════════════════════════════════════

def test_emoji_keycap_shortcodes():
    assert _ansi(":one: :two: :keycap_ten:\n") == ["1\ufe0f\u20e3 2\ufe0f\u20e3 \U0001f51f"]


def test_emoji_flag_and_heart_shortcodes():
    assert _ansi(":cn: :red_heart:\n") == ["\U0001f1e8\U0001f1f3 \u2764\ufe0f"]


def test_emoji_hash_and_asterisk_shortcodes():
    """``:hash:`` / ``:asterisk:``（表内已有条目）此前因名字字符集不含
    ``#`` / ``*`` 而永远无法解析。"""
    assert _ansi(":hash: :asterisk:\n") == ["#\ufe0f\u20e3 *\ufe0f\u20e3"]


def test_emoji_common_face_shortcodes():
    assert _ansi(":grinning: :partying_face:\n") == ["\U0001f600 \U0001f973"]


# ══════════════════════════════════════════════════════════
# <strike> 行内删除线
# ══════════════════════════════════════════════════════════

def test_strike_tag_not_leaked():
    assert _ansi("a <strike>X</strike> b\n") == ["a X b"]


def test_strike_tag_applies_strikethrough_style():
    runs = _runs("a <strike>X</strike> b")
    hit = [r for r in runs if r.text == "X"]
    assert hit and hit[0].style is not None and hit[0].style.dim


def test_strike_tag_stream_matches_once():
    src = "a <strike>**粗** X</strike> b\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 行内出现的块级 HTML 标签：透明 / 不泄漏
# ══════════════════════════════════════════════════════════

def test_inline_ul_li_transparent():
    assert _ansi("前 <ul><li>一</li><li>二</li></ul> 后\n") == ["前 一二 后"]


def test_inline_p_and_table_transparent():
    assert _ansi("前 <p>段</p> 后\n") == ["前 段 后"]
    assert _ansi("前 <table><tr><td>格</td></tr></table> 后\n") == ["前 格 后"]


def test_inline_heading_tag_bold():
    runs = _runs("前 <h3>标题</h3> 后")
    assert "".join(r.text for r in runs) == "前 标题 后"
    hit = [r for r in runs if r.text == "标题"]
    assert hit and hit[0].style is not None and hit[0].style.bold


def test_inline_details_transparent():
    assert _ansi("前 <details>x</details> 后\n") == ["前 x 后"]


def test_inline_pre_renders_as_code():
    assert _ansi("前 <pre>a = 1</pre> 后\n") == ["前 a = 1 后"]
    runs = _runs("<pre>a = 1</pre>")
    assert runs and runs[0].style is not None and runs[0].style.fg == 46


def test_inline_block_tags_stream_matches_once():
    src = "前 <ul><li>一</li></ul> 与 <p>段</p> 后\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 背景色
# ══════════════════════════════════════════════════════════

def test_span_background_color():
    runs = _runs('<span style="background-color:#ffff00">x</span>')
    assert runs and runs[0].style is not None
    assert runs[0].style.bg is not None


def test_font_bgcolor_attribute():
    runs = _runs('<font bgcolor="red">x</font>')
    assert runs and runs[0].style is not None
    assert runs[0].style.bg == 196


def test_background_color_keeps_nested_formatting():
    runs = _runs('<span style="background-color:red">**粗**</span>')
    hit = [r for r in runs if r.text == "粗"]
    assert hit and hit[0].style is not None
    assert hit[0].style.bg == 196 and hit[0].style.bold


# ══════════════════════════════════════════════════════════
# 图片 alt 行内标记
# ══════════════════════════════════════════════════════════

def test_image_alt_markup_stripped():
    assert _ansi("![**粗** 图](u.png)\n")[0].startswith("\U0001f5bc\ufe0f 粗 图")


def test_image_alt_code_markup_stripped():
    first = _ansi("![a `c` b](u.png)\n")[0]
    assert "a c b" in first
    assert "`" not in first


def test_image_alt_unresolved_bracket_kept():
    """未定义引用的方括号文本保留（CommonMark alt 语义）。"""
    assert "a [b]" in _ansi("![a [b]](u.png)\n")[0]


def test_image_alt_stream_matches_once():
    src = "![**粗** 图](u.png)\n"
    assert _stream(src) == _ansi(src)


def test_tail_open_marker_detects_link_destination():
    """未闭合的链接目标 / 行内属性视为未闭合结构（增量快路径回退整行重渲）。"""
    from src.renderer.ansi import _tail_has_open_marker
    assert _tail_has_open_marker("![alt](u")
    assert _tail_has_open_marker("[x]{.cls")
    assert _tail_has_open_marker("`x`{.py")
    assert not _tail_has_open_marker("![alt](u)")


def test_active_line_preview_completes_image_destination():
    """活动行预览：图片目标闭合后立即显示渲染结果（不留原文）。

    修复前增量快路径把 ``)`` 当作纯文本追加到「未闭合时按普通文本渲染」的
    上一帧结果上，预览一直显示 ``![粗体 图片](a.png`` 形态直到整行重渲。
    """
    r = AnsiStreamRenderer(width=72)
    src = "::: note\n正文\n:::\n\n![**粗体** 图片](a.png)\n"
    for i in range(0, len(src), 3):
        r.write(src[i:i + 3])
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview == ["\U0001f5bc\ufe0f 粗体 图片 (a.png)"], preview


# ══════════════════════════════════════════════════════════
# Pandoc 自动编号有序列表 #.
# ══════════════════════════════════════════════════════════

def test_auto_numbered_list():
    assert _ansi("#. a\n#. b\n#. c\n") == ["1. a", "2. b", "3. c"]


def test_auto_numbered_list_paren_marker():
    assert _ansi("#) a\n#) b\n") == ["1. a", "2. b"]


def test_auto_numbered_list_after_explicit_number():
    assert _ansi("3. a\n#. b\n") == ["3. a", "4. b"]


def test_auto_numbered_list_resets_between_lists():
    lines = _ansi("#. a\n\n段落\n\n#. b\n")
    numbered = [ln for ln in lines
                if ln.endswith(". a") or ln.endswith(". b")]
    assert numbered == ["1. a", "1. b"], lines


def test_auto_numbered_list_nested_restarts():
    """嵌套层级的 ``#.`` 各自从 1 开始，不推进外层编号。"""
    assert _ansi("#. a\n   #. n\n#. b\n") == [
        "1. a", "  1. n", "2. b"]


def test_auto_numbered_list_stream_matches_once():
    src = "#. a\n#. b\n"
    assert _stream(src) == _ansi(src)


def test_auto_numbered_list_preview_number():
    """未换行的 ``#.`` 活动行预览按当前自动编号显示（且不推进计数）。"""
    assert _preview("#. a") == ["1. a"]


def test_auto_numbered_list_preview_does_not_advance_counter():
    r = AnsiStreamRenderer(width=70)
    r.write("#. a\n")
    # 预览多帧（未闭合列表项）不得推进编号
    for _ in range(3):
        r.take_preview_lines()
    r.write("#. b\n")
    r.close()
    assert [ln.plain for ln in r.take_lines()] == ["1. a", "2. b"]


# ══════════════════════════════════════════════════════════
# Pandoc 定义标记 ~
# ══════════════════════════════════════════════════════════

def test_tilde_definition_marker():
    assert _ansi("Term\n~ def\n") == ["Term: def"]


def test_tilde_definition_marker_inline_markup():
    assert _ansi("Term\n~ **bold** def\n") == ["Term: bold def"]


def test_tilde_definition_stream_matches_once():
    src = "Term\n~ def\n"
    assert _stream(src) == _ansi(src)


# ══════════════════════════════════════════════════════════
# 定义列表块级正文
# ══════════════════════════════════════════════════════════

def test_definition_block_list():
    assert _ansi("Term\n: - a\n  - b\n") == [
        "Term:", "      \u2022 a", "      \u2022 b"]


def test_definition_block_fence():
    lines = _ansi("Term\n: ```py\n  x = 1\n  ```\n")
    assert lines[0] == "Term:"
    assert any(ln.strip().startswith("```py") for ln in lines)
    assert any(ln.strip() == "x = 1" for ln in lines)
    # 打开 / 关闭围栏各一次（修复前重复闭合）
    assert sum(1 for ln in lines if ln.strip().startswith("```")) == 2
    assert all(ln.startswith("      ") for ln in lines[1:] if ln)


def test_definition_block_table_not_corrupted():
    lines = _ansi("Term\n: | a | b |\n  |---|---|\n  | 1 | 2 |\n")
    assert lines[0] == "Term:"
    joined = "\n".join(lines)
    assert "a" in joined and "b" in joined
    # 修复前冒号被当作表头单元格（``│ : │ a │``）
    assert "\u2502 : \u2502" not in joined
    assert all(ln.startswith("      ") for ln in lines[1:] if ln)


def test_definition_block_quote():
    assert _ansi("Term\n: > q\n") == ["Term:", "      \u2502 q"]


def test_definition_block_preview_has_no_duplicate_marker():
    r = AnsiStreamRenderer(width=70)
    r.write("Term\n: - a\n  - b")
    committed = [ln.plain for ln in r.take_lines()]
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert committed == ["Term:"]
    # 标记行已提交，预览不得重复渲染（修复前 committed + preview 各一份）
    assert "Term:" not in preview
    assert any("\u2022 a" in ln for ln in preview)


def test_definition_block_stream_matches_once():
    src = "Term\n: | a | b |\n  |---|---|\n  | 1 | 2 |\n\n后续\n"
    assert _stream(src) == _ansi(src)


def test_definition_block_flushes_before_following_block():
    """定义块级正文在其后的新块之前刷出（内容顺序不颠倒）。

    修复前 ``_maybe_end_list_context`` 先清空列表上下文，``_handle_list_block_
    line`` 因内容列丢失而无法刷出，表格/代码延迟到文档结束才发射——出现在
    后续 fenced div 之后。
    """
    lines = _ansi("Term\n: | a | b |\n  |---|---|\n  | 1 | 2 |\n\n"
                  '::: warning "注意"\n正文\n:::\n')
    assert lines[0] == "Term:"
    table_end = max(i for i, ln in enumerate(lines) if "\u2514" in ln)
    div_at = next(i for i, ln in enumerate(lines) if "WARNING" in ln)
    assert table_end < div_at, lines


def test_list_block_flushes_before_following_block():
    lines = _ansi("- ```\n  x\n  ```\n\n::: warning\n正文\n:::\n")
    close_at = max(i for i, ln in enumerate(lines) if ln.strip() == "```")
    div_at = next(i for i, ln in enumerate(lines) if "WARNING" in ln)
    assert close_at < div_at, lines


def test_definition_plain_body_still_inline():
    """普通文本定义正文不受块级正文通道影响。"""
    assert _ansi("Term\n: **bold** def\n") == ["Term: bold def"]


# ══════════════════════════════════════════════════════════
# fenced div 标题引号
# ══════════════════════════════════════════════════════════

def test_fenced_div_title_quotes_stripped():
    assert _ansi('::: warning "标题"\nbody\n:::\n') == [
        "\u25aa WARNING 标题", "  body"]


def test_fenced_div_title_inline_markup():
    assert _ansi('::: note "**重点**"\nbody\n:::\n')[0] == "\u25aa NOTE 重点"


def test_fenced_div_title_stream_matches_once():
    src = '::: warning "标题"\nbody\n:::\n'
    assert _stream(src) == _ansi(src)
