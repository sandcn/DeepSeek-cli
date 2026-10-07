"""流式 Markdown 渲染问题修复回归测试（性能 / 表格 / 预览一致性）。

覆盖修复清单：
  - P0-1 代码块流式预览行内增量（消除 O(n²) 整段重渲染）
  - P0-2 GFM 无前导 pipe 表格数据行（不再退化为段落）
  - P1-1 超长代码块分段提交后 committed/preview 不重复显示
  - P1-2 未换行活动行的块级语法预览（列表/标题/引用/分隔线/围栏/setext）
  - P2-1 引用块活动行剥离 ``>`` 前缀、Mermaid/数学结束定界符不入预览
  - P2-2 代码块预览缓冲行数上限
  - 额外：Mermaid 块提交后内容不丢失（``meta["source"]`` 缺失修复）
"""
from __future__ import annotations

from src.renderer.ansi import AnsiStreamRenderer


def _preview(chunks, width=80):
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    return [line.plain for line in r.take_preview_lines()]


def _render(chunks, width=80):
    r = AnsiStreamRenderer(width=width)
    for c in chunks:
        r.write(c)
    r.close()
    return [line.plain for line in r.take_lines()]


# ═══════════════════════════════════════════════════════════
# P0-1 代码块预览行内增量
# ═══════════════════════════════════════════════════════════


def test_code_preview_incremental_on_partial_line(monkeypatch):
    """行内（未换行）字符增量只重渲变化行——不再是 O(n²) 整段重渲染。"""
    from src.renderer.ansi import code as _code

    total = {"lines": 0}
    orig = _code.highlight_code_lines

    def spy(lines, lang="", theme="monokai", highlight_lines=None,
            start_index=1, **kwargs):
        total["lines"] += len(lines)
        return orig(lines, lang, theme, highlight_lines, start_index, **kwargs)

    monkeypatch.setattr(_code, "highlight_code_lines", spy)

    n = 200
    text = "".join(f"v{i} = {i}\n" for i in range(n))
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    for i in range(0, len(text), 8):
        r.write(text[i:i + 8])
        r.take_lines()
        r.take_preview_lines()
    # 修复前每帧整段重渲（约 166x 总行数）；增量后约 2x（未换行行重渲一次）
    assert total["lines"] < n * 4


# ═══════════════════════════════════════════════════════════
# P0-2 GFM 无前导 pipe 表格数据行
# ═══════════════════════════════════════════════════════════


def test_table_data_rows_without_leading_pipe():
    """表头带前导 pipe，数据行不带——数据行仍进表格（不再退化成段落）。"""
    lines = _render(["| h1 | h2 |\n", "|---|---|\n", "a | b\n", "c | d\n"])
    assert lines[0].startswith("┌")
    assert lines[-1].startswith("└")
    assert "a | b" not in lines
    assert "c | d" not in lines


def test_table_all_rows_without_leading_pipe():
    """表头与数据行都不带前导 pipe（≥1 个 pipe）时正常成表。"""
    lines = _render(["a | b\n", "--- | ---\n", "1 | 2\n"])
    assert lines[0].startswith("┌")
    assert lines[-1].startswith("└")
    assert "1 | 2" not in lines


def test_pipe_text_without_separator_stays_paragraph():
    """无分隔行的含 pipe 文本仍是段落（不误判为表格）。"""
    assert _render(["a | b\n", "c | d\n"]) == ["a | b", "c | d"]


def test_table_extra_column_terminates_table():
    """数据行列数超过表头 → 结束表格，该行作为后续段落。"""
    lines = _render(["| a | b |\n", "|---|---|\n", "| 1 | 2 | 3 |\n"])
    assert "| 1 | 2 | 3 |" in lines


# ═══════════════════════════════════════════════════════════
# P1-1 超长代码块分段提交不重复
# ═══════════════════════════════════════════════════════════


def test_oversized_code_block_no_committed_preview_overlap():
    """超过缓冲上限被分段提交后，预览与 committed 行不重叠（不重复显示）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\n")
    committed = []
    for i in range(2100):
        r.write(f"v{i} = {i}\n")
        committed.extend(line.plain for line in r.take_lines())
    preview = [line.plain for line in r.take_preview_lines()]
    assert committed, "应有分段提交"
    assert preview, "应有未提交尾部预览"
    assert set(committed) & set(preview) == set()


def test_normal_code_block_still_uses_preview():
    """未超限的代码块仍全程走预览（不因分段逻辑受扰）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("```python\nx = 1\n")
    assert [line.plain for line in r.take_preview_lines()] == ["```python [python]", "x = 1"]


# ═══════════════════════════════════════════════════════════
# P1-2 未换行活动行的块级语法预览
# ═══════════════════════════════════════════════════════════


def test_preview_unordered_list_uses_bullet():
    assert _preview(["- 项目"]) == ["• 项目"]


def test_preview_ordered_list():
    assert _preview(["1. 项目"]) == ["1. 项目"]


def test_preview_task_list():
    assert _preview(["- [ ] 待办"]) == ["• [ ] 待办"]
    assert _preview(["- [x] 完成"]) == ["• [x] 完成"]


def test_preview_heading():
    assert _preview(["# 标题"]) == ["标题"]


def test_preview_hr():
    # HR 现按终端宽度整宽渲染（width=80 → 80 列分隔线）
    assert _preview(["---"]) == ["\u2500" * 80]


def test_preview_blockquote_line():
    assert _preview(["> 引用"]) == ["│ 引用"]


def test_preview_setext_heading():
    assert _preview(["标题\n", "==="]) == ["标题"]


def test_preview_code_fence():
    assert _preview(["```python"]) == ["```python [python]"]


def test_preview_plain_text_unchanged():
    assert _preview(["普通文字"]) == ["普通文字"]


def test_list_preview_matches_commit():
    """列表预览与提交后的渲染一致（无 ``- x`` → ``• x`` 跳变）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("- 项")
    assert [line.plain for line in r.take_preview_lines()] == ["• 项"]
    r.write("目\n")
    assert [line.plain for line in r.take_lines()] == ["• 项目"]


# ═══════════════════════════════════════════════════════════
# P2-1 引用活动行 / 块结束定界符
# ═══════════════════════════════════════════════════════════


def test_blockquote_active_tail_strips_prefix():
    """引用块内未换行活动行剥离 ``>`` 前缀（不再出现 ``│ > xxx``）。"""
    r = AnsiStreamRenderer(width=80)
    r.write("> 第一行\n")
    r.write("> 第二")
    assert [line.plain for line in r.take_preview_lines()] == ["│ 第一行", "│ 第二"]


def test_code_preview_excludes_closing_fence():
    """代码块未换行的结束围栏不进入预览内容。"""
    assert _preview(["```python\nx=1\n```"]) == ["```python [python]", "x=1"]


def test_mermaid_preview_excludes_closing_fence():
    """Mermaid 块未换行的结束围栏不进入预览内容（渲染为流程图图形）。"""
    preview = _preview(["```mermaid\ngraph TD; A-->B\n```"])
    joined = "\n".join(preview)
    assert "```" not in joined
    assert "A" in joined and "B" in joined
    assert "\u25bc" in joined  # ▼ 箭头


def test_math_preview_excludes_closing_delimiter():
    """数学块未换行的 ``$$`` 结束定界符不进入预览内容（渲染为公式）。"""
    preview = _preview(["$$\nE=mc^2\n$$"])
    joined = "\n".join(preview)
    assert "$$" not in joined
    assert "E=mc\u00b2" in joined


# ═══════════════════════════════════════════════════════════
# P2-2 代码块预览缓冲上限
# ═══════════════════════════════════════════════════════════


def test_preview_code_buffer_capped():
    """代码块预览缓冲不超过上限，超出的最旧行被丢弃并计数。"""
    from src.renderer._block_parser import RegexFreeBlockParser

    limit = RegexFreeBlockParser._PREVIEW_CODE_LINES_MAX
    extra = 120
    parser = RegexFreeBlockParser()
    parser.feed("```python\n")
    for i in range(limit + extra):
        parser.feed(f"line{i}\n")
    assert len(parser._preview_code_lines) == limit
    assert parser._preview_code_dropped == extra


def test_preview_code_buffer_reset_on_new_block():
    """新代码块开始时预览缓冲与丢弃计数归零。"""
    from src.renderer._block_parser import RegexFreeBlockParser

    limit = RegexFreeBlockParser._PREVIEW_CODE_LINES_MAX
    parser = RegexFreeBlockParser()
    parser.feed("```python\n")
    for i in range(limit + 5):
        parser.feed(f"line{i}\n")
    assert parser._preview_code_dropped > 0
    parser.feed("```\n")
    parser.feed("```python\n")
    assert parser._preview_code_lines == []
    assert parser._preview_code_dropped == 0


# ═══════════════════════════════════════════════════════════
# 额外：Mermaid 提交内容不丢失
# ═══════════════════════════════════════════════════════════


def test_mermaid_commit_keeps_source():
    """Mermaid 块提交后渲染为图形（``meta["source"]`` 缺失曾导致只剩空框）。"""
    lines = _render(["```mermaid\ngraph TD; A-->B\n```\n"])
    text = "\n".join(lines)
    assert "A" in text and "B" in text
    assert "\u25bc" in text
    assert "```" not in text


def test_mermaid_commit_multiline_source():
    lines = _render(["```mermaid\ngraph TD\nA-->B\n```\n"])
    text = "\n".join(lines)
    assert "A" in text and "B" in text
    assert "\u25bc" in text
