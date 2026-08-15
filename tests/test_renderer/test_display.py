"""cjk_display_width 侧回归 + 双函数一致性（H1，双保险）。

修复背景（2026-08-15 H1）：``src.tui._width.wcswidth_simple`` 的
``_CJK_RANGES`` 补齐缺失区间后，与 ``src.renderer._utils._display.
cjk_display_width`` 区间表内容一致。本文件锁定 renderer 侧既有行为
（韩文/部首/假名宽度 2）以及与 ``wcswidth_simple`` 的一致性（与
``tests/test_tui/test_width.py`` 同源字符集，双保险）。
"""

from __future__ import annotations

import pytest

from src.renderer._utils._display import cjk_display_width
from src.tui._width import wcswidth_simple


@pytest.mark.parametrize("ch", [
    "가", "한",      # 韩文音节（0xAC00-0xD7AF）
    "ᄀ",            # Hangul Jamo（0x1100-0x11FF）
    "⼀",            # 康熙部首（0x2E80-0x2FFF）
    "あ", "カ",      # 平假名/片假名（0x3040-0x33FF，含于 0x2E80-0x9FFF）
    "中",           # CJK 统一表意
])
def test_cjk_related_width_regression(ch):
    """cjk_display_width 对新增相关区间（韩文/部首/假名）宽度为 2。"""
    assert cjk_display_width(ch) == 2, f"{ch!r} (U+{ord(ch):04X})"


#: 与 tests/test_tui/test_width.py 同源字符集（双保险）
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
])


def test_dual_width_consistency_regression():
    """H1 双函数一致性：cjk_display_width 与 wcswidth_simple 同字符集一致。"""
    for ch in _CONSISTENCY_CHARS:
        w1 = cjk_display_width(ch)
        w2 = wcswidth_simple(ch)
        assert w1 == w2, (
            f"{ch!r} (U+{ord(ch):04X}): cjk_display_width={w1} "
            f"wcswidth_simple={w2}"
        )
