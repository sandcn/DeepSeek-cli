"""ANSI 流式 Markdown 语法/渲染修复（第十四批）测试。

本轮改动：

  - **列表跨空行层级**：空行不再清空列表缩进栈，改由下一个非空行延迟判定
    （``_maybe_end_list_context``）——修复 ``- a\\n\\n  - b`` 渲染为顶层
    ``• b``、``- a\\n\\n    - b`` 退化为缩进代码块、列表项内多段后子列表
    层级丢失；同时列表项内 4 空格以上的缩进代码块仍按代码块渲染。
  - **Obsidian 折叠告示**：``> [!WARNING]- 标题`` / ``> [!TYPE]+ 标题``
    解析折叠标记（▸ / ▾），并统一复用 ``_ADMONITION_TYPES``（EXAMPLE /
    QUOTE / TODO 等类型此前在引用式告示中被当作普通文本）。
  - **代码块高亮行**：``hl_lines=[1, 3-5]``（Pandoc 列表写法）此前解析为空，
    高亮不生效；现支持方括号包裹与内部空格。
  - **告示标题行内格式**：``> [!NOTE] **重点**`` 的标题经 ``render_inline``
    解析（不再泄漏字面 ``**``）。
  - **性能**：段落预览源行按内容缓存（空转帧免重复 split）；
    ``LinePreviewCache`` 对同一源行列表做 identity 快路径（免前缀比较与复制）。
"""

from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi._preview_cache import LinePreviewCache
from src.renderer.ansi.helpers import AnsiLine
from src.renderer._utils import parse_highlight_lines


def _render(src: str, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


def _chunked(src: str, size: int = 3, width: int = 72) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    for i in range(0, len(src), size):
        r.write(src[i:i + size])
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# 列表跨空行层级
# ══════════════════════════════════════════════════════════

def test_nested_list_after_blank_line_keeps_depth():
    assert _render("- a\n\n  - b\n") == ["• a", "  ◦ b"]


def test_nested_list_four_spaces_after_blank_not_code_block():
    assert _render("- a\n\n    - b\n") == ["• a", "  ◦ b"]


def test_nested_list_after_continuation_paragraph():
    assert _render("- a\n\n  text\n\n  - b\n") == ["• a", "  text", "  ◦ b"]


def test_deep_nested_list_after_blank_line():
    lines = _render("- a\n\n  - b\n    - c\n")
    assert lines == ["• a", "  ◦ b", "    ▪ c"]


def test_list_context_ends_at_top_level_paragraph():
    lines = _render("- a\n\nb\n\n  - c\n")
    assert lines[0] == "• a"
    assert "b" in lines
    assert lines[-1] == "• c"          # 顶层列表（不再继承已结束的列表层级）
    assert "  ◦ c" not in lines


def test_nested_ordered_list_after_blank_line():
    lines = _render("- a\n\n  1) b\n")
    assert lines[1].startswith("  ")
    assert "1." in lines[1]


def test_lazy_continuation_still_belongs_to_list_item():
    assert _render("- a\nb\n") == ["• a", "  b"]


def test_indented_code_after_list_after_blank():
    # 项目既有契约：列表项后 4+ 空格的普通文本按顶层缩进代码块渲染（内容
    # 剥离量按列表内容列计算），本批次的列表状态跨空行保留不得破坏该行为。
    lines = _render("- a\n\n      code\n")
    assert any("code" in ln for ln in lines)
    assert any(ln.strip() == "```" for ln in lines)


def test_chunked_nested_list_matches_whole():
    md = "- a\n\n  - b\n\n    - c\n\n- d\n"
    assert _chunked(md, 1) == _render(md)


# ══════════════════════════════════════════════════════════
# Obsidian 折叠告示
# ══════════════════════════════════════════════════════════

def test_admonition_collapsed_marker():
    lines = _render("> [!WARNING]- fold\n> body\n")
    assert lines[0] == "▸ WARNING fold"
    assert "fold" in lines[0]


def test_admonition_expanded_marker():
    lines = _render("> [!NOTE]+ expanded\n> body\n")
    assert lines[0] == "▾ NOTE expanded"


def test_admonition_extended_type_and_inline_title():
    lines = _render("> [!EXAMPLE] **重点** 标题\n> body\n")
    assert lines[0] == "■ EXAMPLE 重点 标题"


def test_admonition_fold_marker_not_in_body():
    lines = _render("> [!TIP]- 标题\n> 正文\n")
    assert all("- 标题" not in ln for ln in lines[1:])


def test_admonition_fold_stream_preview():
    r = AnsiStreamRenderer(width=72)
    r.write("> [!WARNING]- 标题\n")
    preview = [ln.plain for ln in r.take_preview_lines()]
    assert preview == ["▸ WARNING 标题"]


# ══════════════════════════════════════════════════════════
# 代码块 hl_lines 列表写法
# ══════════════════════════════════════════════════════════

def test_parse_highlight_lines_list_syntax():
    assert parse_highlight_lines("{hl_lines=[2]}") == [2]
    assert parse_highlight_lines("{hl_lines=[1, 3-4]}") == [1, 3, 4]


def test_code_block_hl_lines_list_marks_lines():
    lines = _render("```python {hl_lines=[2]}\nx = 1\ny = 2\n```\n")
    assert any(line.startswith("▸") and "y" in line for line in lines)
    assert not any(line.startswith("▸") and "x" in line for line in lines)


def test_code_block_hl_lines_ranges():
    lines = _render("```python {hl_lines=[1, 3-4]}\na\nb\nc\nd\n```\n")
    marked = [line for line in lines if line.startswith("▸")]
    assert len(marked) == 3
    assert any("a" in line for line in marked)
    assert any("c" in line for line in marked)
    assert any("d" in line for line in marked)


# ══════════════════════════════════════════════════════════
# 性能：预览源行缓存 / 行级缓存 identity 快路径
# ══════════════════════════════════════════════════════════

def test_preview_src_lines_cached_for_equal_content():
    r = AnsiStreamRenderer(width=80)
    first = r._preview_src_lines("line 1\nline 2")
    second = r._preview_src_lines("line 1\nline 2")
    assert second is first                # 内容相同 → 复用切分结果


def test_line_preview_cache_identity_fast_path():
    cache = LinePreviewCache()
    calls: list[str] = []

    def render_line(text: str):
        calls.append(text)
        return [AnsiLine.of(text)]

    src = ["a", "b"]
    rows1 = cache.render(("para",), src, render_line)
    rows2 = cache.render(("para",), src, render_line)
    assert rows1 is rows2
    assert calls == ["a", "b"]            # 未重复渲染
