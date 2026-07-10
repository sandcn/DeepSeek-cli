"""Tests for _rendering module."""
from rich.text import Text
from rich.style import Style

from src.renderer._rendering import (
    render_blockquote_prefix,
    _build_highlight_style,
    render_diff_line,
)


# ═══════════════════════════════════════════════════════════
# Bug 1 回归测试: render_blockquote_prefix dim 样式偏移
# ═══════════════════════════════════════════════════════════

def test_blockquote_prefix_depth_2_dim_second_bar_regression():
    """Bug 回归：depth=2 时，第一个 ▐ 不 dim，第二个 ▐ 应 dim。

    字符串: " ▐▐ "
    索引:    0123
    预期:    索引 1（第一个 ▐）正常，索引 2（第二个 ▐）dim
    """
    result = render_blockquote_prefix(2)
    spans = result.spans
    # 应恰好有一个 dim span
    dim_spans = [s for s in spans if s.style.dim]
    assert len(dim_spans) == 1, f"期望 1 个 dim span，实际 {len(dim_spans)}: {spans}"
    dim_span = dim_spans[0]
    # dim 应作用于索引 2（第二个 ▐）
    assert dim_span.start == 2, f"dim 应起始于索引 2，实际 {dim_span.start}"
    assert dim_span.end == 3, f"dim 应结束于索引 3，实际 {dim_span.end}"


def test_blockquote_prefix_depth_3_dim_second_third_bar_regression():
    """Bug 回归：depth=3 时，第一个 ▐ 不 dim，第二、三个 ▐ 应 dim。

    字符串: " ▐▐▐ "
    索引:    01234
    预期:    索引 2~4（第二、三个 ▐）dim
    """
    result = render_blockquote_prefix(3)
    spans = result.spans
    dim_spans = [s for s in spans if s.style.dim]
    assert len(dim_spans) == 1, f"期望 1 个 dim span，实际 {len(dim_spans)}: {spans}"
    dim_span = dim_spans[0]
    assert dim_span.start == 2, f"dim 应起始于索引 2，实际 {dim_span.start}"
    assert dim_span.end == 4, f"dim 应结束于索引 4，实际 {dim_span.end}"


def test_blockquote_prefix_depth_1_no_dim():
    """depth=1 时不应应用 dim。"""
    result = render_blockquote_prefix(1)
    assert len(result.spans) == 0, f"depth=1 不应有 spans，实际 {result.spans}"


def test_blockquote_prefix_depth_6_max():
    """depth=6 时，第一个 ▐ 不 dim，第 2~6 个 ▐ 应 dim。

    字符串: " ▐▐▐▐▐▐ "
    索引:    01234567
    预期:    索引 2~7 dim
    """
    result = render_blockquote_prefix(6)
    spans = result.spans
    dim_spans = [s for s in spans if s.style.dim]
    assert len(dim_spans) == 1, f"期望 1 个 dim span，实际 {len(dim_spans)}: {spans}"
    dim_span = dim_spans[0]
    assert dim_span.start == 2, f"dim 应起始于索引 2，实际 {dim_span.start}"
    assert dim_span.end == 7, f"dim 应结束于索引 7，实际 {dim_span.end}"


# ═══════════════════════════════════════════════════════════
# Bug 2 回归测试: _build_highlight_style lru_cache 替代手动淘汰
# ═══════════════════════════════════════════════════════════

def test_build_highlight_style_caching():
    """_build_highlight_style 使用 lru_cache，相同键应返回同一 Style 对象。"""
    from rich.color import Color
    color = Color.from_rgb(100, 150, 200)

    s1 = _build_highlight_style(color, True, False, False, False)
    s2 = _build_highlight_style(color, True, False, False, False)

    # 相同参数应命中缓存返回同一对象
    assert s1 is s2, "lru_cache 应对相同参数返回同一 Style 对象"

    # 不同参数应返回不同对象
    s3 = _build_highlight_style(color, False, False, False, False)
    assert s1 is not s3, "不同参数应返回不同 Style 对象"

    # 验证属性正确
    assert s1.color == color
    assert s1.bold is True
    assert s1.italic is False
    assert s1.underline is False
    assert s1.bgcolor is None


def test_build_highlight_style_none_color():
    """_build_highlight_style 支持 color=None（纯 dim 样式）。"""
    s = _build_highlight_style(None, False, False, False, True)
    assert s.color is None
    assert s.strike is True
    assert s.bgcolor is None


# ═══════════════════════════════════════════════════════════
# render_diff_line 测试
# ═══════════════════════════════════════════════════════════

def test_diff_line_added():
    """+ 开头的行应显示为绿色。"""
    result = render_diff_line("+print('hello')")
    assert not result.style.bold
    assert result.plain == "+print('hello')"


def test_diff_line_removed():
    """- 开头的行应显示为红色。"""
    result = render_diff_line("-print('bye')")
    assert not result.style.bold
    assert result.plain == "-print('bye')"


def test_diff_line_hunk_header():
    """@@ 开头的行应显示为青色 + 粗体。"""
    result = render_diff_line("@@ -1,4 +1,5 @@")
    assert result.style.bold is True
    assert result.plain == "@@ -1,4 +1,5 @@"


def test_diff_line_context():
    """上下文行（空格开头）应显示为 dim + bright_black。"""
    result = render_diff_line(" def existing_func():")
    assert result.style.dim is True
    assert result.plain == " def existing_func():"


def test_diff_line_empty():
    """空行应返回空 Text。"""
    result = render_diff_line("")
    assert isinstance(result, Text)
    assert result.plain == ""


def test_diff_line_no_leading_char():
    """无行首特殊字符的行应视为上下文行。"""
    result = render_diff_line("plain line")
    assert result.style.dim is True
    assert result.plain == "plain line"

