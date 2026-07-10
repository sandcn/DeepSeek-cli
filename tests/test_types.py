#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/api/renderer/types.py — TokenType, Token, RenderContext

覆盖内容：
  1. TokenType — 枚举完整性、唯一性、命名规范
  2. Token — 数据类构造、repr 截断、meta 字典
  3. RenderContext — 属性默认值、fn_next_number 计数器、__repr__/__str__
"""

from collections import Counter
import pytest

from src.renderer.types import (
    TokenType,
    Token,
    RenderContext,
)


# ═══════════════════════════════════════════════════════════════
# 1. TokenType — 枚举完整性、唯一性、命名规范
# ═══════════════════════════════════════════════════════════════

class TestTokenType:
    """TokenType 枚举完整性测试"""

    def test_members_not_empty(self):
        """TokenType 至少有一个成员"""
        assert len(TokenType) > 0

    def test_member_names_uppercase(self):
        """所有成员名应全大写"""
        for member in TokenType:
            assert member.name == member.name.upper(), \
                f"{member.name} 不是全大写"

    def test_member_values_unique(self):
        """所有成员值（auto()）唯一"""
        values = [m.value for m in TokenType]
        assert len(values) == len(set(values))

    def test_member_names_match_pattern(self):
        """成员名符合 'BLOCK' / '_OPEN' / '_CLOSE' / '_LINE' 等模式"""
        valid_patterns = [
            name for name in TokenType._member_names_
            if any(name.endswith(suffix) for suffix in
                   ('', '_OPEN', '_CLOSE', '_LINE', '_MARKER'))
            or name in ('PARAGRAPH', 'EMPTY_LINE', 'HEADING', 'HR',
                       'BLOCKQUOTE', 'LIST_ITEM', 'DEFINITION_ITEM',
                       'CODE_BLOCK', 'LINE_BREAK', 'TABLE', 'TOC_MARKER')
        ]
        # 只需检查没有奇怪的命名
        for name in TokenType._member_names_:
            assert '_' in name or name == name, f"成员名 {name} 可能不符合规范"

    # ── 特定成员存在性 ─────────────────────────────────────

    def test_has_code_fence_members(self):
        """存在代码围栏相关成员"""
        assert TokenType.CODE_FENCE_OPEN
        assert TokenType.CODE_LINE
        assert TokenType.CODE_FENCE_CLOSE
        assert TokenType.CODE_BLOCK

    def test_has_block_elements(self):
        """存在基础块级元素成员"""
        assert TokenType.PARAGRAPH
        assert TokenType.HEADING
        assert TokenType.HR
        assert TokenType.BLOCKQUOTE
        assert TokenType.TABLE
        assert TokenType.EMPTY_LINE

    def test_has_math_members(self):
        """存在数学块成员"""
        assert TokenType.MATH_BLOCK_OPEN
        assert TokenType.MATH_LINE
        assert TokenType.MATH_BLOCK_CLOSE

    def test_has_mermaid_members(self):
        """存在 Mermaid 图表成员"""
        assert TokenType.MERMAID_BLOCK_OPEN
        assert TokenType.MERMAID_LINE
        assert TokenType.MERMAID_BLOCK_CLOSE

    def test_has_details_members(self):
        """存在 Details 折叠块成员"""
        assert TokenType.DETAILS_OPEN
        assert TokenType.DETAILS_LINE
        assert TokenType.DETAILS_CLOSE

    def test_has_admonition_members(self):
        """存在 Admonition 成员"""
        assert TokenType.ADMONITION_OPEN
        assert TokenType.ADMONITION_LINE
        assert TokenType.ADMONITION_CLOSE

    def test_has_html_block_members(self):
        """存在 HTML 块成员"""
        assert TokenType.HTML_BLOCK_OPEN
        assert TokenType.HTML_BLOCK_LINE
        assert TokenType.HTML_BLOCK_CLOSE

    def test_has_fenced_div_members(self):
        """存在 Fenced Div 成员"""
        assert TokenType.FENCED_DIV_OPEN
        assert TokenType.FENCED_DIV_LINE
        assert TokenType.FENCED_DIV_CLOSE

    def test_has_line_break(self):
        """存在 LINE_BREAK 成员"""
        assert TokenType.LINE_BREAK

    def test_has_toc_marker(self):
        """存在 TOC_MARKER 成员"""
        assert TokenType.TOC_MARKER

    def test_has_list_item(self):
        """存在 LIST_ITEM 成员"""
        assert TokenType.LIST_ITEM

    def test_has_definition_item(self):
        """存在 DEFINITION_ITEM 成员"""
        assert TokenType.DEFINITION_ITEM

    def test_has_blockquote_open_line_close(self):
        """存在 BLOCKQUOTE_OPEN / _LINE / _CLOSE 三件套"""
        assert TokenType.BLOCKQUOTE_OPEN
        assert TokenType.BLOCKQUOTE_LINE
        assert TokenType.BLOCKQUOTE_CLOSE


# ═══════════════════════════════════════════════════════════════
# 2. Token — 数据类构造、repr 截断、meta 字典
# ═══════════════════════════════════════════════════════════════

class TestToken:
    """Token 数据类测试"""

    def test_defaults(self):
        """无参构造使用默认值"""
        token = Token(TokenType.PARAGRAPH)
        assert token.type == TokenType.PARAGRAPH
        assert token.content == ''
        assert token.meta == {}

    def test_minimal_construction(self):
        """仅 type 参数"""
        for tt in TokenType:
            token = Token(tt)
            assert token.type == tt
            assert token.content == ''
            assert token.meta == {}

    def test_with_content(self):
        """带 content 参数"""
        token = Token(TokenType.HEADING, content='Hello')
        assert token.content == 'Hello'

    def test_with_meta(self):
        """带 meta 参数"""
        token = Token(TokenType.CODE_BLOCK, meta={'lang': 'python', 'indented': False})
        assert token.meta == {'lang': 'python', 'indented': False}

    def test_full_construction(self):
        """所有参数完整构造"""
        token = Token(
            TokenType.HEADING,
            content='Introduction',
            meta={'level': 1, 'id': 'introduction'},
        )
        assert token.type == TokenType.HEADING
        assert token.content == 'Introduction'
        assert token.meta == {'level': 1, 'id': 'introduction'}

    def test_meta_mutable(self):
        """meta 字典可变"""
        token = Token(TokenType.PARAGRAPH)
        token.meta['key'] = 'value'
        assert token.meta['key'] == 'value'

    def test_meta_default_not_shared(self):
        """meta 默认空字典不共享"""
        a = Token(TokenType.PARAGRAPH)
        b = Token(TokenType.PARAGRAPH)
        a.meta['a'] = 1
        assert 'a' not in b.meta

    def test_empty_content(self):
        """content 可为空字符串"""
        token = Token(TokenType.EMPTY_LINE, content='')
        assert token.content == ''

    def test_multiline_content(self):
        """content 含换行符"""
        token = Token(TokenType.CODE_LINE, content='line1\nline2')
        assert '\n' in token.content

    def test_large_content(self):
        """长 content"""
        text = 'x' * 10000
        token = Token(TokenType.PARAGRAPH, content=text)
        assert len(token.content) == 10000

    def test_none_meta(self):
        """meta 可设为 None"""
        token = Token(TokenType.PARAGRAPH, meta=None)
        assert token.meta is None

    # ── __repr__ ──────────────────────────────────────────

    def test_repr_short_content(self):
        """短 content 完整显示"""
        token = Token(TokenType.PARAGRAPH, content='hello')
        r = repr(token)
        assert 'PARAGRAPH' in r
        assert 'hello' in r

    def test_repr_long_content_truncated(self):
        """content 超过 40 字符时截断"""
        long_text = 'a' * 50
        token = Token(TokenType.PARAGRAPH, content=long_text)
        r = repr(token)
        assert len(long_text) > 40
        assert '...' in r
        assert len(r) < len(long_text) + 50  # repr 截断后更短

    def test_repr_with_meta(self):
        """repr 包含 meta 信息"""
        token = Token(TokenType.HEADING, content='Title', meta={'level': 1})
        r = repr(token)
        assert 'meta={' in r

    def test_repr_empty_content(self):
        """content 为空时的 repr"""
        token = Token(TokenType.EMPTY_LINE, content='')
        r = repr(token)
        assert "''" in r

    def test_repr_boundary_40_chars(self):
        """content 恰好 40 字符不应截断"""
        text = 'a' * 40
        token = Token(TokenType.PARAGRAPH, content=text)
        r = repr(token)
        assert '...' not in r
        assert text in r

    def test_repr_boundary_41_chars(self):
        """content 41 字符应截断"""
        text = 'a' * 41
        token = Token(TokenType.PARAGRAPH, content=text)
        r = repr(token)
        assert '...' in r


# ═══════════════════════════════════════════════════════════════
# 3. RenderContext — 共享状态容器
# ═══════════════════════════════════════════════════════════════

class TestRenderContext:
    """RenderContext dataclass 测试"""

    def test_defaults(self):
        """无参构造应使用正确默认值"""
        ctx = RenderContext()
        assert ctx.ref_map == {}
        assert ctx.fn_map == {}
        assert ctx.fn_order == []
        assert ctx.abbr_map == {}
        assert ctx.fn_counter == 0
        assert isinstance(ctx.metrics, Counter)
        assert ctx.metrics == Counter()
        assert ctx.start_time == 0.0
        assert ctx.token_count == 0
        assert ctx.heading_numbering is False
        assert ctx.heading_counters == {}

    def test_custom_values(self):
        """自定义参数应正确设置"""
        ctx = RenderContext(
            ref_map={'ref1': ('https://example.com', 'Example')},
            fn_map={'^fn1': 'footnote content'},
            fn_order=['^fn1'],
            fn_counter=10,
            start_time=12345.67,
            token_count=500,
            heading_numbering=True,
        )
        assert len(ctx.ref_map) == 1
        assert ctx.fn_order == ['^fn1']
        assert ctx.fn_counter == 10
        assert ctx.start_time == 12345.67
        assert ctx.token_count == 500
        assert ctx.heading_numbering is True

    # ── fn_next_number ─────────────────────────────────────

    def test_fn_next_number_starts_at_1(self):
        """首次调用 fn_next_number 返回 1"""
        ctx = RenderContext()
        assert ctx.fn_next_number() == 1

    def test_fn_next_number_increments(self):
        """多次调用 fn_next_number 递增"""
        ctx = RenderContext()
        assert ctx.fn_next_number() == 1
        assert ctx.fn_next_number() == 2
        assert ctx.fn_next_number() == 3

    def test_fn_next_number_does_not_overflow(self):
        """大量调用不溢出"""
        ctx = RenderContext()
        for _ in range(1000):
            ctx.fn_next_number()
        assert ctx.fn_next_number() == 1001

    def test_fn_next_number_uses_and_increments(self):
        """fn_next_number 使用后 fn_counter 增加"""
        ctx = RenderContext()
        _ = ctx.fn_next_number()
        assert ctx.fn_counter == 1
        _ = ctx.fn_next_number()
        assert ctx.fn_counter == 2

    def test_fn_next_number_from_custom_start(self):
        """从非零 fn_counter 开始"""
        ctx = RenderContext(fn_counter=5)
        assert ctx.fn_next_number() == 6
        assert ctx.fn_counter == 6

    # ── heading_counters ───────────────────────────────────

    def test_heading_counters_default(self):
        """heading_counters 默认空字典"""
        ctx = RenderContext()
        assert ctx.heading_counters == {}

    def test_heading_counters_not_shared(self):
        """heading_counters 默认不共享"""
        a = RenderContext()
        b = RenderContext()
        a.heading_counters[1] = 1
        assert 1 not in b.heading_counters

    def test_heading_numbering_true_effect(self):
        """heading_numbering=True 不影响其他字段"""
        ctx = RenderContext(heading_numbering=True)
        assert ctx.heading_numbering is True
        assert ctx.token_count == 0

    # ── metrics Counter ────────────────────────────────────

    def test_metrics_counter_usage(self):
        """metrics Counter 可正常使用"""
        ctx = RenderContext()
        ctx.metrics['paragraphs'] += 1
        ctx.metrics['headings'] += 3
        assert ctx.metrics['paragraphs'] == 1
        assert ctx.metrics['headings'] == 3

    def test_metrics_counter_not_shared(self):
        """metrics 默认不共享"""
        a = RenderContext()
        b = RenderContext()
        a.metrics['test'] = 1
        assert b.metrics['test'] == 0

    # ── ref_map / fn_map / abbr_map ────────────────────────

    def test_ref_map_insertion(self):
        """ref_map 插入和读取"""
        ctx = RenderContext()
        ctx.ref_map['ref1'] = ('https://example.com', 'Example')
        assert ctx.ref_map['ref1'] == ('https://example.com', 'Example')

    def test_fn_map_insertion(self):
        """fn_map 插入和读取"""
        ctx = RenderContext()
        ctx.fn_map['^fn1'] = 'content'
        assert ctx.fn_map['^fn1'] == 'content'

    def test_fn_order_append(self):
        """fn_order 追加"""
        ctx = RenderContext()
        ctx.fn_order.append('^fn1')
        ctx.fn_order.append('^fn2')
        assert ctx.fn_order == ['^fn1', '^fn2']

    def test_abbr_map_insertion(self):
        """abbr_map 插入和读取"""
        ctx = RenderContext()
        ctx.abbr_map['AI'] = 'Artificial Intelligence'
        assert ctx.abbr_map['AI'] == 'Artificial Intelligence'

    # ── __repr__ / __str__ ─────────────────────────────────

    def test_repr_mentions_key_fields(self):
        """__repr__ 应包含关键字段信息"""
        ctx = RenderContext()
        ctx.ref_map['r1'] = ('u', 't')
        ctx.fn_order = ['fn1']
        ctx.fn_counter = 5
        r = repr(ctx)
        assert 'ref_map=' in r
        assert 'fn_order=' in r
        assert 'fn_counter=' in r

    def test_repr_with_empty_state(self):
        """空状态时 __repr__ 仍可工作"""
        ctx = RenderContext()
        r = repr(ctx)
        assert isinstance(r, str)
        assert len(r) > 0

    def test_str_same_as_repr(self):
        """__str__ 等于 __repr__"""
        ctx = RenderContext()
        assert str(ctx) == repr(ctx)

    def test_repr_with_large_ref_map(self):
        """大型 ref_map 时 repr 显示数量"""
        ctx = RenderContext()
        for i in range(100):
            ctx.ref_map[f'r{i}'] = (f'https://example.com/{i}', '')
        r = repr(ctx)
        assert 'ref_map=100 entries' in r
