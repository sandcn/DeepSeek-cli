"""codeblock — CodeBlock 代码块展示控件（AI 聊天 TUI 实用标准控件）。

React Ink 生态 ``<CodeBlock>`` 风格代码块：顶边框（可选语言标签标题栏）+
代码行 + 底边框 + 可选行号。基于标准 BOX border 布局 + TEXT 行，无第三方
依赖。

控件语义（与 React Ink 展示控件一致）：
  - ``code`` 含 ``\\n`` 时按行拆分（Line 内嵌换行符破坏帧行号——见
    FrameBuilder.append 的 BUG-34 同族处理）；
  - 超宽行截断至可用内宽（不破坏行级 diff 宽度不变量；截断不拆 CJK）；
  - 行号栏与代码行对齐（行号右对齐 + 竖线分隔），展示用途不参与复制。

宽度语义（行宽不变量：所有行宽 <= 显式 width 或内容自适应）：
  - 显式 ``width``：标题栏/代码行/底边框统一对齐 width（行号栏计入内宽）。
  - 无 ``width``：内容自适应——顶/底边框 = 内容宽 + 2（行号栏计入）。
"""

from __future__ import annotations

import logging

from src.tui.core.style import Style
from src.tui._width import wcswidth_simple
from ..element import TEXT, Element, h
from ..helpers import _parse_color
from ..widgets.layout import Row, Column

_logger = logging.getLogger(__name__)

__all__ = ["CodeBlock"]

#: 边框字符（single/double/round/bold/classic）：(左上, 右上, 左下, 右下, 横线, 竖线)
_BORDER_CHARS: dict[str, tuple[str, str, str, str, str, str]] = {
    "single": ("\u250c", "\u2510", "\u2514", "\u2518", "\u2500", "\u2502"),   # ┌ ┐ └ ┘ ─ │
    "double": ("\u2554", "\u2557", "\u255a", "\u255d", "\u2550", "\u2551"),   # ╔ ╗ ╚ ╝ ═ ║
    "round": ("\u256d", "\u256e", "\u2570", "\u256f", "\u2500", "\u2502"),    # ╭ ╮ ╰ ╯ ─ │
    "bold": ("\u250f", "\u2513", "\u2517", "\u251b", "\u2501", "\u2503"),     # ┏ ┓ ┗ ┛ ━ ┃
    "classic": ("+", "+", "+", "+", "-", "|"),
}
_DEFAULT_BORDER = ("\u250c", "\u2510", "\u2514", "\u2518", "\u2500", "\u2502")


def _color(value, default: int = 23) -> int:
    """解析颜色 shorthand（颜色名/int）为 256 色号；解析失败回退 default。"""
    if value is None:
        return default
    parsed = _parse_color(value)
    return parsed if parsed is not None else default


def _repeat(ch: str, n: int) -> str:
    """将字符重复到目标显示宽度（宽字符按显示宽度换算）。"""
    if n <= 0:
        return ""
    cw = wcswidth_simple(ch)
    if cw <= 1:
        return ch * n
    return ch * max(0, n // cw)


def _truncate_to_width(text: str, max_w: int) -> str:
    """按显示宽度截断（不拆 CJK；超宽时末尾补省略号）。"""
    if max_w <= 0:
        return ""
    if wcswidth_simple(text) <= max_w:
        return text
    w = 0
    out = []
    for ch in text:
        cw = wcswidth_simple(ch)
        if w + cw > max_w - 1:
            break
        out.append(ch)
        w += cw
    return "".join(out) + "\u2026"


def CodeBlock(props: dict) -> Element:
    """代码块展示控件。

    Props:
        code: 代码内容（str；含 ``\\n`` 时按行拆分）。
        language: 语言标签（顶边框标题栏显示；None 时用 title）。
        title: 标题（language 为空时回退；均空则顶边框无标签）。
        borderStyle: 边框变体（single/double/round/bold/classic）。
        borderColor: 边框颜色（颜色名/int；默认 23 暗青）。
        lineNumbers: 是否显示行号（默认 False）。
        wrap: 超宽行是否换行（默认 False 截断省略号；True 时按容器换行）。
        highlightStyle: 代码文本样式（默认 None）。
        width: 显示宽度（None 时内容自适应；顶/底边框随内容宽度）。
        style: 边框完整样式（``borderColor`` 覆盖 style.fg）。

    Returns:
        Column 元素（顶边框 + 代码行 + 底边框；无标题时顶边框为纯横线）。
    """
    code = props.get("code")
    code = "" if code is None else str(code)
    language = props.get("language")
    language = None if language is None else str(language)
    title = props.get("title")
    title = None if title is None else str(title)
    label = language or title
    border_style = str(props.get("borderStyle", "single"))
    chars = _BORDER_CHARS.get(border_style, _DEFAULT_BORDER)
    border = Style(fg=_color(props.get("borderColor"), 23))
    base_style = props.get("style")
    if base_style is not None:
        border = base_style.merge(border)
    show_lines = bool(props.get("lineNumbers", False))
    wrap = bool(props.get("wrap", False))
    highlight_style = props.get("highlightStyle")
    width = props.get("width")
    if width is not None:
        try:
            width = max(1, int(width))
        except (TypeError, ValueError, OverflowError):
            width = None

    lines = code.split("\n") if code else [""]

    # ── 行号栏宽度 ──
    num_w = 0
    if show_lines:
        num_w = max(2, len(str(len(lines))))
    # 行号栏 + 竖线 + 空格的总前缀宽度（`  1│ ` = num_w + 2）
    num_prefix_w = num_w + 2 if show_lines else 0

    # ── 有效宽度（显式或内容自适应） ──
    content_w = max((wcswidth_simple(l) for l in lines), default=0)
    if width is not None:
        width_eff = width
    else:
        # 内容自适应：内容宽 + 行号栏 + 左右竖线/间距（各 2 列）
        width_eff = num_prefix_w + content_w + 4

    children: list[Element] = []

    # ── 顶边框（含可选语言标签） ──
    if label:
        label_text = f" {label} "
        label_w = wcswidth_simple(label_text)
        fill = max(0, width_eff - 3 - label_w)
        children.append(
            h(TEXT, {
                "children": chars[0] + _repeat(chars[4], 1) + label_text
                            + _repeat(chars[4], fill) + chars[1],
                "style": border,
            })
        )
    else:
        children.append(
            h(TEXT, {
                "children": chars[0] + _repeat(chars[4], max(0, width_eff - 2)) + chars[1],
                "style": border,
            })
        )
    # ── 代码行 ──
    # 内容预算 = 总宽 - 左右边框/竖线/间距（无行号 4：`│ ` 2 + ` │` 2；
    # 有行号 4：行号竖线 `│ ` 2 + 右侧 ` │` 2——行号栏宽度已在 num_prefix_w）
    inner_w = max(1, width_eff - num_prefix_w - 4)
    for i, line in enumerate(lines):
        content = line if line else ""
        if not wrap:
            content = _truncate_to_width(content, inner_w)
        code_runs: list[Element] = []
        if show_lines:
            num_text = f"{i + 1:>{num_w}}"
            code_runs.append(h(TEXT, {"children": num_text, "style": Style(fg=240)}))
            code_runs.append(h(TEXT, {"children": "\u2502 ", "style": border}))
        else:
            code_runs.append(h(TEXT, {"children": "\u2502 ", "style": border}))
        if highlight_style is not None:
            code_runs.append(h(TEXT, {"children": content, "style": highlight_style}))
        else:
            code_runs.append(h(TEXT, {"children": content}))
        # 右侧边框（无行号时补右侧；有行号时行号栏已占左侧，右侧补竖线）
        code_runs.append(h(TEXT, {"children": " \u2502", "style": border}))
        children.append(h(Row, None, code_runs))
    # ── 底边框 ──
    children.append(
        h(TEXT, {
            "children": chars[2] + _repeat(chars[4], max(0, width_eff - 2)) + chars[3],
            "style": border,
        })
    )
    return h(Column, None, children)
