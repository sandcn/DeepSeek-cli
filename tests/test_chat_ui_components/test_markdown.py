"""Markdown 渲染器测试 — render_markdown() 所有功能覆盖。

测试范围：
1. 标题 # ## ### → bold 样式
2. 粗体 **text** → ANSI bold
3. 斜体 *text* → ANSI italic
4. 行内代码 `code` → dim+reverse 样式
5. 无序列表 - item → "  • item"
6. 有序列表 1. item → "  1. item"
7. 块引用 > text → dim+italic
8. 水平线 --- → dim 样式
9. 围栏代码块 ```lang\ncode\n``` → 语言标签 + dim 边框
10. 混合内容
11. 空文本 / 超过 200 行截断
12. 未闭合代码块处理
"""

from __future__ import annotations

import pytest

from src.chat_ui.infrastructure.markdown_renderer import render_markdown


# ── 基础 Markdown 内联渲染 ──────────────────────────────

class TestInlineFormatting:
    """行内格式：粗体、斜体、行内代码。"""

    def test_bold_text(self):
        """**text** → ANSI bold 包裹。"""
        result = render_markdown("Hello **world**!")
        assert "world" in result
        assert "\033[1m" in result  # ANSI bold

    def test_italic_text(self):
        """*text* → ANSI italic 包裹。"""
        result = render_markdown("Hello *world*!")
        assert "world" in result
        assert "\033[3m" in result  # ANSI italic

    def test_inline_code(self):
        """`code` → dim+reverse 样式。"""
        result = render_markdown("Use `print()` function.")
        assert "print()" in result
        assert "\033[2m" in result  # dim
        assert "\033[7m" in result  # reverse

    def test_bold_and_italic_mixed(self):
        """**bold** and *italic* in same line。"""
        result = render_markdown("This is **bold** and *italic* text.")
        assert "\033[1m" in result
        assert "\033[3m" in result

    def test_no_formatting_plain_text(self):
        """无格式纯文本 → 原样返回。"""
        result = render_markdown("Plain text without formatting.")
        assert "Plain text without formatting." in result

    def test_inline_code_has_priority(self):
        """`**not bold**` — 行内代码内的 ** 不被解析为粗体。"""
        result = render_markdown("Use `**not bold**` here.")
        # 行内代码内应有 dim+reverse，不应有 bold
        assert "**not bold**" in result
        assert "\033[2m" in result  # dim for inline code


# ── 标题 ──────────────────────────────────────────────

class TestHeadings:
    """标题渲染：# ## ### → bold。"""

    def test_h1_heading(self):
        """# Heading 1 → bold + ▌ 前缀。"""
        result = render_markdown("# Main Title")
        assert "Main Title" in result
        assert "\033[1m" in result  # bold
        assert "▌" in result

    def test_h2_heading(self):
        """## Heading 2 → bold + 缩进。"""
        result = render_markdown("## Section")
        assert "Section" in result
        assert "\033[1m" in result

    def test_h3_heading(self):
        """### Heading 3 → bold + 更多缩进。"""
        result = render_markdown("### Subsection")
        assert "Subsection" in result
        assert "\033[1m" in result

    def test_heading_with_inline_code(self):
        """# `code` in heading — 标题内行内代码。"""
        result = render_markdown("# Using `foo()` function")
        assert "foo()" in result
        assert "\033[1m" in result  # bold from heading
        assert "\033[7m" in result  # reverse from inline code

    def test_not_heading_without_space(self):
        """#no-space → 不是标题，作为普通文本。"""
        result = render_markdown("#no-space")
        assert "▌" not in result  # 没有标题前缀


# ── 列表 ──────────────────────────────────────────────

class TestLists:
    """无序和有序列表渲染。"""

    def test_unordered_list_single(self):
        """- item → '  • item'。"""
        result = render_markdown("- First item")
        assert "•" in result
        assert "First item" in result

    def test_unordered_list_multiple(self):
        """多个 - item → 每行 '  • item'。"""
        result = render_markdown("- Item A\n- Item B\n- Item C")
        lines = result.split("\n")
        bullet_lines = [l for l in lines if "•" in l]
        assert len(bullet_lines) == 3

    def test_ordered_list_single(self):
        """1. item → '  1. item'。"""
        result = render_markdown("1. First step")
        assert "1." in result
        assert "First step" in result

    def test_ordered_list_multiple(self):
        """多个有序列表项。"""
        result = render_markdown("1. Step one\n2. Step two\n3. Step three")
        lines = result.split("\n")
        num_lines = [l for l in lines if "." in l and l.strip().startswith(("1", "2", "3"))]
        assert len(num_lines) == 3

    def test_list_with_bold_text(self):
        """- **bold item** → 列表项内粗体。"""
        result = render_markdown("- **Important** note")
        assert "•" in result
        assert "Important" in result
        assert "\033[1m" in result

    def test_asterisk_list(self):
        """* item（星号开头的无序列表）。"""
        result = render_markdown("* Star item")
        assert "•" in result
        assert "Star item" in result


# ── 块引用 ──────────────────────────────────────────────

class TestBlockquotes:
    """块引用 > text → dim+italic。"""

    def test_simple_blockquote(self):
        """> quoted text → dim+italic 样式。"""
        result = render_markdown("> This is a quote")
        assert "This is a quote" in result
        assert "\033[2m" in result  # dim
        assert "\033[3m" in result  # italic
        assert "│" in result  # 引用竖线

    def test_blockquote_with_bold(self):
        """> **bold** quote → 块引用内含粗体。"""
        result = render_markdown("> This is **important**")
        assert "important" in result
        assert "\033[1m" in result  # bold
        assert "\033[2m" in result  # dim from blockquote


# ── 水平线 ──────────────────────────────────────────────

class TestHorizontalRules:
    """水平线 --- → dim 样式线。"""

    def test_three_dashes(self):
        """--- → dim 水平线。"""
        result = render_markdown("---")
        assert "\033[2m" in result  # dim
        assert "─" in result  # box drawing char

    def test_three_asterisks(self):
        """*** → dim 水平线。"""
        result = render_markdown("***")
        assert "\033[2m" in result
        assert "─" in result

    def test_three_underscores(self):
        """___ → dim 水平线。"""
        result = render_markdown("___")
        assert "\033[2m" in result
        assert "─" in result


# ── 围栏代码块 ────────────────────────────────────────

class TestFencedCodeBlocks:
    """围栏代码块 ```lang\\ncode\\n``` → 语言标签 + dim 边框。"""

    def test_code_block_with_language(self):
        """```python\\ncode\\n``` → cyan 语言标签 + dim 代码。"""
        result = render_markdown("```python\nprint('hello')\n```")
        assert "python" in result
        assert "\033[36m" in result  # cyan for language
        assert "print('hello')" in result
        assert "\033[2m" in result  # dim for code

    def test_code_block_without_language(self):
        """```\\ncode\\n``` → 'code' 标签。"""
        result = render_markdown("```\nplain code\n```")
        assert "code" in result
        assert "plain code" in result
        assert "\033[2m" in result

    def test_code_block_multiline(self):
        """多行代码块。"""
        result = render_markdown("```bash\nline1\nline2\nline3\n```")
        assert "bash" in result
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_unclosed_code_block(self):
        """未闭合的代码块 → 仍然渲染。"""
        result = render_markdown("```python\nunclosed code")
        assert "python" in result
        assert "unclosed code" in result


# ── 混合内容 ──────────────────────────────────────────

class TestMixedContent:
    """多种 Markdown 元素混合。"""

    def test_heading_followed_by_paragraph(self):
        """标题后跟段落。"""
        result = render_markdown("# Title\n\nThis is a paragraph with **bold**.")
        assert "Title" in result
        assert "paragraph" in result
        assert "\033[1m" in result  # bold in paragraph

    def test_list_followed_by_code(self):
        """列表后跟代码块。"""
        result = render_markdown("- Item 1\n- Item 2\n\n```python\nprint('hello')\n```")
        assert "•" in result
        assert "python" in result
        assert "print('hello')" in result

    def test_multiple_headings_and_paragraphs(self):
        """多个标题和段落混合。"""
        result = render_markdown(
            "# H1\n\nParagraph one.\n\n## H2\n\nParagraph *two*.\n\n### H3\n\n`code` here."
        )
        assert "H1" in result
        assert "H2" in result
        assert "H3" in result
        assert "\033[3m" in result  # italic
        assert "\033[7m" in result  # reverse from inline code


# ── 边界情况 ──────────────────────────────────────────

class TestEdgeCases:
    """边界情况和错误处理。"""

    def test_empty_string(self):
        """空字符串 → 返回空字符串。"""
        assert render_markdown("") == ""

    def test_only_whitespace_lines(self):
        """仅空白行 → 空格保留。"""
        result = render_markdown("   \n   ")
        assert isinstance(result, str)

    def test_very_long_line(self):
        """超长单行 → 不崩溃。"""
        result = render_markdown("x" * 2000)
        assert len(result) > 0

    def test_truncation_at_200_lines(self):
        """超过 200 行 → 截断并添加提示。"""
        # 构造 201 行
        text = "\n".join(f"line {i}" for i in range(201))
        result = render_markdown(text)
        # 应有截断提示
        assert "截断" in result
        # 不应包含第 201 行
        assert "line 200" not in result

    def test_exactly_200_lines(self):
        """恰好 200 行 → 不截断。"""
        text = "\n".join(f"line {i}" for i in range(200))
        result = render_markdown(text)
        assert "截断" not in result
        assert "line 199" in result

    def test_bold_across_lines_not_supported(self):
        """跨行粗体 **...\\n...** 不被支持（每行独立解析）。"""
        result = render_markdown("**start\nend**")
        # 每行独立解析，不崩溃即可
        assert isinstance(result, str)

    def test_code_block_preserves_empty_lines(self):
        """代码块内的空行保留。"""
        result = render_markdown("```\nline1\n\nline3\n```")
        assert "line1" in result
        assert "line3" in result

    def test_multiple_code_blocks(self):
        """多个代码块。"""
        result = render_markdown(
            "```python\na = 1\n```\n\nSome text\n\n```bash\necho hi\n```"
        )
        assert "python" in result
        assert "bash" in result
        assert "a = 1" in result
        assert "echo hi" in result
