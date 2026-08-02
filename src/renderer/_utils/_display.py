"""终端显示宽度计算与 Rich Text 转义标记处理。"""

from __future__ import annotations


# 组合标记区段（零宽——终端以其上方基准字符渲染，不占列；与
# ``src.tui._screen._ZERO_WIDTH_RANGES`` 对齐，双宽度函数一致——方向1 修复：
# 修复前 cjk_display_width 把组合标记计宽 1，与 ink 布局的 wcswidth_simple
# （计宽 0）不一致，同一文本在两处测量结果不同 → 含组合符的行换行/截断错位）。
_COMBINING_MARK_RANGES: tuple[tuple[int, int], ...] = (
    (0x0300, 0x036F),    # Combining Diacritical Marks
    (0x1AB0, 0x1AFF),    # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),    # Combining Diacritical Marks Supplement
    (0x20D0, 0x20FF),    # Combining Diacritical Marks for Symbols
    (0xFE20, 0xFE2F),    # Combining Half Marks
    (0xE0100, 0xE01EF),  # Variation Selectors Supplement
)


def _zero_width_codepoints() -> frozenset:
    """构建零宽码点集合（显式单点 + 组合标记区段展开）。"""
    cps = {
        0x00AD, 0x200B, 0x200C, 0x200D, 0x200E, 0x200F,
        0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
        0xFE00, 0xFE01, 0xFE02, 0xFE03, 0xFE04,
        0xFE05, 0xFE06, 0xFE07, 0xFE08, 0xFE09,
        0xFE0A, 0xFE0B, 0xFE0C, 0xFE0D, 0xFE0E, 0xFE0F,  # 变体选择符
        0xFEFF,  # ZERO WIDTH NO-BREAK SPACE / BOM
    }
    for lo, hi in _COMBINING_MARK_RANGES:
        cps.update(range(lo, hi + 1))
    return frozenset(cps)


#: 零宽字符集合（frozenset 预计算，O(1) 查找）
_ZERO_WIDTH_CHARS = _zero_width_codepoints()


def cjk_display_width(s: str) -> int:
    """计算字符串的终端显示宽度（CJK=2，其他=1）。

    使用 frozenset 零宽字符查找 + 展开的 if-elif 链，
    替代 tuple 遍历，CPython 分支预测更友好。
    """
    width = 0
    for ch in s:
        cp = ord(ch)
        # 零宽字符：frozenset 哈希查找 O(1)
        if cp in _ZERO_WIDTH_CHARS:
            continue
        # 展开的 if-elif 链：编译器优化更友好
        if 0x1100 <= cp <= 0x11FF:      # Hangul Jamo
            width += 2
        elif 0x2E80 <= cp <= 0x9FFF:    # CJK + 韩文（最大块）
            width += 2
        elif 0xAC00 <= cp <= 0xD7AF:    # 韩文音节
            width += 2
        elif 0xF900 <= cp <= 0xFAFF:    # CJK 兼容
            width += 2
        elif 0xFF01 <= cp <= 0xFF60:    # 全角 ASCII
            width += 2
        elif 0xFFE0 <= cp <= 0xFFE6:    # 全角符号
            width += 2
        elif 0x20000 <= cp <= 0x3134F:  # CJK Extension B/G/H
            width += 2
        elif _in_emoji_wide(cp):        # emoji 宽符号（wcwidth emoji-wide 集）
            width += 2
        else:
            width += 1
    return width


# Emoji 宽符号范围（终端以 2 列渲染；与 wcwidth emoji-wide 集对齐）。
# ⚠ 不含 ✔✎⚙✕ 等文本呈现符号（宽度 1）——误计为 2 会导致表格/布局错位。
# 方向1（RI 码点）：首项拆为 (0x1F000, 0x1F1E5) + (0x1F200, 0x1FAFF)，排除
# Regional Indicator（RI，0x1F1E6-0x1F1FF，国旗字母）——与
# ``src.tui._screen._EMOJI_WIDE_RANGES`` 对齐（单 RI 计宽 1、成对 RI 按
# 1×2=2 列）。修复前 (0x1F000, 0x1FAFF) 把单 RI 计宽 2，双宽度函数不一致。
_EMOJI_WIDE: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1F1E5),   # 主要 emoji 块（📖📄🔍 等；不含 RI 码点）
    (0x1F200, 0x1FAFF),   # 主要 emoji 块续（🈁 等；RI 码点 0x1F1E6-0x1F1FF 已排除）
    (0x231A, 0x231B),
    (0x23E9, 0x23EC),
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),
    (0x2614, 0x2615),
    (0x2648, 0x2653),
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),
    (0x26AA, 0x26AB),
    (0x26BD, 0x26BE),
    (0x26C4, 0x26C5),
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),
    (0x270A, 0x270B),
    (0x2728, 0x2728),
    (0x274C, 0x274C),
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),
    (0x2B50, 0x2B50),
    (0x2B55, 0x2B55),
)


def _in_emoji_wide(cp: int) -> bool:
    """检查码点是否在 emoji 宽符号范围内。"""
    for lo, hi in _EMOJI_WIDE:
        if lo <= cp <= hi:
            return True
    return False
