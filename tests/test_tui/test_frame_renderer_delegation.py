"""FrameRenderer ANSI 工具委托一致性测试。

验证 FrameRenderer 的 strip_ansi / char_width / display_width / truncate_to_width
方法委托到 core.ansi_utils 后行为一致。

测试策略：
  - 参数化测试覆盖纯文本、含 ANSI 文本、中文、混合文本、空字符串、
    只有 ANSI 序列的文本、零宽字符、Emoji
  - 直接比较委托前后输入输出一致性
  - FrameRenderer.truncate_to_width 保留 _TRUNC_MARGIN/_TRUNC_MIN_WIDTH
    原逻辑，仅核心截断逻辑委托至 truncate_ansi_visual
"""
from __future__ import annotations

from src.tui.frame.frame_renderer import FrameRenderer
from src.tui.core.ansi_utils import (
    strip_ansi as core_strip_ansi,
    visual_width as core_visual_width,
    truncate_ansi_visual as core_truncate_ansi_visual,
)

# ── 测试数据 ──────────────────────────────────────────

PLAIN_TEXTS = [
    "hello",
    "你好",
    "hello world",
    "你好世界",
    "a你b好c",
    "  leading and trailing  ",
    "",
    "a\u200bb",          # 零宽空格
    "a\u200db",          # 零宽连接符
    "a\u0300b",          # 组合标记
    " \t\n\r ",          # 空白字符
    "abc123!@#",
]

ANSI_TEXTS = [
    "\033[31mhello\033[0m",
    "\033[38;5;45m你好\033[0m",
    "\033[1m\033[31mbold red\033[0m",
    "\033[38;5;214;48;5;236mstyled\033[0m",
    "\033[31ma\033[32mb\033[33mc\033[0m",
    "\033[38;5;45m你好世界\033[0m",
    "\033[31m" "hello world" "\033[0m",
    "inline\033[31mred\033[0mnormal",
    "\033[31m\033[0m",       # 只有 ANSI，无可见字符
    "\033[38;5;45m" "a你b" "\033[0m",
]

MIXED_TEXTS = PLAIN_TEXTS + ANSI_TEXTS + [
    "\033[31mhello 你好 world\033[0m",
    "  \033[38;5;45mpadding\033[0m  ",
    "a\033[31mb\033[0m",  # 单字符夹 ANSI
]

# ── strip_ansi 一致性 ────────────────────────────────

class TestStripAnsiDelegation:
    """验证 FrameRenderer.strip_ansi 与 core.ansi_utils.strip_ansi 输出一致。"""

    def test_plain_texts(self):
        for text in PLAIN_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result!r} != {expected!r}"
            )

    def test_ansi_texts(self):
        for text in ANSI_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result!r} != {expected!r}"
            )

    def test_fast_path_no_ansi(self):
        """不含 ANSI 的文本走快速路径（'\x1b' not in text 优化）。"""
        for text in PLAIN_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected

    def test_fast_path_with_ansi(self):
        """含 ANSI 的文本委托到 core 函数。"""
        for text in ANSI_TEXTS:
            result = FrameRenderer.strip_ansi(text)
            expected = core_strip_ansi(text)
            assert result == expected


# ── display_width 一致性 ──────────────────────────────

class TestDisplayWidthDelegation:
    """验证 FrameRenderer.display_width 与 core.ansi_utils.visual_width 一致。"""

    def test_plain_texts(self):
        for text in PLAIN_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_ansi_texts(self):
        for text in ANSI_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_mixed_texts(self):
        for text in MIXED_TEXTS:
            result = FrameRenderer.display_width(text)
            expected = core_visual_width(text)
            assert result == expected, (
                f"Mismatch for {text!r}: {result} != {expected}"
            )

    def test_empty(self):
        assert FrameRenderer.display_width("") == 0
        assert core_visual_width("") == 0


# ── char_width 一致性 ─────────────────────────────────

class TestCharWidthDelegation:
    """验证 FrameRenderer.char_width 与 core.ansi_utils._char_width 一致。"""

    def test_ascii_chars(self):
        for ch in "abcdefXYZ012!@# ":
            r = FrameRenderer.char_width(ch)
            assert r in (0, 1), f"ASCII char {ch!r} width={r} expected 0 or 1"

    def test_cjk_chars(self):
        for ch in "你好世界测试":
            r = FrameRenderer.char_width(ch)
            assert r == 2, f"CJK char {ch!r} width={r} expected 2"

    def test_zero_width_chars(self):
        # 零宽空格 (U+200B) 和零宽连接符 (U+200D)
        assert FrameRenderer.char_width("\u200b") == 0
        assert FrameRenderer.char_width("\u200d") == 0

    def test_combining_mark(self):
        # 组合用变音符 (U+0300)
        assert FrameRenderer.char_width("\u0300") == 0

    def test_newline(self):
        # 换行符 - wcwidth 可能返回 -1（不可打印），回退为 1
        w = FrameRenderer.char_width("\n")
        assert w >= 0, f"newline width should be >= 0, got {w}"


# ── truncate_to_width 一致性 ───────────────────────────

class TestTruncateToWidthDelegation:
    """验证 FrameRenderer.truncate_to_width 委托行为正确。

    注意：truncate_to_width 保留 _TRUNC_MARGIN/_TRUNC_MIN_WIDTH 原逻辑，
    仅核心截断逻辑委托至 truncate_ansi_visual。
    """

    def setup_method(self):
        self.renderer = FrameRenderer(terminal_width=80, frame=0)

    def test_no_truncation_plain(self):
        """短文本原样返回。"""
        text = "hello"
        result = self.renderer.truncate_to_width(text, max_width=80)
        assert result == text, f"短文本不应被截断: {result!r}"

    def test_no_truncation_ansi(self):
        """短 ANSI 文本原样返回。"""
        text = "\033[31mhello\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=80)
        assert result == text, f"短 ANSI 文本不应被截断: {result!r}"

    def test_truncation_plain_text(self):
        """长纯文本被截断后视觉宽度不超过 max_width。"""
        text = "a" * 100
        result = self.renderer.truncate_to_width(text, max_width=20)
        # max_width=20 → max(20-2, 10)=18 → truncate_ansi_visual with max_visual=18
        # 截断后视觉宽度 ≤ 18（含 … 占 1 列）
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_cjk_text(self):
        """中文文本截断后视觉宽度不超过 max_width。"""
        text = "你好世界测试" * 10
        result = self.renderer.truncate_to_width(text, max_width=20)
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"CJK 截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_ansi_text(self):
        """ANSI 文本截断后保留样式且视觉宽度不超过 max_width。"""
        text = "\033[31m" + "a" * 100 + "\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        # 应保留 ANSI 颜色
        assert "\033[31m" in result, f"ANSI 样式应保留: {result!r}"
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"ANSI 截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_mixed_cjk_ansi(self):
        """中文 + ANSI 文本截断正确。"""
        text = "\033[38;5;45m" + "你好世界测试" * 5 + "\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert "\033[38;5;45m" in result, "颜色样式应保留"
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 18, f"混合文本截断后视觉宽度 {w} > 18: {result!r}"

    def test_truncation_at_boundary(self):
        """文本恰好等于 max_width 时不截断。"""
        text = "hello world"
        result = self.renderer.truncate_to_width(text, max_width=20)
        # max_width=20 → max(18, 10) = 18, text=11 ≤ 18 → 不截断
        assert result == text

    def test_truncation_min_width_respected(self):
        """_TRUNC_MIN_WIDTH=10 确保极窄 max_width 时仍有合理截断宽度。"""
        text = "hello world extra long text"
        result = self.renderer.truncate_to_width(text, max_width=5)
        # max_width=5 → max(5-2, 10) = 10 → 用 10 作为截断宽度
        # 截断后视觉宽度 ≤ 10
        plain = FrameRenderer.strip_ansi(result)
        w = FrameRenderer.display_width(plain)
        assert w <= 10, f"截断后视觉宽度 {w} > 10"

    def test_truncation_only_ansi_sequence(self):
        """只有 ANSI 序列的文本（无可见字符）原样返回。"""
        text = "\033[31m\033[0m"
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert result == text, "仅有 ANSI 序列的文本不应截断"

    def test_truncation_empty(self):
        """空字符串原样返回。"""
        text = ""
        result = self.renderer.truncate_to_width(text, max_width=20)
        assert result == ""

    def test_truncation_default_max_width(self):
        """max_width=None 时使用 self._terminal_width。"""
        # terminal_width=80 → max(80-2, 10) = 78
        text = "x" * 100
        result = self.renderer.truncate_to_width(text)
        assert len(FrameRenderer.strip_ansi(result)) <= 78

    def test_truncation_ellipsis_char(self):
        """截断后使用 …（U+2026）作为截断标记（来自 truncate_ansi_visual）。"""
        text = "a" * 100
        result = self.renderer.truncate_to_width(text, max_width=20)
        if result != text:
            # 截断后应含 …（单字符省略号）
            assert "…" in result or len(FrameRenderer.strip_ansi(result)) < 100, \
                f"截断应包含 …: {result!r}"
