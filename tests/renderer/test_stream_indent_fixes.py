"""TUI 流式 Markdown 渲染：行首缩进 / 前缀修复回归（P1–P7）。

覆盖本轮修复：

  - **P1 折行断点**（``wrap_line``）：行首标记/缩进里的空白不再被当作词边界
    断点——中文长行（无空格分隔）折行后首行仍有内容（修复前首行只剩 ``•`` /
    ``│``）；同时英文长行仍按词边界断行（半行约束不影响正常断词）。
  - **P2 列表缩进按层级**：``- a\\n    - b``（4 空格源缩进）不再渲染成 8 列缩进，
    每层固定 2 列（与历史实现一致）。
  - **P3 续行/嵌套块对齐内容列**：``1. `` 项续行缩进 3 列、``10. `` 为 4 列、
    任务项 ``- [x] `` 为 6 列（修复前固定 2 列）。
  - **P4 引用块内未闭合块预览的 ``│ `` 前缀**：引用内代码块 / 表格 / 公式在
    流式预览期间即带前缀（修复前显示成顶层块、闭合瞬间才补上）。
  - **P5 未闭合活动行的块级预览**：``- 项\\n  > 引用`` 预览为 ``  │ 引用``；
    嵌套子列表按层级缩进（不再显示为缩进错位的字面 ``- c``）。
  - **P6 容器正文空行不带缩进前缀**：预览空行是 ``""``（修复前是 ``"    "``）。
  - **P7 段落行首 ≤3 空格剥离**（CommonMark）：缩进段落不再整段右移。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer, wrap_line
from src.renderer.ansi.helpers import AnsiLine


def _render(src: str, width: int = 70) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _preview_frames(src: str, chunk: int = 3, width: int = 70) -> list[list[str]]:
    r = AnsiStreamRenderer(width=width)
    frames: list[list[str]] = []
    for i in range(0, len(src), chunk):
        r.write(src[i:i + chunk])
        frames.append([ln.plain for ln in r.take_preview_lines()])
    r.close()
    return frames


def _stream_matches_once(src: str, width: int = 70) -> bool:
    once = _render(src, width)
    for chunk in (1, 2, 3, 7):
        r = AnsiStreamRenderer(width=width)
        for i in range(0, len(src), chunk):
            r.write(src[i:i + chunk])
            r.take_preview_lines()
        r.close()
        if [ln.plain for ln in r.take_lines()] != once:
            return False
    return True


# ══════════════════════════════════════════════════════════
# P1 折行断点：行首标记/缩进里的空白不作断点
# ══════════════════════════════════════════════════════════

def test_wrap_list_marker_chinese_line_keeps_content():
    """列表项中文长行折行：首行含内容（不再只剩 ``•``）。"""
    line = AnsiLine.of("\u2022 " + "\u4e2d\u6587\u5185\u5bb9\u6d4b\u8bd5" * 6)
    line.cont_prefix = "  "
    parts = wrap_line(line, 40)
    assert len(parts) > 1
    assert parts[0].plain.startswith("\u2022 ")
    assert len(parts[0].plain) > 2, parts
    assert all(p.width <= 40 for p in parts), [p.width for p in parts]


def test_wrap_blockquote_chinese_line_keeps_content():
    """引用块中文长行折行：首行含内容（不再只剩 ``│``）。"""
    line = AnsiLine.of("\u2502 " + "\u4e2d\u6587\u5185\u5bb9\u6d4b\u8bd5" * 6)
    line.cont_prefix = "\u2502 "
    parts = wrap_line(line, 40)
    assert len(parts) > 1
    assert parts[0].plain.startswith("\u2502 ")
    assert len(parts[0].plain) > 2, parts


def test_wrap_indented_chinese_line_keeps_content():
    """缩进段落中文长行折行：首行不只前缀空白。"""
    line = AnsiLine.of("  " + "\u4e2d\u6587\u5185\u5bb9\u6d4b\u8bd5" * 6)
    line.cont_prefix = "  "
    parts = wrap_line(line, 40)
    assert len(parts) > 1
    assert parts[0].plain.strip("\u2022 "), parts
    assert len(parts[0].plain.strip()) > 4, parts


def test_wrap_english_word_break_still_applies():
    """英文长行仍按词边界断行（半行约束不破坏正常断词）。"""
    line = AnsiLine.of(
        "\u2022 this is a long english list item used to check wrapping")
    line.cont_prefix = "  "
    parts = wrap_line(line, 40)
    assert len(parts) > 1
    assert parts[0].plain.endswith("used"), parts
    # 续行只带悬挂缩进前缀，词本身未被拆断
    assert parts[1].plain.lstrip().startswith("to check"), parts


def test_wrap_plain_chinese_line_unchanged():
    """无前缀的顶层中文长行行为不变（行宽不超上限）。"""
    line = AnsiLine.of("\u4e2d\u6587\u5185\u5bb9\u6d4b\u8bd5" * 6)
    parts = wrap_line(line, 40)
    assert all(p.width <= 40 for p in parts), [p.width for p in parts]
    assert "\u4e2d\u6587" in parts[0].plain


# ══════════════════════════════════════════════════════════
# P2 列表缩进按层级（每层 2 列）
# ══════════════════════════════════════════════════════════

def test_nested_list_indent_per_level():
    lines = _render("- a\n  - b\n    - c\n")
    assert lines == ["\u2022 a", "  \u25e6 b", "    \u25aa c"]


def test_nested_list_indent_independent_of_source_indent():
    """4 / 8 空格源缩进的子列表同样只缩进一层（修复前 8 / 16 列）。"""
    assert _render("- a\n    - b\n")[1] == "  \u25e6 b"
    assert _render("- a\n        - b\n")[1] == "  \u25e6 b"


def test_ordered_nested_list_indent():
    lines = _render("1. \u4e00\n   1. \u4e8c\n      1. \u4e09\n")
    assert lines == ["1. \u4e00", "  1. \u4e8c", "    1. \u4e09"]


# ══════════════════════════════════════════════════════════
# P3 续行 / 列表项内块级元素对齐内容列
# ══════════════════════════════════════════════════════════

def test_ordered_list_continuation_aligns_content_col():
    assert _render("1. \u9879\n   \u7eed\u884c\n")[1] == "   \u7eed\u884c"
    assert _render("10. \u9879\n    \u7eed\u884c\n")[1] == "    \u7eed\u884c"


def test_todo_list_continuation_aligns_checkbox():
    lines = _render("- [x] \u9879\n      \u7eed\u884c\n")
    assert lines[0] == "\u2022 [x] \u9879"
    assert lines[1] == "      \u7eed\u884c"


def test_unordered_list_continuation_unchanged():
    assert _render("- \u9879\n  \u7eed\u884c\n")[1] == "  \u7eed\u884c"


def test_list_block_element_aligns_content_col():
    """列表项内块级元素（缩进段落）对齐内容列（``1. `` → 3 列）。"""
    assert _render("1. \u9879\n\n   \u6bb5\u843d\n")[1] == "   \u6bb5\u843d"


# ══════════════════════════════════════════════════════════
# P4 引用块内未闭合块预览的引用前缀
# ══════════════════════════════════════════════════════════

def _all_preview_lines(src: str) -> list[str]:
    out: list[str] = []
    for frame in _preview_frames(src):
        out.extend(frame)
    return out


def test_preview_blockquote_code_has_prefix():
    frames = _all_preview_lines("> ```py\n> a = 1\n> b = 2\n> ```\n")
    assert any(ln.startswith("\u2502 ```py") for ln in frames), frames
    assert all(not ln.startswith("```") for ln in frames), frames
    # 提交态与一次性渲染一致
    assert _stream_matches_once("> ```py\n> a = 1\n> b = 2\n> ```\n")


def test_preview_blockquote_indented_code_has_prefix():
    frames = _all_preview_lines(">     code\n")
    assert any(ln.startswith("\u2502 ```") for ln in frames), frames
    assert any(ln == "\u2502 code" for ln in frames), frames


def test_preview_blockquote_table_has_prefix():
    frames = _all_preview_lines("> | a | b |\n> |---|---|\n> | 1 | 2 |\n")
    table_rows = [ln for ln in frames if "\u250c" in ln]
    assert table_rows and all(ln.startswith("\u2502 ") for ln in table_rows), frames


def test_preview_blockquote_math_has_prefix_and_no_marker():
    """引用内公式预览带 ``│ `` 且内容不含 ``>`` 前缀残留。"""
    frames = _all_preview_lines("> $$\n> x = 1\n> $$\n")
    math_rows = [ln for ln in frames if "x = 1" in ln]
    assert math_rows, frames
    for ln in math_rows:
        assert ln.startswith("\u2502 "), frames
        assert "> x" not in ln, frames


def test_preview_nested_blockquote_code_has_prefix():
    frames = _all_preview_lines("> > ```\n> > x\n> > ```\n")
    assert any(ln.startswith("\u2502 \u2502 ```") for ln in frames), frames


# ══════════════════════════════════════════════════════════
# P5 未闭合活动行的块级预览
# ══════════════════════════════════════════════════════════

def test_preview_list_item_quote_line():
    """列表项内的引用行预览为 ``  │ 引用``（不再原样 ``  > 引用``）。"""
    frames = _all_preview_lines("- \u9879\n  > \u5f15\u7528\n")
    assert any(ln == "  \u2502 \u5f15\u7528" for ln in frames), frames
    assert all(not ln.strip().startswith("> ") for ln in frames), frames


def test_preview_nested_list_indent_matches_commit():
    """嵌套子列表未闭合时按层级缩进（不再是缩进错位的字面 ``- c``）。"""
    src = "- a\n  - b\n    - c"
    frames = _preview_frames(src)
    assert frames[-1] == ["    \u25aa c"], frames[-1]
    assert _render(src + "\n")[-1] == "    \u25aa c"


# ══════════════════════════════════════════════════════════
# P6 容器正文空行不带缩进前缀
# ══════════════════════════════════════════════════════════

def test_preview_container_blank_line_without_indent():
    src = "> [!NOTE]\n> \u4e00\n>\n> \u4e8c\n"
    frames = _preview_frames(src)
    all_rows = [ln for frame in frames for ln in frame]
    assert any(ln == "\u25a0 NOTE" for ln in all_rows), all_rows
    # 预览空行必须是真空行（不带 4 空格缩进前缀）
    blank_rows = [ln for ln in all_rows if ln and not ln.strip()]
    assert blank_rows == [], blank_rows
    assert _stream_matches_once(src)


# ══════════════════════════════════════════════════════════
# P7 段落行首 ≤3 空格剥离
# ══════════════════════════════════════════════════════════

def test_paragraph_leading_spaces_stripped():
    assert _render("   \u4e09\u7a7a\u683c\n\u4e0b\u4e00\u884c\n") == [
        "\u4e09\u7a7a\u683c", "\u4e0b\u4e00\u884c"]
    assert _render("  \u4e24\u7a7a\u683c\n") == ["\u4e24\u7a7a\u683c"]
    assert _render(" \u4e00\u7a7a\u683c\n") == ["\u4e00\u7a7a\u683c"]


def test_blockquote_paragraph_leading_spaces_stripped():
    assert _render(">   \u5f15\u7528\u5185\u4e09\u7a7a\u683c\n") == [
        "\u2502 \u5f15\u7528\u5185\u4e09\u7a7a\u683c"]


def test_paragraph_indent_code_block_unaffected():
    """4 空格缩进仍是缩进代码块（不受段落剥离影响）。"""
    lines = _render("    code\n")
    assert any(ln == "code" for ln in lines), lines
