"""数学公式渲染 — 首版退化为等宽纯文本（明确标注限制）。

已知限制（2026-08-01 首版）：LaTeX → 终端符号布局的移植成本高，
首版以等宽纯文本展示公式源（保留可读性）；后续补齐。

可复用资产评估：``renderer.math_renderer`` 强耦合 Rich（Text 布局），
首版不引入；等宽纯文本无依赖。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine

_STYLE_LABEL = Style(fg=45, bold=True)
_STYLE_BODY = Style(fg=242)


def render_math_block(source: str) -> list[AnsiLine]:
    """渲染数学块（等宽纯文本，标注限制）。"""
    out: list[AnsiLine] = []
    out.append(AnsiLine.of("\u256d\u2500 Math（纯文本视图） \u2500\u256e", _STYLE_LABEL))
    for line in source.split("\n"):
        out.append(AnsiLine.of(f"\u2502 {line}", _STYLE_BODY))
    out.append(AnsiLine.of("\u2570\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u256f", _STYLE_LABEL))
    return out


__all__ = ["render_math_block"]
