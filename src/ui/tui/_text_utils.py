"""纯文本工具函数 — 从 message_editor / _message_display 提取的统一实现。

消除 _message_display._truncate() 和 message_editor._truncate_text()
两个语义相同但签名不同的重复定义，统一为单一 truncate() 函数。

渐变分隔线工具 — 供 _message_display 和 _bottom_bar_pkg 共享使用，
避免渐变分隔线 ANSI 构建逻辑在两处重复实现。
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


# ── 渐变 ANSI 构建工具（共享模块，避免重复实现） ────────────

def build_gradient_ansi(colors: list[int], char: str = "\u2501", suffix_reset: bool = True) -> str:
    """从 256 色号列表构建渐变 ANSI 字符串。

    每个字符使用对应色号，适用于分隔线/进度条等场景。
    色号列表可通过 src.ui.colors.gradient_range() 生成。

    Args:
        colors: 256 色号列表（0-255）。
        char: 显示的字符，默认 ━ (U+2501)。
        suffix_reset: 是否在末尾追加 RESET 序列，默认 True。

    Returns:
        带 ANSI 256 色号的渐变字符串。
    """
    parts: list[str] = []
    for c in colors:
        parts.append(f"\033[38;5;{c}m{char}")
    result = "".join(parts)
    if suffix_reset:
        result += "\033[0m"
    return result


def build_gradient_ansi_frame(colors: list[int], index: int, char: str = "\u2501", suffix_reset: bool = True) -> str:
    """从颜色列表中取第 index 帧的颜色，构建单色 ANSI 字符串。

    适用于呼吸/脉动效果中需要逐帧输出单色字符的场景。
    index 超出范围时取模循环。

    Args:
        colors: 256 色号列表（0-255）。
        index: 帧索引，自动取模循环。
        char: 显示的字符，默认 ━ (U+2501)。
        suffix_reset: 是否在末尾追加 RESET 序列，默认 True。

    Returns:
        带 ANSI 256 色号的单色字符串。
    """
    if not colors:
        return ""
    color = colors[index % len(colors)]
    result = f"\033[38;5;{color}m{char}"
    if suffix_reset:
        result += "\033[0m"
    return result


__all__ = ["truncate", "build_gradient_ansi", "build_gradient_ansi_frame"]
