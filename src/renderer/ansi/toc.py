"""TOC 渲染 — Table of Contents 目录（无 Rich）。

复用旧 _rendering/_special.py 的树形连接符逻辑（_build_toc_connectors），
输出为 AnsiLine。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from .inline import render_inline

_STYLE_HEADER = Style(fg=45, bold=True)          # 目录标题
_STYLE_CONN = Style(fg=238)                      # 树形连接符
_STYLE_L1 = Style(fg=220, bold=True)             # 一级标题
_STYLE_L2 = Style(fg=45, bold=True)              # 二级标题
_STYLE_L3 = Style(fg=242, bold=True, dim=True)   # 三级+标题


def _build_toc_connectors(toc: list[dict]) -> list[dict]:
    """构建 TOC 树形前缀（与旧 _build_toc_connectors 逐字符一致）。"""
    if not toc:
        return []
    n = len(toc)
    is_last = [True] * n
    for i in range(n - 1):
        level_i = toc[i]["level"]
        for j in range(i + 1, n):
            if toc[j]["level"] == level_i:
                is_last[i] = False
                break
            elif toc[j]["level"] < level_i:
                break

    ancestors_active: dict[int, bool] = {}
    result: list[dict] = []
    for i, entry in enumerate(toc):
        level = entry["level"]
        prefix_parts: list[str] = []
        for l in range(1, level):
            if ancestors_active.get(l, False):
                prefix_parts.append("\u2503  ")
            else:
                prefix_parts.append("   ")
        connector = "\u2517\u2501 " if is_last[i] else "\u2523\u2501 "
        prefix_parts.append(connector)
        ancestors_active[level] = not is_last[i]
        for l in list(ancestors_active.keys()):
            if l > level:
                del ancestors_active[l]
        result.append({**entry, "prefix": "".join(prefix_parts)})
    return result


def render_toc(toc: list[dict], output_width: int) -> list[AnsiLine]:
    """渲染 Table of Contents 为 AnsiLine 列表（空 toc 返回 []）。"""
    if not toc:
        return []
    lines: list[AnsiLine] = []
    prefix = "\u250c\u2500 \u76ee\u5f55 \u2500"
    remaining = max(3, output_width - _vwidth(prefix) - 1)
    lines.append(AnsiLine.of(prefix + "\u2500" * remaining + "\u2510", _STYLE_HEADER))

    for entry in _build_toc_connectors(toc):
        level = entry["level"]
        if level == 1:
            style = _STYLE_L1
        elif level == 2:
            style = _STYLE_L2
        else:
            style = _STYLE_L3
        line = AnsiLine.of(entry["prefix"], _STYLE_CONN)
        for run in render_inline(entry["text"], style):
            line.append_run(run)
        lines.append(line)

    close_width = _vwidth("\u2514\u2518")
    remaining = max(0, output_width - close_width)
    lines.append(AnsiLine.of("\u2514" + "\u2500" * remaining + "\u2518", _STYLE_CONN))
    return lines


def _vwidth(s: str) -> int:
    from src.renderer._utils import cjk_display_width
    return cjk_display_width(s)


__all__ = ["render_toc"]
