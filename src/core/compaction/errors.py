"""上下文溢出错误识别 — 对齐 dsh ``CONTEXT_WINDOW_EXCEEDED`` 规范化。

提供方对「上下文窗口超限」的措辞各异；这里以一组稳定的关键词识别异常
或错误文本，供溢出恢复路径决定是否压缩后重试。
"""

from __future__ import annotations

#: 上下文窗口超限的常见提供方措辞（小写子串匹配）。
_OVERFLOW_MARKERS = (
    "context length",
    "context_length_exceeded",
    "maximum context",
    "too many tokens",
    "reduce the length",
    "prompt is too long",
    "input is too long",
    "message too long",
    "maximum number of tokens",
    "token limit exceeded",
    "context limit",
)


def is_context_overflow_error(content=None, exc: BaseException | None = None) -> bool:
    """判断内容/异常是否表示上下文窗口超限。

    Args:
        content: 模型返回的文本（可能承载错误信息），或任意可转字符串对象。
        exc: 抛出的异常。

    Returns:
        True 表示应走压缩 + 重试的溢出恢复。
    """
    for candidate in (content, exc):
        if candidate is None:
            continue
        try:
            text = str(candidate).lower()
        except Exception:
            continue
        if not text:
            continue
        if any(marker in text for marker in _OVERFLOW_MARKERS):
            return True
    return False


__all__ = ["is_context_overflow_error"]
