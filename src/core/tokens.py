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


__all__ = ["estimate_tokens"]
