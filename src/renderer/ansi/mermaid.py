"""Mermaid 图渲染 — ASCII/Unicode 终端图形（零 Rich）。

TUI 内容路径的 Mermaid 渲染入口：委托 ``_mermaid_render``（11 类图的字符级
布局），并提供**有界结果缓存**——流式预览每次 write 重渲未闭合 Mermaid 块，
源码未变时直接复用布局结果。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from ._mermaid_render import render_mermaid

_STYLE_LABEL = Style(fg=45, bold=True)
_STYLE_FENCE = Style(fg=242, dim=True, italic=True)
_STYLE_OMITTED = Style(fg=238)

#: 渲染结果缓存：key = (source, dropped) → list[AnsiLine]（有界，超限整体清空）。
_CACHE: dict = {}
_CACHE_MAX = 128


def render_mermaid_block(source: str, dropped: int = 0) -> list[AnsiLine]:
    """渲染 Mermaid 源码为图形行（异常/未知类型时降级为源码展示）。

    Args:
        source: Mermaid 源码。
        dropped: 预览截断丢弃的行数（>0 时图形前插入省略提示行）。
    """
    key = (source or "", int(dropped or 0))
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    lines = render_mermaid(key[0])
    if key[1]:
        lines = [AnsiLine.of(f"… 前 {key[1]} 行省略", _STYLE_OMITTED)] + lines
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = lines
    return lines


def clear_mermaid_cache() -> None:
    """清空 Mermaid 渲染缓存（测试用）。"""
    _CACHE.clear()


__all__ = ["render_mermaid_block", "clear_mermaid_cache"]
