"""Token 估算工具（核心层公共模块，零依赖）。

启发式估算文本 token 数：中文字符约 2.5 tokens/字符，其他约 0.3 tokens/字符。
纯 ASCII 文本走快速路径（str.isascii() C 级实现）。

原位于 ``api.tokens``，下沉核心层以消除核心层对基础设施层（api）的反向
依赖；``api.tokens`` 仅 re-export 兼容。
"""

from __future__ import annotations

import functools


@functools.lru_cache(maxsize=256)
def estimate_tokens(text):
    """启发式估算文本 token 数。"""
    if not text:
        return 0
    if text.isascii():
        return max(1, int(len(text) * 0.3))
    cjk = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
                0xF900 <= cp <= 0xFAFF or 0x3040 <= cp <= 0x309F or
                0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF):
            cjk += 1
    other = len(text) - cjk
    return max(1, int(cjk * 2.5 + other * 0.3))


def count_cjk_other(text) -> tuple:
    """统计文本中的 CJK 字符数与其他字符数（与 estimate_tokens 同一分类口径）。

    流式场景可对每个增量 delta 调用本函数做增量累加，再用
    ``estimate_tokens_from_counts`` 还原「全文整体估算」——等价于直接对
    累计全文调用 ``estimate_tokens``，但避免每次 delta 都对全文重扫
    （O(1) 还原 + O(len(delta)) 增量），且不会因「逐 delta 分别估算再求和」
    的 ``max(1, ...)`` 下限而系统性高估。

    Returns:
        (cjk, other) 二元组；空文本返回 (0, 0)。
    """
    if not text:
        return (0, 0)
    cjk = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
                0xF900 <= cp <= 0xFAFF or 0x3040 <= cp <= 0x309F or
                0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF):
            cjk += 1
    return (cjk, len(text) - cjk)


def estimate_tokens_from_counts(cjk: int, other: int) -> int:
    """由字符分类计数估算 token 数（与 ``estimate_tokens(全文)`` 数值一致）。"""
    cjk = int(cjk or 0)
    other = int(other or 0)
    if cjk + other <= 0:
        return 0
    return max(1, int(cjk * 2.5 + other * 0.3))


__all__ = ["estimate_tokens", "count_cjk_other", "estimate_tokens_from_counts"]
