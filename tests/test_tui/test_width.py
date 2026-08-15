"""双宽度函数区间对齐测试（H1）— wcswidth_simple 与 cjk_display_width。

修复背景（2026-08-15 H1）：``src.tui._width.wcswidth_simple`` 的
``_CJK_RANGES`` 缺失 ``cjk_display_width`` 已覆盖的区间（Hangul Jamo /
CJK 部首补充 + 康熙部首 / 平假名·片假名·注音·CJK 兼容 / 韩文音节），
这些字符在 ink 侧（``wcswidth_simple``）计 1、在 renderer 侧
（``cjk_display_width``）计 2 → 含这些字符的行在 committed 侧（wrap 用
cjk）与 live 侧（wcswidth）测量不一致，破坏行级 diff 宽度不变量。

本测试锁定：区间边界 +1/-1、代表性字符宽度、双函数逐字符一致性、
字符串级与逐字符累加一致、ANSI 序列跳过回归。
"""

from __future__ import annotations

import pytest

from src.tui._width import wcswidth_simple
from src.renderer._utils._display import cjk_display_width


# ── 区间边界 +1/-1 ─────────────────────────────────────────

@pytest.mark.parametrize("cp,width", [
    # Hangul Jamo：0x1100-0x11FF（H1 新增）
    (0x10FF, 1), (0x1100, 2), (0x11FF, 2), (0x1200, 1),
    # CJK 部首补充 + 康熙部首：0x2E80-0x2FFF（H1 新增）
    (0x2E7F, 1), (0x2E80, 2), (0x2FFF, 2),
    # CJK 符号标点：0x3000-0x303F（既有）
    (0x3000, 2), (0x303F, 2),
    # 平假名/片假名/注音/CJK 兼容：0x3040-0x33FF（H1 新增）
    (0x3040, 2), (0x33FF, 2),
    # CJK 扩展 A / 统一表意（既有）
    (0x3400, 2), (0x4DBF, 2), (0x4E00, 2),
    # 韩文音节：0xAC00-0xD7AF（H1 新增）
    (0xABFF, 1), (0xAC00, 2), (0xD7AF, 2), (0xD800, 1),
])
def test_cjk_range_boundary_regression(cp, width):
    """H1 新增区间边界 +1/-1：wcswidth_simple 宽度正确。"""
    assert wcswidth_simple(chr(cp)) == width


# ── 代表性字符宽度 ─────────────────────────────────────────

def test_representative_cjk_chars_regression():
    """H1 代表性字符宽度：韩文/部首/假名宽度 2，半角片假名宽度 1。"""
    cases = [
        ("가", 2), ("한", 2),   # 韩文音节（U+AC00 区）
        ("ᄀ", 2),              # Hangul Jamo 辅音（U+1100 区）
        ("⼀", 2),              # 康熙部首（U+2F00）
        ("あ", 2), ("カ", 2),   # 平假名/片假名（U+3040-0x33FF）
        ("ｱ", 1),              # 半角片假名（U+FF71——非全角，宽 1）
        ("Ａ", 2),              # 全角 ASCII（U+FF21，全角区宽 2）
    ]
    for ch, width in cases:
        assert wcswidth_simple(ch) == width, f"{ch!r} (U+{ord(ch):04X})"


# ── 双函数一致性 ───────────────────────────────────────────

#: 覆盖各区间表的代表性字符集（CJK/全角/emoji/零宽/ASCII/新增区间）
_CONSISTENCY_CHARS = "".join([
    "abc XYZ 012",          # ASCII
    "가한ᄀ",                # 韩文（新增区间）
    "⼀⼆",                  # 部首（新增区间）
    "。、「」",              # CJK 标点
    "あカぁ",                # 平假名/片假名（新增区间）
    "中文测试",              # CJK 统一表意
    "ＡＢＣ",                # 全角 ASCII
    "ｱｶ",                  # 半角片假名（宽 1，双函数一致）
    "🎉🚀📖",                # emoji 宽
    "\u200b\u200c\u200d\u00ad",  # 零宽
    # 注意：不含 ANSI 序列（\x1b）——wcswidth_simple 跳过整段 ANSI 计 0、
    # cjk_display_width 仅测纯文本（ANSI 语义两函数设计不同，不属「字符
    # 宽度」比对范畴；ANSI 跳过回归由 test_ansi_skip_regression 覆盖）。
])


def test_dual_width_consistency_regression():
    """H1 双函数一致性：对覆盖各区间表的字符集逐字符宽度一致。"""
    for ch in _CONSISTENCY_CHARS:
        w1 = wcswidth_simple(ch)
        w2 = cjk_display_width(ch)
        assert w1 == w2, (
            f"{ch!r} (U+{ord(ch):04X}): wcswidth_simple={w1} "
            f"cjk_display_width={w2}"
        )


# ── 字符串级与逐字符累加一致 ───────────────────────────────

def test_string_vs_char_accumulate_regression():
    """H1 字符串级宽度 == 逐字符累加宽度（纯文本无 ANSI）。"""
    samples = [
        "가나다",
        "Hello 中文",
        "あいうえお",
        "  ",
        "ＡＢＣｱ",
        "中文 🎉 测试",
    ]
    for s in samples:
        char_sum = sum(wcswidth_simple(ch) for ch in s)
        assert wcswidth_simple(s) == char_sum, f"{s!r}"


def test_ansi_skip_regression():
    """H1 ANSI 序列跳过：含 ANSI 的字符串宽度 == 剥离 ANSI 后宽度。"""
    from src.tui.ink.helpers import strip_ansi
    samples = [
        "\x1b[31mabc\x1b[0m",
        "가\x1b[1m나\x1b[0m다",
        "\x1b[38;2;255;0;0mRED\x1b[0m",
        "\x1b[31m中文\x1b[0m",
    ]
    for s in samples:
        plain = strip_ansi(s)
        assert wcswidth_simple(s) == wcswidth_simple(plain), f"{s!r}"
