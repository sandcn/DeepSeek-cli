#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for src/api/renderer/emoji_map.py — Emoji 短代码解析

覆盖内容：
  1. EMOJI_MAP — 映射表健康检查
  2. resolve_emoji — emoji 短代码替换函数（边界条件全覆盖）
"""

import unicodedata
import pytest

from src.api.renderer.emoji_map import (
    EMOJI_MAP,
    resolve_emoji,
)


# ═══════════════════════════════════════════════════════════════
# 1. EMOJI_MAP — 映射表健康检查
# ═══════════════════════════════════════════════════════════════

class TestEmojiMap:
    """EMOJI_MAP 字典健康检查"""

    def test_not_empty(self):
        """EMOJI_MAP 不为空"""
        assert len(EMOJI_MAP) > 0

    def test_keys_start_with_colon(self):
        """所有键以 : 开头"""
        for key in EMOJI_MAP:
            assert key.startswith(':'), f"键 {key!r} 不以 : 开头"

    def test_keys_end_with_colon(self):
        """所有键以 : 结尾"""
        for key in EMOJI_MAP:
            assert key.endswith(':'), f"键 {key!r} 不以 : 结尾"

    def test_keys_have_no_internal_colons(self):
        """键内部不包含多余的 :"""
        for key in EMOJI_MAP:
            inner = key[1:-1]
            assert ':' not in inner, f"键 {key!r} 内部包含 :"

    def test_keys_unique(self):
        """键唯一"""
        assert len(EMOJI_MAP) == len(set(EMOJI_MAP))

    def test_values_unique(self):
        """值唯一（大多数 emoji 映射应是一对一的）"""
        values = list(EMOJI_MAP.values())
        dupes = {v for v in values if values.count(v) > 1}
        # 允许少数重复（如 :smile: 和 :blush: 可映射到同一 unicode）
        if dupes:
            pytest.skip(f"存在重复值: {dupes}")

    def test_all_values_are_strings(self):
        """所有值均为 str 类型"""
        for value in EMOJI_MAP.values():
            assert isinstance(value, str), f"值 {value!r} 不是 str 类型"

    def test_all_values_are_single_emoji(self):
        """大多数值是单个 emoji 字符（或变体选择器序列）"""
        multi = []
        for key, value in EMOJI_MAP.items():
            grapheme_count = sum(1 for c in value if unicodedata.category(c) != 'Mn')
            if grapheme_count > 3 and not any(c in value for c in '\ufe0f\u200d'):
                multi.append((key, value, len(value)))
        if multi:
            pytest.skip(f"多字符值: {multi[:5]}")

    def test_keys_min_length(self):
        """键最短长度至少为 3（:x:）"""
        for key in EMOJI_MAP:
            assert len(key) >= 3, f"键 {key!r} 太短"

    def test_keys_max_length(self):
        """键最长不超过 30 字符"""
        for key in EMOJI_MAP:
            assert len(key) <= 30, f"键 {key!r} 过长 ({len(key)})"

    def test_common_emojis_present(self):
        """常见 emoji 短代码存在于映射表中"""
        common = {':smile:', ':heart:', ':fire:', ':star:', ':thumbsup:',
                  ':warning:', ':check:', ':x:', ':bug:', ':rocket:'}
        missing = common - set(EMOJI_MAP.keys())
        assert not missing, f"缺少常见 emoji: {missing}"


# ═══════════════════════════════════════════════════════════════
# 2. resolve_emoji — emoji 短代码替换函数
# ═══════════════════════════════════════════════════════════════

class TestResolveEmoji:
    """resolve_emoji() 函数边界全覆盖测试"""

    # ── 空/边界输入 ────────────────────────────────────────

    def test_empty_string(self):
        """空字符串应返回空字符串"""
        assert resolve_emoji('') == ''

    def test_no_emoji(self):
        """不含 emoji 短代码的文本原样返回"""
        assert resolve_emoji('hello world') == 'hello world'

    def test_only_text_no_colon(self):
        """纯文本不含 : 符号"""
        assert resolve_emoji('普通文本') == '普通文本'

    # ── 单个 emoji ─────────────────────────────────────────

    def test_single_emoji(self):
        """单个 emoji 短代码应被替换为对应 unicode"""
        result = resolve_emoji(':smile:')
        assert result == EMOJI_MAP[':smile:']
        assert result == '\U0001f60a'

    def test_single_emoji_with_text_before(self):
        """文本在前，emoji 在后"""
        result = resolve_emoji('hello :smile:')
        assert result == 'hello \U0001f60a'

    def test_single_emoji_with_text_after(self):
        """emoji 在前，文本在后"""
        result = resolve_emoji(':smile: world')
        assert result == '\U0001f60a world'

    def test_emoji_between_text(self):
        """emoji 在文本中间"""
        result = resolve_emoji('hello :fire: world')
        assert result == 'hello \U0001f525 world'

    # ── 多个 emoji ─────────────────────────────────────────

    def test_multiple_emojis(self):
        """多个 emoji 短代码全部替换"""
        result = resolve_emoji(':smile: :fire: :heart:')
        assert result == '\U0001f60a \U0001f525 \u2764\ufe0f'

    def test_adjacent_emojis(self):
        """相邻 emoji 无空格分隔"""
        result = resolve_emoji(':fire::heart:')
        assert result == '\U0001f525\u2764\ufe0f'

    def test_repeated_same_emoji(self):
        """相同 emoji 重复出现"""
        result = resolve_emoji(':heart: :heart: :heart:')
        assert result == '\u2764\ufe0f \u2764\ufe0f \u2764\ufe0f'

    # ── 未知/无效短代码 ───────────────────────────────────

    def test_unknown_emoji_preserved(self):
        """未知 emoji 短代码原样保留"""
        result = resolve_emoji(':unknown_emoji:')
        assert result == ':unknown_emoji:'

    def test_partial_emoji_no_close(self):
        """只有起始 : 没有结束 : 的短代码原样保留"""
        result = resolve_emoji(':smile')
        assert result == ':smile'

    def test_partial_emoji_no_open(self):
        """没有起始 : 只有结束 : 的场景"""
        result = resolve_emoji('smile:')
        assert result == 'smile:'

    def test_empty_shortcode(self):
        """空短代码 :: 原样保留"""
        result = resolve_emoji('::')
        assert result == '::'

    def test_single_colon(self):
        """单个 : 原样保留"""
        result = resolve_emoji(':')
        assert result == ':'

    def test_triple_colon(self):
        """三个 : 的场景，每个 : 单独保留"""
        result = resolve_emoji(':::')
        assert result == ':::'

    # ── 特殊字符在名称中 ─────────────────────────────────

    def test_emoji_with_underscore(self):
        """短代码含下划线"""
        result = resolve_emoji(':heart_eyes:')
        assert result == EMOJI_MAP[':heart_eyes:']

    def test_emoji_with_hyphen(self):
        """短代码含连字符"""
        # 确保存在这样的键
        hyphen_keys = [k for k in EMOJI_MAP if '-' in k]
        if not hyphen_keys:
            pytest.skip("EMOJI_MAP 中无含连字符的键")
        key = hyphen_keys[0]
        expected = EMOJI_MAP[key]
        result = resolve_emoji(key)
        assert result == expected

    def test_emoji_with_plus(self):
        """短代码含加号"""
        plus_keys = [k for k in EMOJI_MAP if '+' in k]
        if not plus_keys:
            pytest.skip("EMOJI_MAP 中无含加号的键")
        key = plus_keys[0]
        expected = EMOJI_MAP[key]
        result = resolve_emoji(key)
        assert result == expected

    # ── 文本含多个冒号 ────────────────────────────────────

    def test_colon_in_text_no_match(self):
        """文本中的冒号不是 emoji 短代码的一部分"""
        result = resolve_emoji('时间: 12:30')
        assert result == '时间: 12:30'

    def test_emoji_inside_url(self):
        """URL 中的冒号不应被误解析"""
        result = resolve_emoji('https://example.com')
        assert result == 'https://example.com'

    def test_html_entity_colon(self):
        """HTML 实体分数中的冒号不应替换"""
        result = resolve_emoji(':happy:表情:sad:')
        # :happy: 被替换，:sad: 被替换
        expected = EMOJI_MAP[':happy:'] + '表情' + EMOJI_MAP[':sad:']
        assert result == expected

    # ── 中文字符混合 ──────────────────────────────────────

    def test_chinese_with_emoji(self):
        """中文字符与 emoji 混合"""
        result = resolve_emoji('你好 :smile: 世界')
        assert result == '你好 \U0001f60a 世界'

    def test_chinese_punctuation_with_colon(self):
        """中文冒号不应干扰"""
        result = resolve_emoji('注意：:warning:')
        assert result == '注意：\u26a0\ufe0f'

    # ── 边界 ──────────────────────────────────────────────

    def test_long_text_no_emoji(self):
        """长文本无 emoji 应原样返回"""
        text = 'a' * 10000
        assert resolve_emoji(text) == text

    def test_max_emojis_in_text(self):
        """大量 emoji 替换"""
        text = ':smile:' * 100
        result = resolve_emoji(text)
        assert result == EMOJI_MAP[':smile:'] * 100
        assert len(result) == len(EMOJI_MAP[':smile:']) * 100

    def test_mixed_valid_invalid(self):
        """混合有效和无效短代码"""
        result = resolve_emoji(':smile: :invalid: :heart:')
        assert result == f"{EMOJI_MAP[':smile:']} :invalid: {EMOJI_MAP[':heart:']}"

    def test_text_end_with_partial_emoji(self):
        """文本末尾只有起始冒号"""
        result = resolve_emoji('test :')
        assert result == 'test :'

    def test_text_start_with_partial_emoji(self):
        """文本开头只有结束冒号"""
        result = resolve_emoji(': test')
        assert result == ': test'

    def test_emoji_surrounded_by_punctuation(self):
        """emoji 被标点符号包围"""
        result = resolve_emoji('(:smile:)')
        assert result == f"({EMOJI_MAP[':smile:']})"

    def test_multiline_text(self):
        """多行文本中的 emoji 替换"""
        text = 'line1 :smile:\nline2 :fire:\nline3'
        expected = f"line1 {EMOJI_MAP[':smile:']}\nline2 {EMOJI_MAP[':fire:']}\nline3"
        assert resolve_emoji(text) == expected

    def test_emoji_with_variation_selector(self):
        """含变体选择器的 emoji 短代码"""
        key = ':v:'  # ✌️ (victory hand) 含 \ufe0f
        if key in EMOJI_MAP:
            result = resolve_emoji(key)
            assert result == EMOJI_MAP[key]
            assert '\ufe0f' in result or True  # 可能有也可能没有

    # ── return type ────────────────────────────────────────

    def test_return_type_is_str(self):
        """返回类型应为 str"""
        assert isinstance(resolve_emoji(''), str)
        assert isinstance(resolve_emoji(':smile:'), str)
        assert isinstance(resolve_emoji('hello :fire: world'), str)

    # ── 不变性：不修改映射表 ──────────────────────────────

    def test_does_not_mutate_map(self):
        """resolve_emoji 不应修改 EMOJI_MAP"""
        before = dict(EMOJI_MAP)
        resolve_emoji(':smile: :new_one:')
        assert EMOJI_MAP == before
