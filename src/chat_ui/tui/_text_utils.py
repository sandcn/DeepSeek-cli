"""纯文本工具函数 — 从 message_editor / _message_display 提取的统一实现。

消除 _message_display._truncate() 和 message_editor._truncate_text()
两个语义相同但签名不同的重复定义，统一为单一 truncate() 函数。
"""

from __future__ import annotations


def truncate(
    text: str | None,
    max_len: int,
    *,
    suffix: str = "\u2026",  # "…"
    normalize: bool = True,
) -> str:
    """截断文本到指定长度。

    超长时在 max_len 位置截断并追加 suffix（默认 "…"）。
    normalize=True 时先规范化空白（替换换行为空格、去首尾空白），
    与 _message_display._truncate 行为一致。

    Args:
        text: 要截断的文本，None 视为空字符串。
        max_len: 最大字符数（不含 suffix）。
        suffix: 超长时追加的后缀。
        normalize: 是否先规范化空白。

    Returns:
        截断后的文本。长度 ≤ max_len 时原样返回。
    """
    if not text:
        return ""
    if max_len < 0:
        raise ValueError(f"max_len must be >= 0, got {max_len}")
    if normalize:
        text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + suffix


__all__ = ["truncate"]
