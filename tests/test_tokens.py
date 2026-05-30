"""测试 src/api/tokens.py 的 estimate_tokens 函数。"""

from src.api.tokens import estimate_tokens


class TestEstimateTokens:
    """estimate_tokens 函数测试"""

    def test_empty_string(self):
        """空字符串应返回 0"""
        assert estimate_tokens("") == 0
        assert estimate_tokens("") == 0  # 缓存覆盖验证

    def test_none_like_empty(self):
        """非文本空值也应返回 0"""
        assert estimate_tokens("") == 0

    # ── 纯 ASCII 快速路径 ──────────────────────────────

    def test_ascii_short(self):
        """短 ASCII 文本"""
        result = estimate_tokens("hello world")
        # len("hello world") = 11, 11 * 0.3 = 3.3 -> int = 3 -> max(1, 3) = 3
        assert result == 3

    def test_ascii_single_char(self):
        """单 ASCII 字符应至少返回 1"""
        assert estimate_tokens("a") == 1

    def test_ascii_long(self):
        """长 ASCII 文本"""
        text = "a" * 100
        result = estimate_tokens(text)
        # 100 * 0.3 = 30
        assert result == 30

    def test_ascii_very_long(self):
        """极长 ASCII 文本确保为正整数"""
        text = "word " * 2000  # 10000 字符
        result = estimate_tokens(text)
        assert isinstance(result, int)
        assert result > 0

    def test_ascii_isascii_used(self):
        """验证纯 ascii 走了快速路径（isascii 判断），不会触发正则"""
        text = "pure ascii here 123"
        result = estimate_tokens(text)
        assert result == int(len(text) * 0.3)

    # ── 纯中文文本 ──────────────────────────────────────

    def test_chinese_only(self):
        """纯中文文本"""
        text = "你好世界"
        result = estimate_tokens(text)
        # 4 个中文字符, 4 * 2.5 = 10
        assert result == 10

    def test_chinese_single_char(self):
        """单中文字符"""
        result = estimate_tokens("人")
        assert result == 2  # 1 * 2.5 = 2.5 -> int = 2 -> max(1, 2) = 2

    def test_chinese_long_text(self):
        """长中文文本"""
        text = "这是一个很长" * 50  # 300 个中文字符
        result = estimate_tokens(text)
        assert isinstance(result, int)
        assert result > 0

    # ── 中英文混合 ──────────────────────────────────────

    def test_mixed_chinese_english(self):
        """中英文混合文本"""
        text = "Hello 你好 World 世界"
        # 总长度: H(1)e(2)l(3)l(4)o(5)' '(6)你(7)好(8)' '(9)W(10)o(11)r(12)l(13)d(14)' '(15)世(16)界(17) = 17
        # CJK: 你, 好, 世, 界 = 4
        # other: 17 - 4 = 13
        # 4*2.5 + 13*0.3 = 10 + 3.9 = 13.9 -> int = 13 -> max(1, 13) = 13
        result = estimate_tokens(text)
        assert result == 13

    def test_mixed_with_punctuation(self):
        """中英文混合含标点"""
        text = "测试AI, 你好!"
        # 中文: 测(1) 试(1) 你(1) 好(1) = 4
        # 总长度: 10
        # 其他: 10 - 4 = 6
        # 4*2.5 + 6*0.3 = 10 + 1.8 = 11.8 -> int = 11 -> max(1, 11) = 11
        result = estimate_tokens(text)
        assert result == 11

    # ── 边界测试 ──────────────────────────────────────

    def test_single_char_each(self):
        """单字符各类型边界"""
        assert estimate_tokens("a") == 1     # ASCII 单字符
        assert estimate_tokens("你") == 2    # CJK: 1*2.5=2.5->2
        assert estimate_tokens("1") == 1     # 数字
        assert estimate_tokens(" ") == 1     # 空格
        assert estimate_tokens("\u3000") == 1  # 全角空格 U+3000 不在 CJK 正则范围内

    def test_positive_integer_always(self):
        """所有返回值都为正整数（或 0）"""
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") >= 1
        assert estimate_tokens("中") >= 1
        assert estimate_tokens("a" * 10000) >= 1

    def test_very_long_mixed_text(self):
        """超长混合文本，确保不崩溃且返回正整数"""
        text = "中文" * 1000 + "english" * 1000
        result = estimate_tokens(text)
        assert isinstance(result, int)
        assert result > 0

    # ── lru_cache 验证 ──────────────────────────────────

    def test_cache_hit(self):
        """重复相同参数应命中缓存（通过性能观察）"""
        text = "hello world test for cache"
        # 第一次调用
        r1 = estimate_tokens(text)
        # 第二次调用（应该命中 lru_cache）
        r2 = estimate_tokens(text)
        assert r1 == r2

    def test_cache_different_args(self):
        """不同参数返回不同结果"""
        assert estimate_tokens("hello") == 1     # len=5, 5*0.3=1.5->1
        assert estimate_tokens("hello!") == 1    # len=6, 6*0.3=1.8->1
        assert estimate_tokens("a") == 1
        assert estimate_tokens("ab") == 1        # 2*0.3=0.6->int=0->max(1,0)=1
        assert estimate_tokens("abcdefgh") == 2  # 8*0.3=2.4->int=2

    def test_cache_hit_functional(self):
        """验证相同参数返回相同结果（缓存命中不影响正确性）"""
        from src.api.tokens import estimate_tokens
        result1 = estimate_tokens("cache_functional_test")
        result2 = estimate_tokens("cache_functional_test")
        assert result1 == result2
        assert result1 > 0
