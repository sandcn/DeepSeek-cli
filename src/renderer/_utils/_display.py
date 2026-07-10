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
        else:
            width += 1
    return width


def strip_escape_markers(text: "Text") -> "Text":
    """移除 \\x00 转义标记，保留被转义字符的样式。

    内联 Markdown 预处理时将 \\X 转义为 \\x00+X，
    渲染完成后需移除 \\x00 标记，保持被转义字符的样式。

    Args:
        text: 含 \\x00 标记的 Rich Text

    Returns:
        移除标记后的 Rich Text
    """
    from rich.text import Text

    plain = text.plain
    if '\x00' not in plain:
        return text

    new_plain_chars: list[str] = []
    old_to_new: dict[int, int | None] = {}
    new_idx = 0
    for old_idx, ch in enumerate(plain):
        if ch == '\x00':
            old_to_new[old_idx] = None
        else:
            old_to_new[old_idx] = new_idx
            new_plain_chars.append(ch)
            new_idx += 1

    new_plain = ''.join(new_plain_chars)
    result = Text(new_plain, style=text.style)
    for span in text.spans:
        new_start: int | None = None
        new_end: int | None = None
        for old_pos in range(span.start, span.end):
            mapped = old_to_new.get(old_pos)
            if mapped is not None:
                if new_start is None:
                    new_start = mapped
                new_end = mapped + 1
        if new_start is not None and new_end is not None and new_end > new_start:
            result.stylize(span.style, new_start, new_end)
    return result
