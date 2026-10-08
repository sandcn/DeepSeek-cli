"""数学公式渲染 — LaTeX → Unicode/框线终端排版（零 Rich）。

TUI 内容路径的数学渲染入口：

  - ``render_math_block(source)``  — 块级公式（真二维排版 + 边框），供
    ``AnsiRenderEngine`` 处理 ``MATH_BLOCK_CLOSE``；
  - ``render_math_inline(source)`` — 行内公式（紧凑单行），供 ``inline``
    节点渲染使用。

实现委托 ``_math_latex``（解析 + ``_Box`` 布局）；本模块只负责外观（边框、
居中）与**有界结果缓存**（流式预览每次 write 会重渲未闭合数学块，源码未变
时直接复用，避免重复布局）。
"""

from __future__ import annotations

from .style import Style
from .helpers import AnsiLine
from ._math_latex import render_math_box
from ._math_macros import MacroState

_STYLE_LABEL = Style(fg=45, bold=True)
_STYLE_BORDER = Style(fg=238)
_STYLE_OMITTED = Style(fg=238)

#: 跨公式共享的宏状态（``\gdef`` 等全局定义持久到后续公式，与 KaTeX 一致）。
_MACRO_STATE = MacroState()

#: 数学渲染结果缓存：key → 渲染结果。块级缓存 list[AnsiLine]，行内缓存
#: 单个 AnsiLine。数学块/行内公式数量有限、源码重复率高（流式预览逐帧
#: 重渲），有界缓存使源码未变时零布局成本。超限整体清空（简单、无淘汰开销）。
#: 缓存键含宏状态版本号——全局宏变化会让展开结果变化，必须失效。
_CACHE: dict = {}
_CACHE_MAX = 512


def _cache_key(source: str, inline: bool, label: str = "",
               dropped: int = 0):
    return (source, inline, label, dropped, _MACRO_STATE.version)


def _cached(source: str, inline: bool, label: str = "数学公式", dropped: int = 0):
    key = _cache_key(source, inline, label, dropped)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    if inline:
        box = render_math_box(source, inline=True, macro_state=_MACRO_STATE)
        value: object = AnsiLine()
        for idx, ln in enumerate(box.lines):
            if idx:
                value.append(" ")
            for run in ln.runs:
                value.append_run(run)
    else:
        value = _build_block_lines(source, label, dropped)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = value
    return value


def _build_block_lines(source: str, label: str = "数学公式",
                       dropped: int = 0) -> list[AnsiLine]:
    box = render_math_box(source or "", inline=False, macro_state=_MACRO_STATE)
    body = box.lines
    inner_w = max((ln.width for ln in body), default=0)
    omitted = int(dropped or 0)
    if omitted:
        inner_w = max(inner_w, len(f"… 前 {omitted} 行省略"))

    head = AnsiLine.of(f"╭─ {label} ", _STYLE_LABEL)
    head_fill = inner_w + 2 - head.width
    if head_fill > 0:
        head.append("─" * head_fill, _STYLE_BORDER)
    head.append("╮", _STYLE_BORDER)

    lines: list[AnsiLine] = [head]
    if omitted:
        om = AnsiLine.of("│ ", _STYLE_BORDER)
        om.append(f"… 前 {omitted} 行省略", _STYLE_OMITTED)
        pad = inner_w - (om.width - 2)
        if pad > 0:
            om.append(" " * pad)
        om.append(" │", _STYLE_BORDER)
        lines.append(om)
    for ln in body:
        nl = AnsiLine.of("│ ", _STYLE_BORDER)
        for run in ln.runs:
            nl.append_run(run)
        pad = inner_w - ln.width
        if pad > 0:
            nl.append(" " * pad)
        nl.append(" │", _STYLE_BORDER)
        lines.append(nl)

    lines.append(AnsiLine.of("╰" + "─" * (inner_w + 2) + "╯", _STYLE_BORDER))
    return lines


def render_math_block(source: str, label: str = "数学公式",
                      dropped: int = 0) -> list[AnsiLine]:
    """渲染块级数学公式（二维排版 + 边框）。

    Args:
        source: LaTeX 源码（不含 ``$$`` / ``\\[`` 定界符）。
        label: 边框标签（默认「数学公式」）。
        dropped: 预览截断丢弃的行数（>0 时正文前插入省略提示行）。

    Returns:
        AnsiLine 列表（首行标题、正文居中、末行边框）。
    """
    return _cached(source or "", False, label, dropped)


def render_math_omitted(chars: int, label: str = "数学公式") -> list[AnsiLine]:
    """超长公式的流式预览占位框（完整内容在本块结束后渲染）。

    二维排版成本与公式长度成正比；超长公式（> ``_MATH_PREVIEW_MAX_SRC``）
    流式期间每帧重排会把渲染线程占满，故预览降级为提示框——与代码块/表格
    预览的「省略提示」同一语义（预览有界、提交完整）。
    """
    msg = f"… 公式过长（{chars} 字符），本块结束后完整渲染"
    om = AnsiLine.of("│ ", _STYLE_BORDER)
    om.append(msg, _STYLE_OMITTED)
    inner_w = max(0, om.width - 2)
    om.append(" │", _STYLE_BORDER)
    head = AnsiLine.of(f"╭─ {label} ", _STYLE_LABEL)
    head_fill = inner_w + 2 - head.width
    if head_fill > 0:
        head.append("─" * head_fill, _STYLE_BORDER)
    head.append("╮", _STYLE_BORDER)
    bottom = AnsiLine.of("╰" + "─" * (inner_w + 2) + "╯", _STYLE_BORDER)
    return [head, om, bottom]


def render_math_inline(source: str) -> AnsiLine:
    """渲染行内数学公式为单行（多行布局展平为空格连接）。"""
    return _cached(source or "", True)


def render_math_inline_block(source: str) -> tuple[list[AnsiLine], int]:
    """渲染行内数学公式为**二维多行块**，返回 ``(行列表, 基线行号)``。

    行内公式使用与块级相同的二维排版（分数堆叠 / 根式 / 大算符上下限 /
    矩阵 / 上下标注），供 ``inline.inline_lines`` 与周围文本按基线水平拼接
    ——修复前行内公式一律展平为单行（``a⁄b`` / ``∑ⁿᵢ₌₁``），复杂公式在行内
    完全失去二维结构。

    单行内容（如 ``x^2``）返回单行块，调用方可直接当普通文本处理。
    """
    key = ("inline_block", source or "", _MACRO_STATE.version)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    box = render_math_box(source or "", inline=False, macro_state=_MACRO_STATE)
    lines: list[AnsiLine] = []
    for ln in box.lines:
        nl = AnsiLine()
        for run in ln.runs:
            nl.append_run(run)
        lines.append(nl)
    value = (lines, box.baseline)
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.clear()
    _CACHE[key] = value
    return value


def clear_math_cache() -> None:
    """清空数学渲染缓存与宏状态（测试 / 主题切换 / 会话重置用）。"""
    global _MACRO_STATE
    _CACHE.clear()
    _MACRO_STATE = MacroState()


__all__ = [
    "render_math_block", "render_math_inline", "render_math_inline_block",
    "render_math_omitted", "clear_math_cache",
]
