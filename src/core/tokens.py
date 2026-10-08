"""Token 估算工具（核心层公共模块，零依赖）。

启发式估算文本 token 数，系数对齐 DeepSeek 官方文档
（https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/，
2026-10 核对）：

    - 1 个英文字符（ASCII）≈ 0.3 token
    - 1 个中文字符（CJK/假名/谚文）≈ 0.6 token

★ 2026-10 修正（用户反馈「main 上下文百分比统计不准」）：中文系数原为
``2.5``（远高于真实的 0.6，纯中文内容虚高约 3~4 倍），导致模式行
``main · N%`` 与压缩触发点系统性偏高/提前。修正为官方比例后，中文与
混合文本的估算值显著接近真实 usage（服务端 ``prompt_tokens``）。

纯 ASCII 文本走快速路径（``str.isascii()`` C 级实现）。

原位于 ``api.tokens``，下沉核心层以消除核心层对基础设施层（api）的反向
依赖；``api.tokens`` 仅 re-export 兼容。
"""

from __future__ import annotations

import functools

#: CJK（中文/日文假名/韩文）每字符估算 token 数（DeepSeek 官方 ≈ 0.6）。
CJK_TOKENS_PER_CHAR = 0.6
#: 其他（ASCII / 拉丁等）每字符估算 token 数（DeepSeek 官方 ≈ 0.3）。
OTHER_TOKENS_PER_CHAR = 0.3


def is_cjk_char(ch: str) -> bool:
    """判断单个字符是否属于 CJK 分类（与 ``estimate_tokens`` 同一口径）。

    覆盖：CJK 统一表意文字（含扩展 A / 兼容区）、日文平假名/片假名、
    韩文音节与韩文字母。
    """
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0xF900 <= cp <= 0xFAFF or 0x3040 <= cp <= 0x309F or
            0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF)


@functools.lru_cache(maxsize=256)
def estimate_tokens(text):
    """启发式估算文本 token 数。"""
    if not text:
        return 0
    if text.isascii():
        return max(1, int(len(text) * OTHER_TOKENS_PER_CHAR))
    cjk = 0
    for ch in text:
        if is_cjk_char(ch):
            cjk += 1
    other = len(text) - cjk
    return max(1, int(cjk * CJK_TOKENS_PER_CHAR + other * OTHER_TOKENS_PER_CHAR))


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
        if is_cjk_char(ch):
            cjk += 1
    return (cjk, len(text) - cjk)


def estimate_tokens_from_counts(cjk: int, other: int) -> int:
    """由字符分类计数估算 token 数（与 ``estimate_tokens(全文)`` 数值一致）。"""
    try:
        cjk = int(cjk or 0)
        other = int(other or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    if cjk + other <= 0:
        return 0
    return max(1, int(cjk * CJK_TOKENS_PER_CHAR + other * OTHER_TOKENS_PER_CHAR))


__all__ = [
    "estimate_tokens",
    "count_cjk_other",
    "estimate_tokens_from_counts",
    "is_cjk_char",
    "CJK_TOKENS_PER_CHAR",
    "OTHER_TOKENS_PER_CHAR",
]
