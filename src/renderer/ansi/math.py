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

from typing import Any

from .style import Style
from .helpers import AnsiLine, visual_width
from ._math_latex import render_math_box
from ._math_box import center_line
from ._math_style import _M_TAG
from ._math_macros import MacroState

#: 块级公式框的默认标签（简洁——框头保持清爽；``label`` 仅在特殊场景覆盖）
_LABEL_DEFAULT = "公式"

_STYLE_LABEL = Style(fg=45, bold=True)
_STYLE_BORDER = Style(fg=240)
_STYLE_OMITTED = Style(fg=240)

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


def _cached(source: str, inline: bool, label: str = _LABEL_DEFAULT,
            dropped: int = 0):
    key = _cache_key(source, inline, label, dropped)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    if inline:
        box = render_math_box(source, inline=True, macro_state=_MACRO_STATE)
        value: Any = AnsiLine()
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


def _box_row(content, inner_w: int, style: Style | None = None,
             align: str = "center") -> AnsiLine:
    """框内一行（``│ 内容 │``）：内容在 ``inner_w`` 内按 ``align`` 定位。

    ``content`` 为 ``AnsiLine``（公式正文行）或 ``str``（提示 / 编号文本）。
    空行（无 run）也补齐为满宽空行，保证框线左右对齐。
    """
    src = AnsiLine.of(content, style) if isinstance(content, str) else content
    w = src.width
    pad = inner_w - w
    left = 0
    if pad > 0:
        if align == "right":
            left = pad
        elif align == "center":
            left = pad // 2
    line = AnsiLine.of("\u2502 ", _STYLE_BORDER)
    if left:
        line.append(" " * left)
    for run in src.runs:
        line.append_run(run)
    right = inner_w - left - w
    if right > 0:
        line.append(" " * right)
    line.append(" \u2502", _STYLE_BORDER)
    return line


def _block_head(label: str, inner_w: int) -> AnsiLine:
    """框顶行（``╭─ 标签 ─────╮``）：与内容行 / 底边等宽。"""
    head = AnsiLine.of(f"\u256d\u2500 {label} ", _STYLE_LABEL)
    fill = inner_w - visual_width(label) - 1
    if fill > 0:
        head.append("\u2500" * fill, _STYLE_BORDER)
    head.append("\u256e", _STYLE_BORDER)
    return head


def _inner_width(body: list[AnsiLine], label: str, texts: list[str]) -> int:
    """框内宽（内容区列数）：容纳正文、标签（顶边框）与附加文本。

    标签至少留 2 列余量（``╭─ 标签 ─╮`` 至少有 1 个填充横线）——修复前短公式
    （如 ``$x$``）的顶边比内容行宽、底边比内容行窄，圆形框左右不闭合。
    """
    inner = max((ln.width for ln in body), default=0)
    inner = max(inner, visual_width(label) + 2)
    for text in texts:
        inner = max(inner, visual_width(text))
    return inner


def _build_block_lines(source: str, label: str = _LABEL_DEFAULT,
                       dropped: int = 0) -> list[AnsiLine]:
    """块级公式 → 框线行（公式居中、标签在顶边、编号右对齐在框内）。

    - **内容居中**：公式按内宽居中（印刷习惯；修复前一律左贴框线，长宽不匹配
      的公式右侧留白突兀）；
    - **编号**：``\\tag{1}`` 右对齐到框内末行（公式视觉主体不被编号挤偏）；
    - **超长预览截断**：``dropped`` > 0 时正文前插入左对齐的省略提示行。
    """
    box = render_math_box(source or "", inline=False, macro_state=_MACRO_STATE)
    body = list(box.lines)
    omitted = int(dropped or 0)
    omitted_text = f"… 前 {omitted} 行省略" if omitted else ""
    tag = (box.tag or "").strip()
    if tag and len(body) == 1 and not body[0].runs:
        body = []      # 仅 ``\tag``（无公式内容）时不输出空内容行
    inner_w = _inner_width(body, label, [omitted_text, tag])

    lines: list[AnsiLine] = [_block_head(label, inner_w)]
    if omitted_text:
        lines.append(_box_row(omitted_text, inner_w, _STYLE_OMITTED,
                              align="left"))
    for ln in body:
        lines.append(_box_row(center_line(ln, inner_w), inner_w))
    if tag:
        lines.append(_box_row(tag, inner_w, _M_TAG, align="right"))

    lines.append(AnsiLine.of(
        "\u2570" + "\u2500" * (inner_w + 2) + "\u256f", _STYLE_BORDER))
    return lines


def render_math_block(source: str, label: str = _LABEL_DEFAULT,
                      dropped: int = 0) -> list[AnsiLine]:
    """渲染块级数学公式（二维排版 + 圆角框，内容居中）。"""
    return _cached(source or "", False, label, dropped)


def render_math_omitted(chars: int, label: str = _LABEL_DEFAULT) -> list[AnsiLine]:
    """超长公式的流式预览占位框（完整内容在本块结束后渲染）。

    二维排版成本与公式长度成正比；超长公式（> ``_MATH_PREVIEW_MAX_SRC``）
    流式期间每帧重排会把渲染线程占满，故预览降级为提示框——与代码块/表格
    预览的「省略提示」同一语义（预览有界、提交完整）。
    """
    msg = f"… 公式过长（{chars} 字符），本块结束后完整渲染"
    inner_w = _inner_width([], label, [msg])
    return [
        _block_head(label, inner_w),
        _box_row(msg, inner_w, _STYLE_OMITTED, align="left"),
        AnsiLine.of("\u2570" + "\u2500" * (inner_w + 2) + "\u256f",
                    _STYLE_BORDER),
    ]


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

    ``\\tag{1}`` 公式编号：行内无处右对齐，附在块末行末尾（`` (1)``，
    编号样式）——修复前编号随布局块传递、行内路径不消费，编号内容静默丢失。
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
    tag = (box.tag or "").strip()
    if tag and lines:
        last = AnsiLine()
        for run in lines[-1].runs:
            last.append_run(run)
        last.append(" " + tag, _M_TAG)
        lines[-1] = last
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
