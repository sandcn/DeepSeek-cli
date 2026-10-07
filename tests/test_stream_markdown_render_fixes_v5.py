"""TUI 流式 Markdown 渲染缺陷修复回归（第五批）。

覆盖本轮修复：

  - **行尾快捷引用式链接 ``[ref]``**：``]`` 是文本最后一个字符时
    ``_try_link`` 提前返回，行尾的 ``[ref]`` 不解析（同一语法在行中 / 行首
    正常展开，定义存在也不展开）——修复后按引用定义表展开，未命中保留原文；
  - **折叠式引用图片 ``![alt][]``**：ref_id 为空时未回退到 alt 标签，url 恒为
    ``[ref:]``（即便定义了 ``[alt]: url`` 也渲染 ``🖼️ alt ([ref:])``）；
    修复后与折叠引用式链接 ``[text][]`` 同规则；
  - **快捷引用式图片 ``![alt]``**：原先完全不解析（按普通文本）——修复后与
    快捷引用式链接 ``[ref]`` 对称，未命中定义回退原文 ``![alt]``；
  - **上标 / 下标内容含空白**：``x^2 + y^2`` 修复前把 ``2 + y`` 当作上标
    （渲染为 ``x² ⁺ ʸ2``）、``a~b c~d`` 把 ``b c`` 当作下标；修复后内容
    不含空白才识别，否则原样输出；
  - **单行显示数学 ``\\[ ... \\]``**：修复前只有独占一行的 ``\\[`` 才开块，
    单行形态落入段落（定界符被转义为字面 ``[ ... ]``）——修复后与单行
    ``$$ ... $$`` 一致渲染为数学框；
  - **缩进代码块尾随空行**：块内空行立即发射，``    代码\\n\\n正文`` 的代码块
    渲染出多余空行（CommonMark 尾随空行不计入）——修复后空行暂存、仅在
    后续仍有缩进内容时补发。

两条渲染路径（ANSI / Rich）共用解析层，图片渲染分别覆盖。
"""
from __future__ import annotations

from io import StringIO

from src.renderer import IncrementalRenderer
from src.renderer.ansi import AnsiStreamRenderer
from src.tui.app.apply import _flush_renderer_to_block
from src.tui.app.model import AppModel

from src.tui._const import ContentCmd
from src.tui.app.apply import apply_cmd


# ══════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════


def _render(src: str, width: int = 60):
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _per_char(src: str, width: int = 60):
    r = AnsiStreamRenderer(width=width)
    for i in range(len(src)):
        r.write(src[i:i + 1])
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _rich(src: str) -> str:
    buf = StringIO()
    r = IncrementalRenderer(show_indicator=False, _file=buf)
    r.write(src)
    r.close()
    return buf.getvalue()


def _tui_stream(md: str, width: int = 60, chunk: int = 1):
    model = AppModel()
    model.width = width
    renderer = model.ensure_content()
    for i in range(0, len(md), chunk):
        part = md[i:i + chunk]
        renderer.write(part)
        _flush_renderer_to_block(model, "content", renderer, source_delta=part)
    model.close_content()
    return [ln.plain for ln in model.committed_lines]


def _tui_apply(md: str, width: int = 60, chunk: int = 1):
    model = AppModel()
    model.width = width
    for i in range(0, len(md), chunk):
        apply_cmd(model, ContentCmd(text=md[i:i + chunk]))
    model.finish_stream_render()
    return [ln.plain for ln in model.blocks[model.content_block_index].lines]


# ══════════════════════════════════════════════════════════
# 行尾快捷引用式链接
# ══════════════════════════════════════════════════════════

TRAILING_REF = "原文 [docs]\n\n[docs]: https://a.b \"T\"\n\n"


def test_trailing_shortcut_reference_link_resolved():
    """``]`` 位于文本末尾的快捷引用式链接同样展开。"""
    lines = _render(TRAILING_REF)
    assert lines[0] == "原文 docs ① (https://a.b) \"T\"", lines


def test_trailing_shortcut_matches_mid_text():
    """行尾与行中位置的快捷引用式链接渲染一致（同一语法两种结果修复）。"""
    mid = _render("原文 [docs] 说明\n\n[docs]: https://a.b \"T\"\n\n")
    tail = _render(TRAILING_REF)
    assert mid[0] == "原文 docs ① (https://a.b) \"T\" 说明"
    assert tail[0].endswith("docs ① (https://a.b) \"T\"")


def test_trailing_shortcut_unresolved_keeps_text():
    """无对应定义的 ``[nope]`` 在行尾仍保留原文（不误改方括号文本）。"""
    assert _render("原文 [nope]\n\n") == ["原文 [nope]"]


def test_trailing_shortcut_stream_matches_oneshot():
    """定义在引用之前时逐字符流式渲染与一次性一致。"""
    src = "[docs]: https://a.b \"T\"\n\n原文 [docs]\n\n"
    assert _per_char(src) == _render(src)
    assert any("https://a.b" in ln for ln in _per_char(src))


def test_trailing_shortcut_tui_stream_matches():
    """TUI 流式路径（含前向引用关闭重渲染）结果一致。"""
    per_char = _tui_apply(TRAILING_REF, chunk=1)
    oneshot = _tui_apply(TRAILING_REF, chunk=len(TRAILING_REF))
    assert per_char == oneshot
    assert any("https://a.b" in ln for ln in per_char)


# ══════════════════════════════════════════════════════════
# 折叠式 / 快捷引用式图片
# ══════════════════════════════════════════════════════════

COLLAPSED_IMG = "![i][]\n\n[i]: https://a.b/i.png\n\n"
SHORTCUT_IMG = "![i]\n\n[i]: https://a.b/i.png\n\n"


def test_collapsed_reference_image_resolved():
    """``![alt][]`` 折叠式引用图片按 alt 查定义表展开（不再 ``[ref:]``）。"""
    lines = _render(COLLAPSED_IMG)
    assert lines[0] == "🖼️ i (https://a.b/i.png)", lines
    assert "[ref:]" not in lines[0]


def test_shortcut_reference_image_resolved():
    """``![alt]`` 快捷引用式图片按 alt 查定义表展开。"""
    lines = _render(SHORTCUT_IMG)
    assert lines[0] == "🖼️ i (https://a.b/i.png)", lines


def test_collapsed_image_unresolved_keeps_raw():
    """折叠式引用图片无定义 → 回退原文 ``![alt][]``。"""
    assert _render("![i][] 结束\n\n") == ["![i][] 结束"]


def test_shortcut_image_unresolved_keeps_raw():
    """快捷引用式图片无定义 → 回退原文 ``![alt]``（不误改普通文本）。"""
    assert _render("![i] 结束\n\n") == ["![i] 结束"]


def test_full_reference_image_unresolved_keeps_placeholder():
    """完整引用式图片未命中仍保留 ``[ref:id]`` 占位（既有语义不变）。"""
    assert _render("![i][x] 结束\n\n") == ["🖼️ i ([ref:x]) 结束"]


def test_image_in_heading_and_list_keeps_text():
    """图片语法判断不破坏标题 / 列表中的普通方括号文本显示。"""
    assert _render("# 图 ![icon] 标题\n\n") == ["图 ![icon] 标题"]
    assert _render("- 列表 ![icon] 项\n\n") == ["• 列表 ![icon] 项"]


def test_collapsed_image_stream_matches_oneshot():
    """定义在图片之前时逐字符流式渲染与一次性一致。"""
    src = "[i]: https://a.b/i.png\n\n![i][]\n\n"
    assert _per_char(src) == _render(src)


def test_collapsed_image_tui_forward_ref_consistent():
    """TUI 路径：定义在引用之后（前向引用）时由关闭重渲染保证一致。"""
    assert _tui_apply(COLLAPSED_IMG, chunk=1) == _tui_apply(
        COLLAPSED_IMG, chunk=len(COLLAPSED_IMG))


def test_rich_path_collapsed_and_shortcut_image_resolved():
    """Rich 路径（IncrementalRenderer）同步修复。"""
    assert "https://a.b/i.png" in _rich(COLLAPSED_IMG)
    assert "https://a.b/i.png" in _rich(SHORTCUT_IMG)
    # 未命中回退原文
    assert "![i] 结束" in _rich("![i] 结束\n\n")


# ══════════════════════════════════════════════════════════
# 上标 / 下标内容不含空白
# ══════════════════════════════════════════════════════════


def test_superscript_span_with_space_not_consumed():
    """``x^2 + y^2`` 原样输出（不再把 ``2 + y`` 当上标）。"""
    assert _render("v = x^2 + y^2 结束\n\n") == ["v = x^2 + y^2 结束"]


def test_superscript_paired_still_works():
    """合法成对上标（内容无空白）仍正常渲染。"""
    lines = _render("x^2^ 与结束\n\n")
    assert lines[0] == "x² 与结束", lines


def test_superscript_math_text_intact_stream():
    """含空白的 ``^`` 文本在流式渲染下同样原样（与一次性一致）。"""
    src = "E = mc^2 与 x^2 + y^2\n\n"
    assert _per_char(src) == _render(src) == ["E = mc^2 与 x^2 + y^2"]


def test_subscript_span_with_space_not_consumed():
    """``a~b c~d`` 原样输出（不再把 ``b c`` 当下标）。"""
    assert _render("a~b c~d 结束\n\n") == ["a~b c~d 结束"]


def test_subscript_paired_still_works():
    """合法成对下标（内容无空白）仍正常渲染。"""
    assert _render("H~2~O 说明\n\n") == ["H₂O 说明"]
    assert _render("CO~2~ 排放\n\n") == ["CO₂ 排放"]


def test_strikethrough_unaffected():
    """删除线 ``~~...~~`` 不受下标空白规则影响。"""
    assert _render("~~删除线~~ 结束\n\n") == ["删除线 结束"]


# ══════════════════════════════════════════════════════════
# 单行显示数学 ``\[ ... \]``
# ══════════════════════════════════════════════════════════


def test_single_line_display_math_renders_box():
    """单行 ``\\[ ... \\]`` 渲染为数学框（不再字面 ``[ ... ]``）。"""
    lines = _render("\\[ x^2 + y^2 \\]\n\n")
    assert lines[0].startswith("╭─ 数学公式"), lines
    assert any("x² + y²" in ln for ln in lines)
    assert all(not ln.startswith("[ ") for ln in lines)


def test_single_line_display_math_stream_matches_oneshot():
    src = "\\[ x^2 + y^2 \\]\n\n"
    assert _per_char(src) == _render(src)


def test_multiline_display_math_unchanged():
    """多行 ``\\[`` 块行为不变。"""
    lines = _render("\\[\nx^2\n\\]\n\n")
    assert lines[0].startswith("╭─ 数学公式"), lines
    assert any("x²" in ln for ln in lines)


def test_single_line_display_math_preceded_by_paragraph():
    lines = _render("前文\n\n\\[ a \\]\n\n")
    assert lines[0] == "前文"
    assert any(ln.startswith("╭─ 数学公式") for ln in lines)


# ══════════════════════════════════════════════════════════
# 缩进代码块尾随空行
# ══════════════════════════════════════════════════════════


def test_indented_code_trailing_blank_dropped():
    """代码块结束后的空行不计入代码块（不再多出空行）。"""
    lines = _render("    代码一\n    代码二\n\n结束\n")
    assert lines == ["```", "代码一", "代码二", "```", "结束"], lines


def test_indented_code_inner_blank_kept():
    """块内空行（后面仍有缩进内容）保留。"""
    lines = _render("    代码一\n\n    代码二\n\n结束\n")
    assert lines == ["```", "代码一", "", "代码二", "```", "结束"], lines


def test_indented_code_stream_matches_oneshot():
    src = "    代码一\n    代码二\n\n结束\n"
    assert _per_char(src) == _render(src)


def test_indented_code_trailing_blank_tui_matches():
    src = "段落\n\n    代码一\n\n结束\n"
    assert _tui_apply(src, chunk=1) == _tui_apply(src, chunk=len(src))


def test_rich_path_single_line_display_math_and_indent_code():
    """Rich 路径同源解析层：单行显示数学 / 缩进代码块尾随空行同步修复。"""
    from src.renderer.ansi.helpers import strip_ansi

    out = strip_ansi(_rich("\\[ x^2 \\]\n\n"))
    assert "数学" in out or "│" in out
    # 缩进代码块「代码一」之后不留空行（尾随空行不计入）
    code_out = strip_ansi(_rich("    代码一\n\n结束\n"))
    assert "代码一\n" + chr(0x1F4C4) in code_out


# ══════════════════════════════════════════════════════════
# 下划线粗体：仅非 ASCII（CJK）场景渲染，ASCII 标识符保持 dunder 保护
# ══════════════════════════════════════════════════════════


def test_cjk_bold_at_line_start_rendered():
    """行首 ``__粗体__`` 渲染为粗体（不再被误判为 dunder 标识符）。"""
    assert _render("__粗体__ 结束\n\n") == ["粗体 结束"]
    assert _render("中文 __混排bold__ 结束\n\n") == ["中文 混排bold 结束"]


def test_cjk_bold_italic_triple_underscore_rendered():
    """行首 ``___粗斜体___`` 渲染为粗斜体（文本正确）。"""
    assert _render("___粗斜体___ 结束\n\n") == ["粗斜体 结束"]


def test_ascii_dunder_still_protected():
    """纯 ASCII 标识符保持 dunder 保护（不渲染为粗体、不丢字符）。"""
    assert _render("__init__ 说明\n\n") == ["__init__ 说明"]
    assert _render("调用 __init__ 方法\n\n") == ["调用 __init__ 方法"]
    assert _render("___init___ 说明\n\n") == ["___init___ 说明"]


def test_ascii_bold_keeps_dunder_protection():
    """``__bold__``（纯 ASCII）按既有 dunder 保护不渲染为粗体（用户选定折中）。"""
    assert _render("__bold__ text\n\n") == ["__bold__ text"]
    assert _render("a __b__ c\n\n") == ["a __b__ c"]


def test_underscore_identifier_no_char_loss():
    """``__my_var__`` 原样输出（修复前渲染为 ``_myvar__``，丢失下划线）。"""
    assert _render("名称 __my_var__ 结束\n\n") == ["名称 __my_var__ 结束"]


def test_dunder_identifier_in_sentence_no_char_loss():
    """句中 dunder 标识符与中文粗体混排时不丢字符、不跨段配对。"""
    src = "段落 **加粗** 与 __中文粗体__ 与 __init__ 与 __my_var__ 结束\n\n"
    assert _render(src) == ["段落 加粗 与 中文粗体 与 __init__ 与 __my_var__ 结束"]


def test_multiple_dunder_identifiers_no_cross_pairing():
    """连续 dunder 标识符之间不跨越配对（修复前两端各丢两个下划线）。"""
    assert _render("__init__ 与 __main__ 说明\n\n") == ["__init__ 与 __main__ 说明"]
    assert _render("foo__bar__baz 结束\n\n") == ["foo__bar__baz 结束"]


def test_plain_italic_underscore_unaffected():
    """普通 ``_斜体_`` 不受下划线标识符规则影响。"""
    assert _render("_斜体_ 结束\n\n") == ["斜体 结束"]
    assert _render("a _b_ c\n\n") == ["a b c"]


def test_cjk_bold_stream_matches_oneshot():
    """下划线粗体的流式渲染与一次性一致。"""
    src = "__粗体__ 与 __my_var__ 与 __init__\n\n"
    assert _per_char(src) == _render(src)
    assert _tui_apply(src, chunk=1) == _tui_apply(src, chunk=len(src))


def test_rich_path_cjk_bold_and_dunder_protection():
    """Rich 路径同源解析层：中文粗体渲染、ASCII dunder 保护一致。"""
    from src.renderer.ansi.helpers import strip_ansi

    assert "粗体" in strip_ansi(_rich("__粗体__ 结束\n\n"))
    assert "__init__" in strip_ansi(_rich("__init__ 说明\n\n"))

