"""Mermaid 图渲染 — 首版退化为等宽纯文本（明确标注限制）。

已知限制（2026-08-01 首版）：Mermaid 图形渲染成本高，首版以
等宽框线 + 源码形式展示，保留图源可读性；后续补齐真正的图布局。

复用 ``renderer._mermaid_*`` 解析模块的可行性已评估——当前发射逻辑
强耦合 Rich；首版选择纯文本退化并明确标注。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine

_STYLE_LABEL = Style(fg=45, bold=True)
_STYLE_BODY = Style(fg=242)
_STYLE_FENCE = Style(fg=242, dim=True, italic=True)


def render_mermaid_block(source: str) -> list[AnsiLine]:
    """渲染 Mermaid 块（等宽框线纯文本，标注限制）。"""
    out: list[AnsiLine] = []
    out.append(AnsiLine.of("\u256d\u2500 Mermaid 图（纯文本视图） \u2500\u256e", _STYLE_LABEL))
    for line in source.split("\n"):
        out.append(AnsiLine.of(f"\u2502 {line}", _STYLE_BODY))
    out.append(AnsiLine.of("\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f", _STYLE_FENCE))
    return out


__all__ = ["render_mermaid_block"]
