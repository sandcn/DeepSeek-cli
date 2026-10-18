"""ANSI 流式 Markdown 收尾完善（第十六批）测试。

本轮改动：

  - **块级脚注**：``[^1]:`` 后缩进的列表 / 代码 / 引用 / 表格按完整
    Markdown 渲染。
  - **定义列表块级续段**：``Term\\n: def\\n\\n    - a`` 的续段按块解析并缩进
    4 列。
  - **字形簇（grapheme cluster）宽度/换行/截断**：ZWJ 组合 emoji、肤色修饰、
    keycap、国旗不再被拆散或高估宽度。
  - **Pandoc line block**：``| 行内容`` 连续行按行块渲染（保留逐行语义），
    含内部 pipe 的表格候选文本不受影响。
  - ``has_block_markers`` 收敛为两条渲染路径共用实现。
"""

from __future__ import annotations

import pytest

from src._text_width import (
    cluster_width,
    grapheme_clusters,
    string_width,
)
from src.renderer._block_helpers import has_block_markers
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.helpers import (
    AnsiLine,
    Run,
    truncate_line,
    wrap_line,
)


FAMILY = "👨\u200d👩\u200d👧\u200d👦"
KEYCAP = "1\ufe0f\u20e3"
SKIN = "👍🏽"
FLAG = "🇨🇳"


def _ansi(src: str, width: int = 60) -> list[str]:
    r = AnsiStreamRenderer(width=width)
    r.write(src)
    r.close()
    return [ln.plain for ln in r.take_lines()]


# ══════════════════════════════════════════════════════════
# 字形簇宽度 / 换行 / 截断
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,width", [
    (FAMILY, 2),
    (FAMILY * 3, 6),
    (KEYCAP, 2),
    (SKIN, 2),
    (FLAG, 2),
    ("a\u0301", 1),
    ("中文", 4),
    ("hello", 5),
])
def test_grapheme_width(text, width):
    assert string_width(text) == width


def test_grapheme_clusters_keep_zwj_sequence():
    assert list(grapheme_clusters(FAMILY)) == [FAMILY]
    assert list(grapheme_clusters("a\u0301b")) == ["a\u0301", "b"]


def test_cluster_width_variants():
    assert cluster_width(FAMILY) == 2
    assert cluster_width(KEYCAP) == 2
    assert cluster_width(SKIN) == 2
    assert cluster_width(FLAG) == 2


def test_wrap_line_does_not_split_zwj_emoji():
    line = AnsiLine([Run(FAMILY * 4)])
    parts = wrap_line(line, 5)
    assert all(FAMILY in p.plain for p in parts)
    assert "".join(p.plain for p in parts) == FAMILY * 4
    assert all(p.width <= 5 for p in parts)


def test_wrap_line_flag_and_keycap_not_split():
    line = AnsiLine([Run(FLAG * 3 + KEYCAP * 2)])
    parts = wrap_line(line, 4)
    assert all(FLAG in p.plain or KEYCAP in p.plain for p in parts)
    assert "".join(p.plain for p in parts) == FLAG * 3 + KEYCAP * 2


def test_truncate_line_keeps_cluster_intact():
    line = AnsiLine([Run(FAMILY * 3)])
    out = truncate_line(line, 5)
    assert out.plain == FAMILY * 2
    assert out.width == 4


def test_ansi_line_width_uses_cluster_width():
    line = AnsiLine([Run(FAMILY)])
    assert line.width == 2


# ══════════════════════════════════════════════════════════
# 定义列表块级续段（ANSI）
# ══════════════════════════════════════════════════════════

def test_ansi_definition_continuation_list_block():
    lines = _ansi("Term\n: def\n\n    - a\n    - b\n")
    assert "Term: def" in lines[0]
    assert any(ln.strip() == "• a" for ln in lines)
    assert any(ln.strip() == "• b" for ln in lines)


def test_ansi_definition_continuation_quote_table_hr():
    quote = _ansi("Term\n: def\n\n    > quote\n")
    assert any("│ quote" in ln for ln in quote)
    table = _ansi("Term\n: def\n\n    | a |\n    |---|\n    | 1 |\n")
    assert any("┌" in ln for ln in table)


def test_ansi_definition_continuation_plain_still_inline():
    assert _ansi("Term\n: def\n\n    para2\n")[-1].strip() == "para2"


def test_ansi_definition_continuation_indented_code_block():
    lines = _ansi("Term\n: def\n\n        code\n        two\n")
    joined = "\n".join(lines)
    assert "```" in joined
    assert "code" in joined and "two" in joined
    assert "para2" not in joined


def test_ansi_definition_continuation_stream_matches_oneshot():
    src = "Term\n: def\n\n    - a\n    - b\n"
    r = AnsiStreamRenderer(width=60)
    for i in range(0, len(src), 2):
        r.write(src[i:i + 2])
    r.close()
    assert [ln.plain for ln in r.take_lines()] == _ansi(src)


# ══════════════════════════════════════════════════════════
# Pandoc line block
# ══════════════════════════════════════════════════════════

def test_line_block_keeps_lines():
    assert _ansi("| line one\n| line two\n") == ["line one", "line two"]


def test_line_block_inline_formatting():
    assert _ansi("| **bold**\n| *it*\n") == ["bold", "it"]


def test_line_block_then_paragraph():
    assert _ansi("| a\n| b\n\nnext\n") == ["a", "b", "next"]


def test_line_block_in_blockquote():
    assert _ansi("> | a\n> | b\n") == ["│ a", "│ b"]


def test_pipe_text_with_internal_pipes_stays_paragraph():
    assert _ansi("| a | b |\n| 1 | 2 |\n") == ["| a | b |", "| 1 | 2 |"]


def test_line_block_stream_matches_oneshot():
    src = "| a\n| b\n\n| c\n"
    r = AnsiStreamRenderer(width=60)
    for i in range(0, len(src), 2):
        r.write(src[i:i + 2])
    r.close()
    assert [ln.plain for ln in r.take_lines()] == _ansi(src)


def test_line_block_preview():
    r = AnsiStreamRenderer(width=50)
    r.write("| line one\n| li")
    assert [ln.plain for ln in r.take_preview_lines()] == ["line one", "li"]
    r.write("ne two\n")
    assert [ln.plain for ln in r.take_preview_lines()] == ["line one", "line two"]
    r.close()


# ══════════════════════════════════════════════════════════
# has_block_markers 共用实现
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("line,expected", [
    ("- a", True),
    ("1. a", True),
    ("> q", True),
    ("```js", True),
    ("| a |", True),
    ("# h", True),
    ("---", True),
    ("plain", False),
    ("para | pipe", False),
])
def test_has_block_markers(line, expected):
    assert has_block_markers([line]) is expected


def test_ansi_blocks_reexports_shared_marker_detector():
    from src.renderer.ansi import blocks

    assert blocks.has_block_markers is has_block_markers
