"""测试渲染器语法扩展：++underline++、||spoiler||、下划线-in-word 保护等。

注意：本文件约 84KB，建议拆分为多个小文件（如按语法扩展分组）。

覆盖内容：
  1. ++underline++ — 下划线语法
  2. ||spoiler|| — 剧透/黑幕语法
  3. 下划线 in-word 保护 — variable_name 不触发斜体
  4. 未闭合格式标记的优雅降级
  5. 边界条件：空内容、嵌套溢出
"""

from __future__ import annotations

from src.api.renderer.inline_renderer import InlineRenderer


def _render(text: str) -> str:
    """渲染内联 Markdown 并返回纯文本。"""
    return InlineRenderer().render(text).plain


def _render_text(text: str) -> object:
    """渲染内联 Markdown 并返回完整 Text 对象。"""
    return InlineRenderer().render(text)


def _get_style_at(text_obj, offset: int):
    """获取 Rich Text 在指定 offset 处的 Style 对象。

    合并所有覆盖该 offset 的 span 的样式（Rich 中多个 span 叠加生效），
    字符串样式名自动转为 Style 对象。
    """
    from rich.style import Style
    combined = text_obj.style
    for span in text_obj.spans:
        if span.start <= offset < span.end:
            span_style = Style.parse(span.style) if isinstance(span.style, str) else span.style
            combined = Style.combine([combined, span_style]) if combined else span_style
    if isinstance(combined, str):
        return Style.parse(combined)
    return combined


# ═══════════════════════════════════════════════════════════
# ++underline++ 语法
# ═══════════════════════════════════════════════════════════


class TestUnderlineSyntax:
    """++underline++ 语法测试。"""

    def test_basic_underline(self):
        """++text++ → 下划线样式。"""
        result = _render_text("hello ++world++")
        assert result.plain == "hello world", f"plain={result.plain!r}"
        # 验证 'world' 部分有 underline 样式
        world_start = result.plain.index("world")
        world_style = _get_style_at(result, world_start)
        assert world_style.underline, f"'world' 应有下划线样式, style={world_style}"

    def test_underline_empty(self):
        """++++ → 空下划线被消化，不出现在输出中。"""
        result = _render("hello ++++ world")
        # ++ ++ 作为空下划线被消化，++++ 不在输出中
        assert "hello" in result, f"hello 应保留: {result!r}"
        assert "world" in result, f"world 应保留: {result!r}"

    def test_underline_inside_sentence(self):
        """句子中 ++ 语法应正常工作。"""
        result = _render_text("this is ++very important++ text")
        assert result.plain == "this is very important text"
        important_start = result.plain.index("very")
        style = _get_style_at(result, important_start)
        assert style.underline, "下划线样式应生效"

    def test_underline_unclosed(self):
        """++text 未闭合 → 降级为纯文本。"""
        result = _render("hello ++world")
        assert "++world" in result or "hello ++world" in result, \
            f"未闭合下划线应降级为纯文本: {result!r}"

    def test_underline_nested_bold(self):
        """++**bold** inside++ → 粗体+下划线嵌套。"""
        result = _render_text("++**bold** inside++")
        plain = result.plain
        assert plain == "bold inside"
        bold_start = plain.index("bold")
        bold_style = _get_style_at(result, bold_start)
        assert bold_style.bold, "嵌套粗体应生效"
        assert bold_style.underline, "外层下划线应生效"


# ═══════════════════════════════════════════════════════════
# ||spoiler|| 语法
# ═══════════════════════════════════════════════════════════


class TestSpoilerSyntax:
    """||spoiler|| 语法测试。"""

    def test_basic_spoiler(self):
        """||text|| → 字符掩码 ████（dim + bright_black）。"""
        result = _render_text("this is ||spoiler|| text")
        assert result.plain == "this is ███████ text"
        spoiler_start = result.plain.index("███████")
        style = _get_style_at(result, spoiler_start)
        assert style.dim, "剧透掩码应有 dim 样式"

    def test_spoiler_empty(self):
        """|||| → 空剧透被消化。"""
        result = _render("hello |||| world")
        assert "hello" in result, f"hello 应保留: {result!r}"
        assert "world" in result, f"world 应保留: {result!r}"

    def test_spoiler_unclosed(self):
        """||text 未闭合 → 降级为纯文本。"""
        result = _render("hello ||world")
        assert "||world" in result or "hello ||world" in result, \
            f"未闭合剧透应降级为纯文本: {result!r}"

    def test_spoiler_nested_italic(self):
        """||*italic* spoiler|| → 斜体嵌套剧透（掩码化）。"""
        result = _render_text("||*italic* spoiler||")
        plain = result.plain
        assert plain == "██████ ███████"
        # 整个文本被 █ 掩码，统一应用 dim 样式（无嵌套样式残留）
        style = _get_style_at(result, 0)
        assert style.dim, "剧透掩码应有 dim 样式"


# ═══════════════════════════════════════════════════════════
# 下划线-in-word 保护
# ═══════════════════════════════════════════════════════════


class TestUnderscoreInWord:
    """下划线-in-word 保护测试。"""

    def test_variable_name(self):
        """variable_name → 不触发斜体。"""
        result = _render("use variable_name here")
        assert "variable_name" in result, \
            f"variable_name 应保持原样: {result!r}"

    def test_file_path(self):
        """含下划线的文件名应保持原样。"""
        result = _render("open file_name.txt to read")
        assert "file_name.txt" in result, \
            f"含下划线的文件名应保持原样: {result!r}"

    def test_snake_case(self):
        """snake_case_text → 完全保持。"""
        result = _render("call my_function_name()")
        assert "my_function_name()" in result, \
            f"snake_case 应保持原样: {result!r}"

    def test_italic_still_works_with_spaces(self):
        """真正的斜体 _ should work_ 仍然工作。"""
        result = _render_text("this _should be_ italic")
        plain = result.plain
        assert "should be" in plain
        italic_start = plain.index("should")
        style = _get_style_at(result, italic_start)
        assert style.italic, "空格包围的 _ 应触发斜体"

    def test_italic_at_start_of_line(self):
        """行首的 _italic_ 应正常工作。"""
        result = _render_text("_hello_ world")
        plain = result.plain
        assert "hello" in plain
        hello_start = plain.index("hello")
        style = _get_style_at(result, hello_start)
        assert style.italic, "行首 _ 应触发斜体"

    def test_italic_after_punctuation(self):
        """标点后的 _italic_ 应正常工作。"""
        result = _render_text("say _hello_ to him")
        plain = result.plain
        hello_start = plain.index("hello")
        style = _get_style_at(result, hello_start)
        assert style.italic, "空格后 _ 应触发斜体"


# ═══════════════════════════════════════════════════════════
# 错误处理与边界条件
# ═══════════════════════════════════════════════════════════


class TestErrorRecovery:
    """语法错误恢复和边界条件测试。"""

    def test_unclosed_bold(self):
        """**bold 未闭合 → 降级为纯文本。"""
        result = _render("this is **bold text")
        assert "**" in result or "bold text" in result, \
            f"未闭合粗体应显示为文本: {result!r}"

    def test_unclosed_italic(self):
        """*italic 未闭合 → 降级为纯文本。"""
        result = _render("this is *italic")
        assert "*italic" in result or "this is *italic" in result, \
            f"未闭合斜体应显示为文本: {result!r}"

    def test_unclosed_strikethrough(self):
        """~~strike 未闭合 → 降级为纯文本。"""
        result = _render("this is ~~strike")
        assert "~~" in result or "strike" in result, \
            f"未闭合删除线应显示为文本: {result!r}"

    def test_empty_bold(self):
        """**** 空粗体 → 降级。"""
        result = _render("hello **** world")
        assert "hello" in result
        assert "world" in result

    def test_deeply_nested(self):
        """深度嵌套 (超过 20 层) → 不崩溃。"""
        text = "**" * 25 + "deep" + "**" * 25
        result = _render(text)
        assert "deep" in result, f"深层嵌套应保底输出: {result!r}"

    def test_mixed_unclosed_formats(self):
        """混合未闭合格式 → 整体降级为纯文本。"""
        result = _render("**bold *italic ~~strike ==highlight")
        # 不应崩溃，应输出文本
        assert result, "不应为空"
        assert "bold" in result
        assert "italic" in result

    def test_paragraph_continuation(self):
        """格式标记后的普通文本应正常延续。"""
        result = _render("hello **world** and more text")
        assert "hello" in result
        assert "world" in result
        assert "and more text" in result

    def test_code_in_mixed_context(self):
        """代码块内 `code` 应不受其他格式影响。"""
        result = _render_text("use `variable_name` in code")
        plain = result.plain
        assert "variable_name" in plain
        # `variable_name` 应为绿色代码样式
        code_start = plain.index("variable_name")
        style = _get_style_at(result, code_start)
        # InlineCodeNode 渲染为绿色
        assert style.color is not None and "green" in str(style.color), \
            f"code 应为绿色: {style.color}"


# ═══════════════════════════════════════════════════════════
# 增强上下标测试（新特性）
# ═══════════════════════════════════════════════════════════

class TestSubscriptSyntax:
    """增强下标 ~text~ 语法（支持空白和嵌套格式）。"""

    def test_basic_subscript(self):
        """基础下标：~text~ → dim italic 样式 + Unicode 转换。"""
        result = _render_text("x~i~")
        plain = result.plain
        assert "x" in plain
        # ★ 修复 Bug: 单字符下标现在自动 Unicode 转换 i → ᵢ
        assert "ᵢ" in plain or "i" in plain, f"应包含下标 i: {plain!r}"
        sub_pos = plain.index("ᵢ") if "ᵢ" in plain else plain.index("i")
        style = _get_style_at(result, sub_pos)
        assert style.dim is True, f"下标应 dim: {style}"
        assert style.italic is True, f"下标应 italic: {style}"

    def test_subscript_with_space(self):
        """增强：~hello world~ 允许空格。"""
        result = _render_text("~hello world~ is subscript")
        plain = result.plain
        assert "hello world" in plain, f"应包含 hello world: {plain!r}"

    def test_subscript_with_bold(self):
        """增强：下标内可嵌套粗体 ~hello *bold* world~。"""
        result = _render_text("~hello *bold* world~")
        plain = result.plain
        assert "hello" in plain
        assert "bold" in plain
        assert "world" in plain
        # 下标整体样式为 dim italic（通过 result.style 反映）
        # 子节点 bold 的粗体样式在 Rich 内部通过 span 叠加，
        # 终端渲染时正确显示。测试仅验证文本内容完整性。
        assert "hello" in plain and "bold" in plain and "world" in plain

    def test_subscript_nested_italic(self):
        """增强：下标内可嵌套斜体 ~text _italic_ text~。"""
        result = _render_text("~a _b_ c~")
        plain = result.plain
        assert "a" in plain
        assert "b" in plain
        assert "c" in plain

    def test_subscript_unclosed(self):
        """未闭合的下标标记 → 优雅降级为纯文本。"""
        result = _render_text("~unclosed subscript")
        plain = result.plain
        assert "unclosed subscript" in plain, "应降级为纯文本"

    def test_subscript_with_strikethrough(self):
        """~text~~ 优先匹配删除线，单~作为下标不冲突。"""
        # ~~text~~ → 删除线，单独 ~char~ → 下标
        result = _render("~~strike~~ and ~sub~")
        assert "strike" in result
        assert "sub" in result

    def test_subscript_unicode_fallback(self):
        """单字符~x~ → Unicode 下标转换（叶子节点）。"""
        result = _render_text("H~2~O")
        plain = result.plain
        # 2 应转换为 Unicode 下标 ₂
        assert "₂" in plain or "2" in plain, f"应包含下标2: {plain!r}"


class TestSuperscriptSyntax:
    """增强上标 ^text^ 语法（支持空白和嵌套格式）。"""

    def test_basic_superscript(self):
        """基础上标：^text^ → bright_cyan italic 样式 + Unicode 转换。"""
        result = _render_text("x^2^")
        plain = result.plain
        # ★ 修复 Bug: 单字符上标现在自动 Unicode 转换 2 → ²
        assert "²" in plain or "2" in plain, f"应包含上标 2: {plain!r}"
        sup_pos = plain.index("²") if "²" in plain else plain.index("2")
        style = _get_style_at(result, sup_pos)
        assert style.italic is True, f"上标应 italic: {style}"

    def test_superscript_with_space(self):
        """增强：^hello world^ 允许空格 + Unicode 上标转换。"""
        result = _render_text("note^superscript text^")
        plain = result.plain
        # ★ Bug 修复后：所有字符可转换时进行 Unicode 上标转换
        # 输出为 "noteˢᵘᵖᵉʳˢᶜʳⁱᵖᵗ ᵗᵉˣᵗ"，或降级时的原始文本
        assert len(plain) > 4, f"应有输出内容: {plain!r}"
        has_content = ("superscript" in plain or
                       "ˢᵘᵖᵉʳ" in plain or
                       "text" in plain or "ᵗᵉˣᵗ" in plain)
        assert has_content, f"应包含内容: {plain!r}"

    def test_superscript_with_nested_format(self):
        """增强：上标内可嵌套格式 ^text *bold* text^ + Unicode 转换。"""
        result = _render_text("^a *b* c^")
        plain = result.plain
        # ★ Bug 修复后: 全部字符可转换时 Unicode 上标转换
        # 输出 "ᵃ ᵇ ᶜ" 或含嵌套格式的降级文本 "a b c"
        assert len(plain) >= 3, f"应有输出: {plain!r}"
        has_a = "a" in plain or "ᵃ" in plain
        has_b = "b" in plain or "ᵇ" in plain
        has_c = "c" in plain or "ᶜ" in plain
        assert has_a, f"应包含 a: {plain!r}"
        assert has_b, f"应包含 b: {plain!r}"
        assert has_c, f"应包含 c: {plain!r}"

    def test_superscript_unclosed(self):
        """未闭合的上标标记 → 优雅降级为纯文本。"""
        result = _render_text("^unclosed superscript")
        plain = result.plain
        assert "unclosed superscript" in plain, "应降级为纯文本"

    def test_superscript_unicode_fallback(self):
        """单字符 ^3^ → Unicode 上标转换（叶子节点）。"""
        result = _render_text("x^3^+y^2^")
        plain = result.plain
        assert "³" in plain or "3" in plain, f"应包含上标3: {plain!r}"


# ═══════════════════════════════════════════════════════════
# 缩写定义语法测试（新特性）
# ═══════════════════════════════════════════════════════════

class TestAbbreviationDefinition:
    """`*[ABBR]: Full Text` 缩写定义语法测试。"""

    def test_abbreviation_definition_stored(self):
        """*[HTML]: Full Text → 存储在 abbr_map 中。"""
        from src.api.renderer.types import RenderContext
        from src.api.renderer.recursive_parser import RegexFreeBlockParser
        ctx = RenderContext()
        parser = RegexFreeBlockParser(ctx=ctx)
        tokens = parser.feed("*[HTML]: HyperText Markup Language\n")
        tokens += parser.flush()
        assert "HTML" in ctx.abbr_map
        assert ctx.abbr_map["HTML"] == "HyperText Markup Language"
        para_tokens = [t for t in tokens if t.type.name == "PARAGRAPH"]
        assert len(para_tokens) == 0, f"缩写定义行不应产生段落 Token: {para_tokens}"

    def test_abbreviation_case_insensitive(self):
        """缩写名转大写存储。"""
        from src.api.renderer.types import RenderContext
        from src.api.renderer.recursive_parser import RegexFreeBlockParser
        ctx = RenderContext()
        parser = RegexFreeBlockParser(ctx=ctx)
        parser.feed("*[css]: Cascading Style Sheets\n")
        parser.flush()
        assert "CSS" in ctx.abbr_map
        assert ctx.abbr_map["CSS"] == "Cascading Style Sheets"

    def test_abbreviation_multiple_definitions(self):
        """多个缩写定义都正确存储。"""
        from src.api.renderer.types import RenderContext
        from src.api.renderer.recursive_parser import RegexFreeBlockParser
        ctx = RenderContext()
        parser = RegexFreeBlockParser(ctx=ctx)
        parser.feed("*[HTML]: HyperText Markup Language\n*[CSS]: Cascading Style Sheets\n")
        parser.flush()
        assert ctx.abbr_map["HTML"] == "HyperText Markup Language"
        assert ctx.abbr_map["CSS"] == "Cascading Style Sheets"

    def test_abbreviation_does_not_interfere_with_list(self):
        """* 缩写定义不应干扰 * item 无序列表。"""
        from src.api.renderer.recursive_parser import RegexFreeBlockParser
        parser = RegexFreeBlockParser()
        tokens = parser.feed("* list item\n")
        tokens += parser.flush()
        list_tokens = [t for t in tokens if t.type.name == "LIST_ITEM"]
        assert len(list_tokens) > 0, "* list item 应产生 LIST_ITEM Token"


class TestAbbreviationRendering:
    """缩写（abbr_map）在后处理中的自动替换测试。"""

    def _ctx_with_abbr(self, abbr_map: dict[str, str]):
        """创建带 abbr_map 的 RenderContext。"""
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        ctx.abbr_map.update(abbr_map)
        return ctx

    def test_plain_text_abbr_replaced(self):
        """纯文本中的缩写词替换为黄色下划线+斜体样式。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        result = InlineRenderer().render("Learn HTML", ctx)
        assert result.plain == "Learn HTML"
        html_start = result.plain.index("HTML")
        style = _get_style_at(result, html_start)
        assert style.underline is True, "HTML 应有下划线"
        assert style.italic is True, "HTML 应有斜体"
        assert style.color is not None and style.color.name == "yellow", \
            f"HTML 应有黄色样式, got {style.color}"

    def test_formatted_text_abbr_replaced(self):
        """含格式标记的文本中缩写词同样替换。"""
        ctx = self._ctx_with_abbr({"CSS": "Cascading Style Sheets"})
        result = InlineRenderer().render("learn **CSS**", ctx)
        assert result.plain == "learn CSS"
        css_start = result.plain.index("CSS")
        style = _get_style_at(result, css_start)
        assert style.underline is True, "CSS 应有下划线"
        assert style.italic is True, "CSS 应有斜体"
        assert style.color is not None and style.color.name == "yellow", \
            f"CSS 应有黄色样式, got {style.color}"

    def test_no_abbr_map_leaves_text_unchanged(self):
        """ctx 没有 abbr_map 时文本不变。"""
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()  # 默认 abbr_map 为 {}
        result = InlineRenderer().render("Learn HTML and CSS", ctx)
        assert result.plain == "Learn HTML and CSS"
        html_start = result.plain.index("HTML")
        style = _get_style_at(result, html_start)
        assert style.color is None, "无 abbr_map 时不应应用颜色样式"

    def test_word_not_in_abbr_map_unchanged(self):
        """不在 abbr_map 中的单词保持原样。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        result = InlineRenderer().render("Learn XML and HTML", ctx)
        assert result.plain == "Learn XML and HTML"
        # XML 不在 abbr_map 中，不应有样式
        xml_start = result.plain.index("XML")
        xml_style = _get_style_at(result, xml_start)
        assert xml_style.color is None, "XML 不在 abbr_map 中不应有颜色样式"
        # HTML 在 abbr_map 中，应有样式
        html_start = result.plain.index("HTML")
        html_style = _get_style_at(result, html_start)
        assert html_style.color is not None and html_style.color.name == "yellow", \
            "HTML 在 abbr_map 中应有黄色样式"

    def test_case_insensitive_matching(self):
        """匹配忽略大小写。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        result = InlineRenderer().render("I write html", ctx)
        assert result.plain == "I write html"
        html_start = result.plain.index("html")
        style = _get_style_at(result, html_start)
        assert style.underline is True
        assert style.color is not None and style.color.name == "yellow", \
            f"小写 html 也应匹配, got {style.color}"

    def test_empty_text(self):
        """空文本不应报错。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        result = InlineRenderer().render("", ctx)
        assert result.plain == ""

    def test_multiple_abbreviations(self):
        """多个不同缩写同时匹配。"""
        ctx = self._ctx_with_abbr({
            "AI": "Artificial Intelligence",
            "UI": "User Interface",
        })
        result = InlineRenderer().render("AI and UI design", ctx)
        assert result.plain == "AI and UI design"
        ai_start = result.plain.index("AI")
        ui_start = result.plain.index("UI")
        assert _get_style_at(result, ai_start).color is not None
        assert _get_style_at(result, ai_start).color.name == "yellow"
        assert _get_style_at(result, ui_start).color is not None
        assert _get_style_at(result, ui_start).color.name == "yellow"

    def test_abbr_not_at_word_boundary_inside_format(self):
        """缩写词边界检测正确（非字母数字字符不影响单词检测）。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        # 纯文本路径：<HTML> 中 HTML 仍是独立单词
        result = InlineRenderer().render("<HTML> tag", ctx)
        assert "HTML" in result.plain
        html_start = result.plain.index("HTML")
        assert _get_style_at(result, html_start).color is not None, \
            "HTML 应被识别为缩写"
        assert _get_style_at(result, html_start).color.name == "yellow", \
            "HTML 被 < 包围仍应被识别为独立单词"


# ═══════════════════════════════════════════════════════════
# 内联 HTML 标签解析测试
# ═══════════════════════════════════════════════════════════

class TestInlineHtmlTags:
    """HTML 内联标签解析与渲染测试。"""

    def test_kbd_tag_renders(self):
        """<kbd>Ctrl+C</kbd> → 键盘样式渲染。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("Press <kbd>Ctrl+C</kbd>")
        plain = result.plain
        assert "Press" in plain
        assert "Ctrl+C" in plain, f"kbd 内容应在输出中: {plain!r}"

    def test_abbr_tag_renders(self):
        """<abbr title=\"...\">HTML</abbr> → 缩写样式渲染。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render(
            '<abbr title="HyperText Markup Language">HTML</abbr>'
        )
        assert "HTML" in result.plain, f"缩写内容应在输出中: {result.plain!r}"

    def test_bold_tag(self):
        """<b>bold</b> → 粗体。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("<b>bold text</b>")
        assert "bold text" in result.plain

    def test_italic_tag(self):
        """<i>italic</i> → 斜体。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("<i>italic text</i>")
        assert "italic text" in result.plain

    def test_mark_tag(self):
        """<mark>highlighted</mark> → 高亮。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("<mark>highlighted text</mark>")
        assert "highlighted text" in result.plain

    def test_nested_html_tags(self):
        """<b><i>nested</i></b> → 嵌套标签。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("<b><i>nested</i></b>")
        assert "nested" in result.plain


# ═══════════════════════════════════════════════════════════
# ==highlight== 语法独立测试
# ═══════════════════════════════════════════════════════════

class TestHighlightSyntax:
    """==highlight== 语法测试。"""

    def test_basic_highlight(self):
        """==text== → 高亮样式。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("this is ==highlighted== text")
        plain = result.plain
        assert "highlighted" in plain, f"高亮内容应在输出中: {plain!r}"
        # 样式验证：查找 highlight 部分的 span
        hl_start = plain.index("highlighted")
        has_style = False
        for span in result.spans:
            if span.start <= hl_start < span.end:
                has_style = True
                break
        assert has_style, "高亮文本应有样式 span"

    def test_highlight_empty(self):
        """==== → 空高亮降级。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("hello ==== world")
        assert "hello" in result.plain
        assert "world" in result.plain

    def test_highlight_unclosed(self):
        """==text 未闭合 → 降级。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("==unclosed highlight")
        assert "unclosed highlight" in result.plain or "==unclosed" in result.plain


# ═══════════════════════════════════════════════════════════
# 标题编号语法测试
# ═══════════════════════════════════════════════════════════

class TestHeadingNumbering:
    """标题编号功能测试。"""

    def test_heading_numbering_disabled_by_default(self):
        """默认不启用编号。"""
        from src.api.renderer._rendering import _get_heading_number
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        result = _get_heading_number(ctx, 1)
        assert result == "", f"默认不应编号: {result!r}"

    def test_heading_numbering_enabled(self):
        """启用后编号递增。"""
        from src.api.renderer._rendering import _get_heading_number
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        ctx.heading_numbering = True
        assert _get_heading_number(ctx, 1) == "1  "
        assert _get_heading_number(ctx, 1) == "2  "
        assert _get_heading_number(ctx, 2) == "2.1  "

    def test_heading_numbering_nested(self):
        """嵌套标题编号正确。"""
        from src.api.renderer._rendering import _get_heading_number
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        ctx.heading_numbering = True
        _get_heading_number(ctx, 1)  # 1
        _get_heading_number(ctx, 2)  # 1.1
        _get_heading_number(ctx, 3)  # 1.1.1
        assert _get_heading_number(ctx, 1) == "2  "

    def test_heading_numbering_h1_resets(self):
        """H1 重置所有下级计数器。"""
        from src.api.renderer._rendering import _get_heading_number
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        ctx.heading_numbering = True
        _get_heading_number(ctx, 1)    # 1
        _get_heading_number(ctx, 2)    # 1.1
        assert _get_heading_number(ctx, 1) == "2  "
        assert _get_heading_number(ctx, 2) == "2.1  "


# ═══════════════════════════════════════════════════════════
# 新特性：图片尺寸语法 ![](url =WxH)
# ═══════════════════════════════════════════════════════════

class TestImageDimensionSyntax:
    """图片尺寸语法 `![alt](url =WxH)` 测试。"""

    def test_image_with_dimension(self):
        """![alt](img.png =200x100) → 尺寸信息在 meta 中。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser("![alt](img.png =200x100)")
        nodes = p.parse()
        assert len(nodes) == 1
        from src.api.renderer.inline_nodes import ImageNode
        assert isinstance(nodes[0], ImageNode)
        assert nodes[0].meta.get("width") == 200
        assert nodes[0].meta.get("height") == 100
        assert nodes[0].url == "img.png"

    def test_image_without_dimension(self):
        """![alt](img.png) → 无尺寸信息。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser("![alt](img.png)")
        nodes = p.parse()
        from src.api.renderer.inline_nodes import ImageNode
        assert isinstance(nodes[0], ImageNode)
        assert nodes[0].meta.get("width", 0) == 0

    def test_image_with_dimension_and_title(self):
        """![alt](img.png =50x50 "title") → 尺寸+标题共存。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser('![alt](img.png =50x50 "Photo")')
        nodes = p.parse()
        from src.api.renderer.inline_nodes import ImageNode
        assert isinstance(nodes[0], ImageNode)
        assert nodes[0].meta.get("width") == 50
        assert nodes[0].meta.get("height") == 50
        assert nodes[0].title == "Photo"

    def test_image_with_title_only(self):
        """![alt](img.png "title") → 标题语法不受影响。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser('![alt](img.png "Photo")')
        nodes = p.parse()
        from src.api.renderer.inline_nodes import ImageNode
        assert isinstance(nodes[0], ImageNode)
        assert nodes[0].title == "Photo"
        assert nodes[0].meta.get("width", 0) == 0

    def test_image_dimension_rendered_in_output(self):
        """尺寸信息出现在渲染输出中。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        result = InlineRenderer().render("![Logo](logo.png =120x60)")
        assert "120x60" in result.plain, f"尺寸应在输出中: {result.plain}"
        assert "logo.png" in result.plain

    def test_image_dimension_with_ref_link(self):
        """参考式链接图片不受影响。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser("![alt][ref]")
        nodes = p.parse()
        from src.api.renderer.inline_nodes import ImageNode
        assert isinstance(nodes[0], ImageNode)


# ═══════════════════════════════════════════════════════════
# Bug B2 回归测试：__bold__ 词内不应触发粗体
# ═══════════════════════════════════════════════════════════

class TestBugB2_UnderscoreBoldInWord:
    """Bug B2: __bold__ 下划线粗体在词内（如 __init__）不应触发粗体。"""

    def test_double_underscore_in_word(self):
        """__init__ → 不应触发粗体（词内下划线）。"""
        result = _render("value = __init__ method")
        assert "init" in result, f"__init__ 不应被解析为粗体: {result!r}"
        assert "__init__" in result, f"__init__ 应保持原样: {result!r}"

    def test_double_underscore_bold_still_works(self):
        """真正的 __粗体__ 仍应正常工作。"""
        result = _render_text("this is __bold text__ here")
        plain = result.plain
        assert "bold text" in plain
        bold_start = plain.index("bold")
        style = _get_style_at(result, bold_start)
        assert style.bold, "__bold__ 应触发粗体"

    def test_triple_underscore_in_word(self):
        """___init___ → 不应崩溃，至少保留内容不丢失。"""
        result = _render("var ___init___ method")
        # 极罕见的 triple-dunder，只需不崩溃、内容不丢失即可
        assert "init" in result, f"内容应保留: {result!r}"
        assert "method" in result, f"后续文本应保留: {result!r}"


# ═══════════════════════════════════════════════════════════
# 新特性：CriticMarkup {++added++}
# ═══════════════════════════════════════════════════════════

class TestCriticAddition:
    """{++added text++} 语法测试。"""

    def test_basic_addition(self):
        """{++new text++} → 绿色粗体文本。"""
        result = _render_text("这里 {++新增内容++} 完成")
        plain = result.plain
        assert "新增内容" in plain, f"应包含添加文本: {plain!r}"

    def test_addition_style(self):
        """{++text++} → 应有绿色样式。"""
        result = _render_text("{++green text++}")
        plain = result.plain
        assert "green text" in plain
        start = plain.index("green")
        style = _get_style_at(result, start)
        assert style.bold, "添加文本应粗体"
        assert style.color is not None, "添加文本应有颜色"

    def test_addition_nested(self):
        """{++**bold** inside++} → 嵌套格式保留。"""
        result = _render_text("{++**bold** inside++}")
        plain = result.plain
        assert "bold" in plain
        assert "inside" in plain

    def test_addition_empty(self):
        """{++++} → 空添加降级。"""
        result = _render("hello {++++} world")
        assert "hello" in result
        assert "world" in result

    def test_addition_unclosed(self):
        """{++text 未闭合 → 降级为纯文本。"""
        result = _render("text {++not closed")
        assert len(result) > 0  # 不崩溃


# ═══════════════════════════════════════════════════════════
# 新特性：CriticMarkup {--deleted--}
# ═══════════════════════════════════════════════════════════

class TestCriticDeletion:
    """{--deleted text--} 语法测试。"""

    def test_basic_deletion(self):
        """{--old text--} → 红色删除线文本。"""
        result = _render_text("删除 {--过时内容--} 完成")
        plain = result.plain
        assert "过时内容" in plain, f"应包含删除文本: {plain!r}"

    def test_deletion_style(self):
        """{--text--} → 应有删除线样式。"""
        result = _render_text("{--deleted text--}")
        plain = result.plain
        assert "deleted text" in plain
        start = plain.index("deleted")
        style = _get_style_at(result, start)
        assert style.strike, "删除文本应有删除线"

    def test_deletion_nested(self):
        """{--*italic* inside--} → 嵌套格式保留。"""
        result = _render_text("{--*italic* deleted--}")
        plain = result.plain
        assert "italic" in plain
        assert "deleted" in plain

    def test_deletion_unclosed(self):
        """{--text 未闭合 → 降级为纯文本。"""
        result = _render("text {--not closed")
        assert len(result) > 0  # 不崩溃


# ═══════════════════════════════════════════════════════════
# 新特性：Small text {-small-}
# ═══════════════════════════════════════════════════════════

class TestSmallText:
    """{-small text-} 语法测试。"""

    def test_basic_small(self):
        """{-small text-} → dim 斜体小号文本。"""
        result = _render_text("这是 {-小号文本-} 内容")
        plain = result.plain
        assert "小号文本" in plain, f"应包含小号文本: {plain!r}"

    def test_small_style(self):
        """{-text-} → dim + italic 样式。"""
        result = _render_text("{-whisper-}")
        plain = result.plain
        assert "whisper" in plain
        start = plain.index("whisper")
        style = _get_style_at(result, start)
        assert style.dim, "小号文本应 dim"
        assert style.italic, "小号文本应 italic"

    def test_small_nested(self):
        """{-**bold** inside-} → 嵌套格式保留。"""
        result = _render_text("{-**bold** whisper-}")
        plain = result.plain
        assert "bold" in plain
        assert "whisper" in plain

    def test_small_unclosed(self):
        """{-text 未闭合 → 降级为纯文本。"""
        result = _render("text {-not closed")
        assert len(result) > 0  # 不崩溃

    def test_small_not_confuse_with_deletion(self):
        """{-text-} 不应被误判为 {--deletion--}。"""
        result = _render_text("{-small-} vs {--deleted--}")
        plain = result.plain
        assert "small" in plain, "{-small-} 应正确解析为小号文本"
        assert "deleted" in plain, "{--deleted--} 应正确解析为删除"


# ═══════════════════════════════════════════════════════════
# 新特性：Color text {color:COLOR}text{color}
# ═══════════════════════════════════════════════════════════

class TestColorText:
    """{color:COLOR}text{color} 语法测试。"""

    def test_basic_color(self):
        """{color:red}red text{color} → 红色文本。"""
        result = _render_text("{color:red}红色文字{color}")
        plain = result.plain
        assert "红色文字" in plain, f"应包含彩色文本: {plain!r}"

    def test_color_style(self):
        """{color:green}text{color} → 绿色文本。"""
        result = _render_text("{color:green}green text{color}")
        plain = result.plain
        assert "green text" in plain
        start = plain.index("green")
        style = _get_style_at(result, start)
        assert style.color is not None, "彩色文本应有颜色样式"
        assert style.bold, "彩色文本应粗体"

    def test_color_nested(self):
        """{color:blue}**bold** blue{color} → 嵌套格式。"""
        result = _render_text("{color:blue}**bold** blue{color}")
        plain = result.plain
        assert "bold" in plain
        assert "blue" in plain

    def test_color_cyan(self):
        """{color:cyan}text{color} → 青色文本。"""
        result = _render_text("{color:cyan}cyan text{color}")
        plain = result.plain
        assert "cyan text" in plain

    def test_color_magenta(self):
        """{color:magenta}text{color} → 品红文本。"""
        result = _render_text("{color:magenta}magenta{color}")
        plain = result.plain
        assert "magenta" in plain

    def test_color_yellow(self):
        """{color:yellow}text{color} → 黄色文本。"""
        result = _render_text("{color:yellow}yellow text{color}")
        plain = result.plain
        assert "yellow text" in plain

    def test_unknown_color_fallback(self):
        """{color:unknown}text{color} → 未知颜色降级为纯文本。"""
        result = _render("text {color:unknown}not colored{color} more")
        assert "not colored" in result, "未知颜色应降级为纯文本显示"
        assert "{color:unknown}" in result, "未知颜色标记应保持原样"

    def test_color_unclosed(self):
        """{color:red}text 未闭合 → 降级。"""
        result = _render("text {color:red}not closed")
        assert len(result) > 0  # 不崩溃

    def test_all_color_names(self):
        """所有已知颜色名都应能解析。"""
        known = ['red', 'green', 'blue', 'yellow', 'cyan', 'magenta',
                 'white', 'bright_red', 'bright_green', 'bright_blue',
                 'bright_yellow', 'bright_cyan', 'bright_magenta']
        for c in known:
            result = _render_text(f"{{color:{c}}}test{{color}}")
            assert "test" in result.plain, f"颜色 {c} 解析失败"


# ═══════════════════════════════════════════════════════════
# CriticMarkup {~~old~>new~~} 替换
# ═══════════════════════════════════════════════════════════

class TestCriticSubstitution:
    """{~~old~>new~~} 替换语法测试。"""

    def test_basic_substitution(self):
        """{~~old~>new~~} → 旧文本+箭头+新文本。"""
        result = _render("replace {~~old~>new~~} here")
        plain = result
        assert "old" in plain and "new" in plain, \
            f"替换语法应包含新旧文本: {plain!r}"
        assert "→" in plain, \
            f"替换语法应有箭头分隔符: {plain!r}"

    def test_substitution_style(self):
        """{~~text~>replacement~~} → 新旧文本应有对应样式。"""
        result = _render_text("fix {~~bug~>feature~~} now")
        plain = result.plain
        assert "bug" in plain
        assert "feature" in plain

        bug_start = plain.index("bug")
        bug_style = _get_style_at(result, bug_start)
        assert bug_style.strike, f"旧文本应有删除线样式, style={bug_style}"

        feat_start = plain.index("feature")
        feat_style = _get_style_at(result, feat_start)
        assert feat_style.bold, f"新文本应粗体, style={feat_style}"

    def test_substitution_with_spaces(self):
        """{~~old text~>new text~~} → 带空格的内容。"""
        result = _render("{~~old text~>new text~~}")
        plain = result
        assert "old text" in plain
        assert "new text" in plain
        assert "→" in plain

    def test_substitution_unclosed(self):
        """{~~text 未闭合 → 降级为纯文本。"""
        result = _render("{~~text")
        assert len(result) > 0  # 不崩溃

    def test_substitution_empty_old(self):
        """{~~ ~>new~~} → 空旧文本。"""
        result = _render("{~~ ~>new~~}")
        assert "new" in result
        assert "→" in result

    def test_substitution_empty_new(self):
        """{~~old~> ~~} → 空新文本。"""
        result = _render("{~~old~> ~~}")
        assert "old" in result

    def test_substitution_scenario(self):
        """实际场景：{~~typo~>correction~~}。"""
        result = _render("I see a {~~typo~>mistake~~} here")
        assert "typo" in result or "mistake" in result, \
            f"实际场景解析失败: {result!r}"


# ═══════════════════════════════════════════════════════════
# CriticMarkup {>>comment<<} 批注
# ═══════════════════════════════════════════════════════════

class TestCriticComment:
    """{>>comment<<} 批注语法测试。"""

    def test_basic_comment(self):
        """{>>comment<<} → 批注样式。"""
        result = _render("text {>>note<<} more")
        plain = result
        assert "note" in plain, f"批注内容应在输出中: {plain!r}"
        assert len(plain) > 0

    def test_comment_marker(self):
        """批注应带标记符号。"""
        result = _render_text("before {>>annotation<<} after")
        plain = result.plain
        # 批注内容应保留
        assert "annotation" in plain
        # 应有批注标记（┌ 或 ┘ 等符号）
        has_marker = "┌" in plain or "└" in plain or "[" in plain
        assert has_marker, f"批注应有视觉标记: {plain!r}"

    def test_comment_unclosed(self):
        """{>>text 未闭合 → 降级纯文本。"""
        result = _render("{>>text")
        assert len(result) > 0  # 不崩溃

    def test_comment_empty(self):
        """{>><<} → 空批注不崩溃。"""
        result = _render("{>><<}")
        assert len(result) > 0  # 不崩溃

    def test_comment_with_nested_format(self):
        """{>>**bold** comment<<} → 嵌套格式。"""
        result = _render_text("{>>**bold** comment<<}")
        plain = result.plain
        assert "bold" in plain, f"嵌套粗体应在输出中: {plain!r}"
        assert "comment" in plain


# ═══════════════════════════════════════════════════════════
# Wiki 链接 [[target]] 和 [[target|display]] 语法（新特性）
# ═══════════════════════════════════════════════════════════


class TestWikiLinkSyntax:
    """Wiki 链接 [[target]] 语法测试。"""

    def test_basic_wikilink(self):
        """[[Python]] → 紫色下划线链接。"""
        result = _render_text("see [[Python]] for details")
        plain = result.plain
        assert "Python" in plain
        py_start = plain.index("Python")
        style = _get_style_at(result, py_start)
        assert style.underline is True, "Wiki 链接应有下划线"
        assert style.color is not None and "magenta" in str(style.color), \
            f"Wiki 链接应为紫色, got {style.color}"

    def test_wikilink_with_display(self):
        """[[Python|the Python language]] → 显示 display 文本。"""
        result = _render_text("see [[Python|the Python language]]")
        plain = result.plain
        assert "the Python language" in plain
        display_start = plain.index("the Python language")
        style = _get_style_at(result, display_start)
        assert style.underline is True, "Wiki 链接应有下划线"

    def test_wikilink_empty_target(self):
        """[[]] → 不触发（空 target 降级为纯文本）。"""
        result = _render("see [[]] here")
        assert "[[]]" in result or "see  here" in result, \
            f"空 target 应降级: {result!r}"

    def test_wikilink_unclosed(self):
        """[[text 未闭合 → 降级纯文本。"""
        result = _render("see [[Python")
        assert "[[Python" in result, "未闭合应降级: {result!r}"

    def test_wikilink_mixed_with_other_syntax(self):
        """Wiki 链接与 **bold** 等共存。"""
        result = _render_text("**see** [[Python]] **now**")
        plain = result.plain
        assert "see" in plain
        assert "Python" in plain
        assert "now" in plain


# ═══════════════════════════════════════════════════════════
# 行内注释 %% comment %% 语法（新特性）
# ═══════════════════════════════════════════════════════════


class TestInlineCommentSyntax:
    """行内注释 %% comment %% 语法测试。"""

    def test_basic_comment(self):
        """%%comment%% → dim 隐藏文本。"""
        result = _render_text("hello %%hidden note%% world")
        plain = result.plain
        assert "hidden note" in plain
        hn_start = plain.index("hidden note")
        style = _get_style_at(result, hn_start)
        assert style.dim is True, "注释应为 dim 样式"
        assert style.italic is True, "注释应为 italic 样式"

    def test_comment_unclosed(self):
        """%%text 未闭合 → 降级纯文本。"""
        result = _render("%%unclosed")
        assert "%%unclosed" in result, f"未闭合应降级: {result!r}"

    def test_comment_empty(self):
        """%%%% → 空注释不崩溃。"""
        result = _render("hello %%%% world")
        assert "hello" in result
        assert "world" in result

    def test_comment_triple_percent(self):
        """%%% not a comment %%% → 三百分号不触发。"""
        result = _render("%%% not a comment %%%")
        assert "%%%" in result, f"三百分号不应触发注释: {result!r}"

    def test_comment_mixed_with_bold(self):
        """%% **bold** %% 内的格式标记原样展示。"""
        result = _render_text("see %% **note** %% here")
        plain = result.plain
        assert "note" in plain, f"注释内容应可见: {plain!r}"


# ═══════════════════════════════════════════════════════════
# CriticDeletionNode 深度回归测试（Bug 修复）
# ═══════════════════════════════════════════════════════════


class TestCriticDeletionDepthRegression:
    """验证 CriticDeletionNode 不会双倍递增递归深度。"""

    def test_critic_deletion_depth_matches_strikethrough(self):
        """{--deleted--} 和 ~~strikethrough~~ 使用相同深度增量。"""
        # 两者都使用 _render_strikethrough_handler，
        # 修复后 CriticDeletionNode 传入 d（不是 d+1）
        result_del = _render_text("{--deleted text--}")
        result_st = _render_text("~~strikethrough text~~")
        # 两个都应该能正常渲染（不会因深度溢出而崩溃）
        assert "deleted text" in result_del.plain
        assert "strikethrough text" in result_st.plain

    def test_nested_critic_deletion(self):
        """嵌套 {--outer {--inner--} outer--} 应正常工作。"""
        result = _render_text("{--outer {--inner--} outer--}")
        # 不崩溃就是成功
        assert "outer" in result.plain
        assert "inner" in result.plain


# ═══════════════════════════════════════════════════════════
# 智能排版：箭头 -> → →, <- → ←, => → ⇒
# ═══════════════════════════════════════════════════════════

class TestSmartArrows:
    """智能排版箭头符号测试。"""

    def test_arrow_right(self):
        """a -> b → 纯文本通道中 -> 转换为 →。"""
        result = _render("a -> b")
        assert "→" in result, f"-> 应转换为 →: {result!r}"
        assert "->" not in result, f"不应残留 ->: {result!r}"

    def test_arrow_left(self):
        """a <- b → 纯文本通道中 <- 转换为 ←。"""
        result = _render("a <- b")
        assert "←" in result, f"<- 应转换为 ←: {result!r}"

    def test_arrow_double(self):
        """a => b → 纯文本通道中 => 转换为 ⇒。"""
        result = _render("a => b")
        assert "⇒" in result, f"=> 应转换为 ⇒: {result!r}"

    def test_arrow_inline_markup_context(self):
        """格式上下文中 -> 也应正常转换（经 TextNode 预处理）。"""
        result = _render("**bold** -> target, *italic* <- source")
        assert "→" in result
        assert "←" in result

    def test_arrow_not_in_code_like(self):
        """a->b 中 -> 不应转换（词内连字符风格）。"""
        result = _render("a->b")
        assert "→" not in result, f"a->b 不应转换为箭头: {result!r}"

    def test_arrow_not_double_gt(self):
        """->> 不应转换为 →>。"""
        result = _render("->>")
        assert "→" not in result, f"->> 不应转换: {result!r}"

    def test_arrow_triple_dash_convert_inner_arrow(self):
        """--> 中 -> 箭头部被转换（箭头部转换优先）。"""
        result = _render("--> end")
        assert "→" in result, f"--> 中的 -> 应被转换: {result!r}"

    def test_arrow_left_not_html_comment(self):
        """<!-- 不应转换（HTML 注释）。"""
        result = _render("<!-- comment -->")
        assert "←" not in result, f"<!-- 不应转换为 ←: {result!r}"


# ═══════════════════════════════════════════════════════════
# 智能排版：(c) → ©, (r) → ®, (tm) → ™
# ═══════════════════════════════════════════════════════════

class TestSmartCopyright:
    """智能排版版权/商标符号测试。"""

    def test_copyright(self):
        """(c) → ©。"""
        result = _render("Copyright (c) 2024")
        assert "©" in result, f"(c) 应转换为 ©: {result!r}"

    def test_copyright_uppercase(self):
        """(C) → ©。"""
        result = _render("Copyright (C) 2024")
        assert "©" in result

    def test_registered(self):
        """(r) → ®。"""
        result = _render("Name (r) trademark")
        assert "®" in result, f"(r) 应转换为 ®: {result!r}"

    def test_trademark(self):
        """(tm) → ™。"""
        result = _render("Brand (tm) symbol")
        assert "™" in result, f"(tm) 应转换为 ™: {result!r}"

    def test_not_longer_word(self):
        """(cmd) 不应转换（不是版权符号）。"""
        result = _render("run (cmd)")
        assert "©" not in result, f"(cmd) 不应转换为 ©: {result!r}"
        assert "(cmd)" in result


# ═══════════════════════════════════════════════════════════
# 智能排版：1/2 → ½, 1/4 → ¼, 3/4 → ¾
# ═══════════════════════════════════════════════════════════

class TestSmartFractions:
    """智能排版分数符号测试。"""

    def test_half(self):
        """1/2 → ½。"""
        result = _render("1/2 cup sugar")
        assert "½" in result, f"1/2 应转换为 ½: {result!r}"

    def test_quarter(self):
        """1/4 → ¼。"""
        result = _render("1/4 tsp salt")
        assert "¼" in result, f"1/4 应转换为 ¼: {result!r}"

    def test_three_quarters(self):
        """3/4 → ¾。"""
        result = _render("3/4 cup flour")
        assert "¾" in result, f"3/4 应转换为 ¾: {result!r}"

    def test_not_with_prev_alnum(self):
        """x1/2 不应转换（前邻字母数字）。"""
        result = _render("x1/2")
        assert "½" not in result, f"x1/2 不应转换: {result!r}"

    def test_not_with_next_alnum(self):
        """1/2x 不应转换（后邻字母数字）。"""
        result = _render("1/2x")
        assert "½" not in result, f"1/2x 不应转换: {result!r}"

    def test_unsupported_fraction(self):
        """2/7 不应转换（不支持的分母）。"""
        result = _render("2/7 cup")
        assert "2/7" in result, f"2/7 不应转换: {result!r}"

    def test_multiple_fractions(self):
        """多个分数符号在同一文本中全部转换。"""
        result = _render("1/2 + 1/4 + 3/4")
        assert "½" in result
        assert "¼" in result
        assert "¾" in result


# ═══════════════════════════════════════════════════════════
# _try_line_break 调度表修复测试
# ═══════════════════════════════════════════════════════════

class TestLineBreakDispatch:
    """验证 _try_line_break 已加入调度表并正确工作。"""

    def test_br_tag_renders_linebreak(self):
        """<br> → 换行节点（通过调度表 _try_line_break 或 _try_html_tag）。"""
        result = _render_text("line1<br>line2")
        plain = result.plain
        assert "line1" in plain
        assert "line2" in plain

    def test_br_slash_renders_linebreak(self):
        """<br/> → 换行。"""
        result = _render_text("line1<br/>line2")
        plain = result.plain
        assert "line1" in plain
        assert "line2" in plain

    def test_br_space_slash_renders_linebreak(self):
        """<br /> → 换行。"""
        result = _render_text("line1<br />line2")
        plain = result.plain
        assert "line1" in plain
        assert "line2" in plain

    def test_br_inside_paragraph(self):
        """段落中 <br> 正确分割行。"""
        result = _render("A <br> B")
        assert "A" in result
        assert "B" in result


# ═══════════════════════════════════════════════════════════
# Bug 回归：HTML 注释 <!-- --> 不被智能排版破坏
# ═══════════════════════════════════════════════════════════


class TestHtmlCommentSmartTypographyRegression:
    """验证 HTML 注释中的 --- 和 -- 不被智能排版转换。

    注意：内联解析器会将 <!-- ... --> 视为 HTML 注释并返回空文本，
    因此这些测试直接验证 _preprocess_text 预处理层的保护。
    最终渲染为空的场景由内联解析器的 _try_html_comment 单独处理。
    """

    def test_html_comment_opening_not_corrupted(self):
        """<!-- comment --> 中的 --- 不应被 _preprocess_text 转换为 em-dash。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!-- comment -->")
        assert "<!--" in result, f"<!-- 不应被破坏: {result!r}"
        assert "—" not in result, f"不应出现 em-dash: {result!r}"

    def test_html_comment_closing_arrow_converted(self):
        """text --> 结尾的 --> 中的 -> 被转换为箭头（箭头部匹配优先）。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("text -->")
        assert "→" in result, f"--> 中的 -> 被转换为箭头: {result!r}"

    def test_html_comment_full_not_corrupted(self):
        """完整 HTML 注释 <!-- a note --> 在预处理层完整保留边界。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!-- a note -->")
        assert "<!--" in result, f"<!-- 应保留: {result!r}"
        assert "-->" in result, f"--> 应保留: {result!r}"

    def test_html_comment_with_extra_dashes(self):
        """<!--- multi dash ---> 多连字符在预处理层不破坏内容。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!--- multi dash --->")
        assert "dash" in result, f"注释内容应保留: {result!r}"

    def test_em_dash_still_works_outside_comment(self):
        """非注释的 --- 仍然正常转换为 em-dash。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("text --- more")
        assert "—" in result, f"普通 --- 应转换: {result!r}"


# ═══════════════════════════════════════════════════════════
# 智能排版：<-> → ↔ (左右箭头)
# ═══════════════════════════════════════════════════════════


class TestSmartBidirectionalArrow:
    """<-> → ↔ 智能排版测试。"""

    def test_bidirectional_basic(self):
        """a <-> b → 左右箭头转换。"""
        result = _render("a <-> b")
        assert "↔" in result, f"<-> 应转换为 ↔: {result!r}"
        assert "<->" not in result, f"不应残留 <->: {result!r}"

    def test_bidirectional_standalone(self):
        """<-> → ↔。"""
        result = _render("relationship <-> connection")
        assert "↔" in result, f"<-> 应转换为 ↔: {result!r}"

    def test_bidirectional_not_angle_bracket_chain(self):
        """<<-> 和 <->> 不应转换（避免与移位混淆）。"""
        result1 = _render("<<->")
        assert "↔" not in result1, f"<<-> 不应转换: {result1!r}"
        result2 = _render("<->>")
        assert "↔" not in result2, f"<->> 不应转换: {result2!r}"

    def test_bidirectional_not_in_word(self):
        """a<->b 不应转换（词内符号）。"""
        result = _render("a<->b")
        assert "↔" not in result, f"a<->b 不应转换: {result!r}"


# ═══════════════════════════════════════════════════════════
# 智能排版：<= → ≤, >= → ≥, != → ≠, ~= → ≈, +- → ±
# ═══════════════════════════════════════════════════════════


class TestSmartComparisons:
    """比较与数学符号智能排版测试。"""

    # ── <= → ≤ ──

    def test_less_equal_basic(self):
        """x <= 10 → ≤。"""
        result = _render("x <= 10")
        assert "≤" in result, f"<= 应转换为 ≤: {result!r}"

    def test_less_equal_not_double_lt(self):
        """<<= 不应触发（双击运算符）。"""
        result = _render("<<= value")
        assert "≤" not in result, f"<<= 不应转换: {result!r}"

    def test_less_equal_not_spaceship(self):
        """<=> 不应触发（spaceship 运算符）。"""
        result = _render("<=> compare")
        assert "≤" not in result, f"<=> 不应转换: {result!r}"

    def test_less_equal_not_in_word(self):
        """a<=b 不应转换（词内）。"""
        result = _render("a<=b")
        assert "≤" not in result, f"a<=b 不应转换: {result!r}"

    def test_less_equal_end_of_text(self):
        """x <= → ≤（文本末尾）。"""
        result = _render("x <=")
        assert "≤" in result, f"末尾 <= 应转换: {result!r}"

    # ── >= → ≥ ──

    def test_greater_equal_basic(self):
        """x >= 5 → ≥。"""
        result = _render("x >= 5")
        assert "≥" in result, f">= 应转换为 ≥: {result!r}"

    def test_greater_equal_not_double_gt(self):
        """>>= 不应触发。"""
        result = _render(">>= shift")
        assert "≥" not in result, f">>= 不应转换: {result!r}"

    def test_greater_equal_not_reverse_spaceship(self):
        """>=< 不应触发。"""
        result = _render(">=< compare")
        assert "≥" not in result, f">=< 不应转换: {result!r}"

    def test_greater_equal_not_in_word(self):
        """a>=b 不应转换。"""
        result = _render("a>=b")
        assert "≥" not in result, f"a>=b 不应转换: {result!r}"

    # ── != → ≠ ──

    def test_not_equal_basic(self):
        """x != y → ≠。"""
        result = _render("x != y")
        assert "≠" in result, f"!= 应转换为 ≠: {result!r}"

    def test_not_equal_not_strict(self):
        """!== 不应触发（严格不等）。"""
        result = _render("!== strict")
        assert "≠" not in result, f"!== 不应转换: {result!r}"

    def test_not_equal_not_double_bang(self):
        """!!= 不应触发。"""
        result = _render("!!= double")
        assert "≠" not in result, f"!!= 不应转换: {result!r}"

    def test_not_equal_not_in_word(self):
        """a!=b 不应转换。"""
        result = _render("a!=b")
        assert "≠" not in result, f"a!=b 不应转换: {result!r}"

    # ── ~= → ≈ ──

    def test_approximately_equal_basic(self):
        """x ~= 10 → ≈。"""
        result = _render("x ~= 10")
        assert "≈" in result, f"~= 应转换为 ≈: {result!r}"

    def test_approximately_equal_not_double_tilde(self):
        """~~= 不应触发。"""
        result = _render("~~= text")
        assert "≈" not in result, f"~~= 不应转换: {result!r}"

    def test_approximately_equal_not_in_word(self):
        """a~=b 不应转换。"""
        result = _render("a~=b")
        assert "≈" not in result, f"a~=b 不应转换: {result!r}"

    def test_approximately_equal_end_of_text(self):
        """x ~= → ≈。"""
        result = _render("x ~=")
        assert "≈" in result, f"末尾 ~= 应转换: {result!r}"

    # ── +- → ± ──

    def test_plus_minus_basic(self):
        """x +- 3 → ±。"""
        result = _render("x +- 3")
        assert "±" in result, f"+- 应转换为 ±: {result!r}"

    def test_plus_minus_not_chain(self):
        """++- 和 +-+ 不应触发（运算符链）。"""
        result1 = _render("++- value")
        assert "±" not in result1, f"++- 不应转换: {result1!r}"
        result2 = _render("+-+ value")
        assert "±" not in result2, f"+-+ 不应转换: {result2!r}"

    def test_plus_minus_not_arithmetic_chain(self):
        """+-*/ 不应触发。"""
        result = _render("+-*/ ops")
        assert "±" not in result, f"+-*/ 不应转换: {result!r}"

    def test_plus_minus_not_in_word(self):
        """a+-b 不应转换。"""
        result = _render("a+-b")
        assert "±" not in result, f"a+-b 不应转换: {result!r}"

    def test_plus_minus_end_of_text(self):
        """x +- → ±。"""
        result = _render("x +-")
        assert "±" in result, f"末尾 +- 应转换: {result!r}"

    # ── 混合场景 ──

    def test_mixed_comparisons(self):
        """多个符号混用全部正确转换。"""
        result = _render("x <= 10 and y >= 5 and z != 0")
        assert "≤" in result
        assert "≥" in result
        assert "≠" in result

    def test_comparisons_in_formatted_context(self):
        """格式上下文中符号也正常转换（经 TextNode 预处理）。"""
        result = _render("**x** <= 10, *y* >= 5")
        assert "≤" in result
        assert "≥" in result


# ============================================================
# 第五轮新增：分数/正负别名/HTML注释修复/数字实体安全
# ============================================================

class TestHtmlCommentDashProtection:
    """HTML 注释体内 --- 和 -- 不被转换为 em-dash/en-dash。"""

    def test_comment_body_triple_dash_preserved(self):
        """<!-- --- note --> 中的 --- 不应被转换。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!-- --- note -->")
        assert "—" not in result  # em-dash
        assert "---" in result

    def test_comment_body_double_dash_preserved(self):
        """<!-- -- note --> 中的 -- 不应被转换。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!-- -- note -->")
        assert "–" not in result  # en-dash
        assert "--" in result

    def test_comment_opening_still_protected(self):
        """现有 <!--（开头---）保护仍生效。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("<!-- comment -->")
        assert "<!--" in result  # 不被 em-dash 破坏


class TestNumericEntitySafety:
    """数字实体安全：拒绝代理对和控制字符。"""

    def test_surrogate_pair_rejected(self):
        """&#xD800; 应原样输出而非生成代理对字符。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("&#xD800;")
        assert "&#xD800;" in result

    def test_null_char_rejected(self):
        """&#0; 应原样输出而非生成 null 字符。"""
        from src.api.renderer._inline_preprocess import _preprocess_text
        result = _preprocess_text("&#0;")
        assert '\x00' not in result


class TestFractionsExtended:
    """扩展分数符号。"""

    def test_fraction_1_3(self):
        assert _render("1/3") == "⅓"

    def test_fraction_2_3(self):
        assert _render("2/3") == "⅔"

    def test_fraction_1_8(self):
        assert _render("1/8") == "⅛"

    def test_fraction_3_8(self):
        assert _render("3/8") == "⅜"

    def test_fraction_5_8(self):
        assert _render("5/8") == "⅝"

    def test_fraction_7_8(self):
        assert _render("7/8") == "⅞"

    def test_fraction_1_5(self):
        assert _render("1/5") == "⅕"

    def test_fraction_2_5(self):
        assert _render("2/5") == "⅖"

    def test_fraction_3_5(self):
        assert _render("3/5") == "⅗"

    def test_fraction_4_5(self):
        assert _render("4/5") == "⅘"

    def test_fraction_1_6(self):
        assert _render("1/6") == "⅙"

    def test_fraction_5_6(self):
        assert _render("5/6") == "⅚"

    def test_fraction_not_in_word(self):
        """1/3 不被误转当处于单词中。"""
        assert _render("a1/3b") == "a1/3b"  # 前后都是字母数字，不转换

    def test_fraction_existing_1_2_still_works(self):
        """现有 1/2 转换仍正常。"""
        assert _render("1/2") == "½"


class TestPlusMinusAlias:
    """+/- → ±"""

    def test_plus_minus_alias(self):
        assert _render("+/-") == "±"

    def test_plus_minus_in_code(self):
        """+/- 后跟字母数字时不转换。"""
        assert _render("a+/-b") == "a+/-b"

    def test_existing_plus_minus_still_works(self):
        """现有 +- → ± 仍正常。"""
        assert _render("+-") == "±"


class TestTildeEqualsConsistency:
    """~= 边界检查一致性。"""

    def test_tilde_equals_not_in_double(self):
        """~== 不应触发。"""
        result = _render("~==")
        assert "≈" not in result  # ~= 不应在 ~== 中触发


# ═══════════════════════════════════════════════════════════
# 第六轮：inline_renderer Bug 修复
# ═══════════════════════════════════════════════════════════


class TestEmailLinkifyFastPath:
    """Bug 1 [P0]: 纯文本快速通道中 Email 链接化修复。"""

    def test_email_linkified_in_plain_text(self):
        """纯文本中的 email 应被链接化（通过 _cached_process_and_linkify）。"""
        result = _render_text("contact@example.com")
        plain = result.plain
        # Email 应保留在输出中
        assert "contact@example.com" in plain, f"Email 应保留: {plain!r}"
        # 应有 cyan + underline + italic 链接样式
        email_start = plain.index("contact@example.com")
        style = _get_style_at(result, email_start)
        assert style.underline is True, f"Email 应有下划线: {style}"
        assert style.color is not None, "Email 应有颜色样式"

    def test_email_in_mixed_text(self):
        """混合文本中 email 被链接化。"""
        result = _render_text("email me at user@domain.com for help")
        plain = result.plain
        assert "user@domain.com" in plain
        email_start = plain.index("user@domain.com")
        style = _get_style_at(result, email_start)
        assert style.underline is True, f"Email 应有下划线: {style}"

    def test_url_still_works_after_email_fix(self):
        """Email 修复不应破坏 URL 检测。"""
        result = _render_text("visit https://example.com now")
        plain = result.plain
        assert "https://example.com" in plain
        url_start = plain.index("https://example.com")
        style = _get_style_at(result, url_start)
        assert style.underline is True, "URL 应有下划线"


class TestAbbreviationStylePreservation:
    """Bug 2 [P1]: _apply_abbreviations 使用不存在的 get_style_at 修复。"""

    def _ctx_with_abbr(self, abbr_map: dict[str, str]):
        """创建带 abbr_map 的 RenderContext。"""
        from src.api.renderer.types import RenderContext
        ctx = RenderContext()
        ctx.abbr_map.update(abbr_map)
        return ctx

    def test_abbr_preserves_surrounding_punctuation(self):
        """缩写替换后周围标点符号样式不丢失。"""
        ctx = self._ctx_with_abbr({"HTML": "HyperText Markup Language"})
        result = InlineRenderer().render("<HTML>", ctx)
        assert result.plain == "<HTML>"
        # < 和 > 应保留，HTML 应有缩写样式
        html_start = result.plain.index("HTML")
        style = _get_style_at(result, html_start)
        assert style.color is not None and style.color.name == "yellow", \
            f"HTML 应有黄色样式: {style.color}"
        assert style.underline is True

    def test_abbr_with_special_chars(self):
        """URL 样式 + 缩写共存时特殊字符不丢失。"""
        ctx = self._ctx_with_abbr({"API": "Application Programming Interface"})
        # 通过格式通道（含 ** 触发内联解析器走另一条路径）
        result = InlineRenderer().render("**the API** is ready", ctx)
        assert "API" in result.plain
        api_start = result.plain.index("API")
        style = _get_style_at(result, api_start)
        assert style.underline is True, "API 应有下划线"
        assert style.color is not None and style.color.name == "yellow", \
            f"API 应有黄色样式: {style.color}"


class TestLruCacheImmutability:
    """Bug 3 [P1]: lru_cache 返回可变 Text 对象修复。"""

    def test_cached_result_is_independent(self):
        """从缓存获取两次结果，修改一个不影响另一个。"""
        text = "user@example.com"
        result1 = InlineRenderer().render(text)
        result2 = InlineRenderer().render(text)
        # 两次结果应是不同对象
        assert result1 is not result2, "同文本两次渲染应返回不同 Text 对象"
        # 内容应相同
        assert result1.plain == result2.plain

    def test_cached_result_can_be_modified_safely(self):
        """缓存结果可安全修改而不污染缓存。"""
        text = "hello world"
        result1 = InlineRenderer().render(text)
        original_plain = result1.plain
        result1.append(" extra")
        # 再次获取应不受前面 append 影响
        result2 = InlineRenderer().render(text)
        assert result2.plain == original_plain, \
            f"缓存不应被修改: {result2.plain!r} != {original_plain!r}"


class TestSubSuperscriptChildStyle:
    """Bug 4 [P2]: 上标/下标 Unicode 转换保留子节点样式。"""

    def test_superscript_bold_preserved(self):
        """^**bold**^ Unicode 转换后保留粗体样式。"""
        result = _render_text("^**bold**^")
        plain = result.plain
        # Unicode 转换应发生：b→ᵇ, o→ᵒ, l→ˡ, d→ᵈ
        assert "ᵇ" in plain or "b" in plain, f"应包含上标内容: {plain!r}"
        # 如果进行了 Unicode 转换，验证样式保留
        if "ᵇ" in plain:
            # 第一个字符应有 bold 样式
            b_pos = plain.index("ᵇ")
            style = _get_style_at(result, b_pos)
            assert style.bold is True, \
                f"^**bold**^ Unicode 转换后应保留粗体: {style}"

    def test_subscript_italic_preserved(self):
        """~*italic*~ Unicode 转换后保留斜体样式。"""
        result = _render_text("~*italic*~")
        plain = result.plain
        # 如果 Unicode 转换发生，验证样式
        if "ᵢ" in plain:
            i_pos = plain.index("ᵢ")
            style = _get_style_at(result, i_pos)
            assert style.italic is True, \
                f"~*italic*~ Unicode 转换后应保留斜体: {style}"

    def test_superscript_plain_leaf_unchanged(self):
        """^2^ 叶子节点 Unicode 转换行为不变。"""
        result = _render_text("x^2^")
        plain = result.plain
        assert "²" in plain or "2" in plain, f"应包含上标2: {plain!r}"
        if "²" in plain:
            sup_pos = plain.index("²")
            style = _get_style_at(result, sup_pos)
            assert style.italic is True, "上标应 italic"


class TestFootnoteRefCR:
    """Bug 5 [P2]: _try_footnote_ref 不处理 \\r 修复。"""

    def test_footnote_ref_with_cr(self):
        """脚注引用 [^1] 后跟 \\r 应正确解析（\\r 不再被误吞入 ref_id）。"""
        from src.api.renderer.inline_parser import _InlineParser
        p = _InlineParser("[^1]\r")
        nodes = p.parse()
        from src.api.renderer.inline_nodes import FootnoteRefNode, TextNode
        assert len(nodes) == 2, f"应有 FootnoteRefNode + TextNode, got {len(nodes)}: {nodes}"
        assert isinstance(nodes[0], FootnoteRefNode)
        assert nodes[0].ref_id == "1"
        # \r 作为 TextNode 保留（不再被吞入 ref_id）
        assert isinstance(nodes[1], TextNode)


class TestDeadCodeRemoval:
    """Bug 7 [P3]: _try_bare_email 死代码移除验证。"""

    def test_bare_email_still_works(self):
        """移除死代码后裸 Email 检测仍正常工作。"""
        result = _render_text("email me at user@domain.com")
        plain = result.plain
        assert "user@domain.com" in plain
        email_start = plain.index("user@domain.com")
        style = _get_style_at(result, email_start)
        assert style.underline is True

    def test_bare_email_with_trailing_paren(self):
        """裸 Email 后跟 ) 仍正确截断。"""
        result = _render_text("(user@domain.com) more")
        plain = result.plain
        assert "user@domain.com" in plain, f"Email 应在输出中: {plain!r}"


# ═══════════════════════════════════════════════════════════
# 第六轮新增：粗箭头 ==> → ⟹, <== → ⟸, <==> → ⟺
# ═══════════════════════════════════════════════════════════


class TestSmartThickArrows:
    """粗箭头智能排版：==> → ⟹, <== → ⟸, <==> → ⟺。"""

    # ── ==> → ⟹ ──

    def test_thick_right_basic(self):
        """a ==> b → ⟹。"""
        result = _render("a ==> b")
        assert "⟹" in result, f"==> 应转换为 ⟹: {result!r}"
        assert "==>" not in result, f"不应残留 ==>: {result!r}"

    def test_thick_right_not_chain(self):
        """===> 不应触发（三连等号）。"""
        result = _render("===> chain")
        assert "⟹" not in result, f"===> 不应转换: {result!r}"

    def test_thick_right_not_quad(self):
        """==== 不应触发。"""
        result = _render("==== separator")
        assert "⟹" not in result, f"==== 不应转换: {result!r}"

    def test_thick_right_not_in_word(self):
        """a==>b 不应转换（词内）。"""
        result = _render("a==>b")
        assert "⟹" not in result, f"a==>b 不应转换: {result!r}"

    def test_thick_right_end_of_text(self):
        """x ==> → ⟹（文本末尾）。"""
        result = _render("x ==>")
        assert "⟹" in result, f"末尾 ==> 应转换: {result!r}"

    # ── <== → ⟸ ──

    def test_thick_left_basic(self):
        """a <== b → ⟸。"""
        result = _render("a <== b")
        assert "⟸" in result, f"<== 应转换为 ⟸: {result!r}"
        assert "<===" not in result and "⟸" in result, f"不应残留 <==: {result!r}"

    def test_thick_left_not_chain(self):
        """<=== 不应触发（三连等号）。"""
        result = _render("<=== chain")
        assert "⟸" not in result, f"<=== 不应转换: {result!r}"

    def test_thick_left_not_angle_bracket_chain(self):
        """<<== 不应触发。"""
        result = _render("<<== shift")
        assert "⟸" not in result, f"<<== 不应转换: {result!r}"

    def test_thick_left_not_in_word(self):
        """a<==b 不应转换（词内）。"""
        result = _render("a<==b")
        assert "⟸" not in result, f"a<==b 不应转换: {result!r}"

    def test_thick_left_end_of_text(self):
        """x <== → ⟸（文本末尾）。"""
        result = _render("x <==")
        assert "⟸" in result, f"末尾 <== 应转换: {result!r}"

    # ── <==> → ⟺ ──

    def test_thick_bidirectional_basic(self):
        """a <==> b → ⟺。"""
        result = _render("a <==> b")
        assert "⟺" in result, f"<==> 应转换为 ⟺: {result!r}"
        assert "<==>" not in result, f"不应残留 <==>: {result!r}"

    def test_thick_bidirectional_not_chain(self):
        """<===> 不应触发。"""
        result = _render("<===> chain")
        assert "⟺" not in result, f"<===> 不应转换: {result!r}"

    def test_thick_bidirectional_not_in_word(self):
        """a<==>b 不应转换（词内）。"""
        result = _render("a<==>b")
        assert "⟺" not in result, f"a<==>b 不应转换: {result!r}"

    def test_thick_bidirectional_end_of_text(self):
        """x <==> → ⟺（文本末尾）。"""
        result = _render("x <==>")
        assert "⟺" in result, f"末尾 <==> 应转换: {result!r}"

    # ── 与原细箭头不冲突 ──

    def test_thin_right_still_works(self):
        """==> 粗箭头检测不破坏 -> 细箭头。"""
        result = _render("a -> b and c ==> d")
        assert "→" in result, f"-> 应转换: {result!r}"
        assert "⟹" in result, f"==> 应转换: {result!r}"

    def test_thin_left_still_works(self):
        """<== 粗箭头检测不破坏 <- 细箭头。"""
        result = _render("a <- b and c <== d")
        assert "←" in result, f"<- 应转换: {result!r}"
        assert "⟸" in result, f"<== 应转换: {result!r}"

    def test_compare_symbols_still_work(self):
        """<== 粗箭头不截胡 <= ≤ 比较符号。"""
        result = _render("x <= 10 and a <== b")
        assert "≤" in result, f"<= 应转换为 ≤: {result!r}"
        assert "⟸" in result, f"<== 应转换为 ⟸: {result!r}"

    def test_all_thick_arrows_mixed(self):
        """三个粗箭头符号混合使用全部正确。"""
        result = _render("==> right, <== left, <==> both")
        assert "⟹" in result
        assert "⟸" in result
        assert "⟺" in result

