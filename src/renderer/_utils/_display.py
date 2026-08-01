"""终端显示宽度计算与 Rich Text 转义标记处理。"""

from __future__ import annotations


# 零宽字符集合（frozenset 预计算，O(1) 查找）
_ZERO_WIDTH_CHARS = frozenset({
    0x00AD,      # SOFT HYPHEN
    0x200B,      # ZERO WIDTH SPACE
    0x200C,      # ZERO WIDTH NON-JOINER
    0x200D,      # ZERO WIDTH JOINER
    0x200E,      # LEFT-TO-RIGHT MARK
    0x200F,      # RIGHT-TO-LEFT MARK
    0x2060,      # WORD JOINER
    0x2061,      # FUNCTION APPLICATION
    0x2062,      # INVISIBLE TIMES
    0x2063,      # INVISIBLE SEPARATOR
    0x2064,      # INVISIBLE PLUS
    0xFE00, 0xFE01, 0xFE02, 0xFE03, 0xFE04,
    0xFE05, 0xFE06, 0xFE07, 0xFE08, 0xFE09,
    0xFE0A, 0xFE0B, 0xFE0C, 0xFE0D, 0xFE0E, 0xFE0F,  # 变体选择符
    0xFEFF,      # ZERO WIDTH NO-BREAK SPACE / BOM
})


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
_EMOJI_WIDE: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),
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
