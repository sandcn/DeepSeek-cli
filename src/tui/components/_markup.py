"""行内标记解析器 — Markup

提供简单的行内标记解析功能，将标记文本解析为 :class:`StyledText` 片段列表，
或直接渲染为 ANSI 字符串。

支持的标记语法：

  - ``[bold]text[/]``       → 加粗
  - ``[dim]text[/]``        → 暗淡
  - ``[italic]text[/]``     → 斜体
  - ``[color=N]text[/]``    → 前景色 N（256 色号）
  - ``[fg=N]text[/]``       → 前景色 N（color 别名）
  - ``[bg=N]text[/]``       → 背景色 N

标记可嵌套：``[bold][color=45]text[/][/]``
未闭合标记宽容处理：保留原文本不变。
未知标记宽容处理：保留原文本不变。

设计模式: 解释器 (Interpreter) — 标记语法构成小型领域语言，
使用栈式解释器逐 token 解析。
"""

from __future__ import annotations

from ..core.style import Style, StyledText


__all__: list[str] = [
    "parse_markup",
    "render_markup",
]


# ═══════════════════════════════════════════════════════════
# 标记解析器常量
# ═══════════════════════════════════════════════════════════

# 支持的样式标记名（无参数），值对应 Style 属性名
_STYLE_TAGS: dict[str, str] = {
    "bold": "bold",
    "dim": "dim",
    "italic": "italic",
}

# 带参数的标记名集合（需解析 =N 参数）
_PARAM_TAGS: set[str] = {"color", "fg", "bg"}

# 颜色参数值范围
_COLOR_MIN: int = 0
_COLOR_MAX: int = 255


def _is_int_str(s: str) -> bool:
    """检查字符串是否为有效的整数表示（含可选负号）。

    Args:
        s: 待检查的字符串。

    Returns:
        字符串可解析为 int 时返回 True。
    """
    if not s:
        return False
    # 允许前导负号
    if s[0] == '-':
        return s[1:].isdigit() if len(s) > 1 else False
    return s.isdigit()


# ═══════════════════════════════════════════════════════════
# 标记平衡检查
# ═══════════════════════════════════════════════════════════


def _is_balanced(text: str) -> bool:
    """检查标记是否平衡（所有开标记都有对应的闭标记）。

    只统计有效标记（已知的样式标记和颜色参数标记），
    未知标记不纳入统计。纯文本无标记时也视为平衡。

    Args:
        text: 待检查的文本。

    Returns:
        标记平衡返回 True，有未闭合标记返回 False。
    """
    balance: int = 0
    i: int = 0
    n: int = len(text)

    while i < n:
        if text[i] != '[':
            i += 1
            continue

        close_bracket: int = text.find(']', i + 1)
        if close_bracket == -1:
            i += 1
            continue

        tag: str = text[i + 1:close_bracket]

        if tag == '/':
            balance -= 1
        elif tag.strip().lower() in _STYLE_TAGS:
            balance += 1
        elif '=' in tag:
            tag_parts: list[str] = tag.split('=', 1)
            tag_name: str = tag_parts[0].strip().lower()
            tag_val: str = tag_parts[1].strip()
            if tag_name in _PARAM_TAGS and _is_int_str(tag_val.strip()):
                balance += 1
            # 值非整数的参数标记不计数（视为未知标记）
        # 未知标记不计数

        i = close_bracket + 1

    return balance == 0


# ═══════════════════════════════════════════════════════════
# parse_markup — 解析行内标记
# ═══════════════════════════════════════════════════════════


def parse_markup(text: str) -> list[StyledText]:
    """解析行内标记为 :class:`StyledText` 片段列表。

    使用栈式解析器逐字符扫描，支持标记嵌套。
    未知标记和未闭合标记宽容处理，保留原文本不变。

    Args:
        text: 包含行内标记的文本。

    Returns:
        StyledText 片段列表。无标记时返回包含单一无样式片段的列表。
        空文本返回空列表。
    """
    if not text:
        return []

    # 预检：标记未闭合时直接返回原文本（宽容处理）
    if not _is_balanced(text):
        return [StyledText(text, None)]

    segments: list[StyledText] = []
    style_stack: list[Style] = [Style()]  # 栈底为无样式
    buf: list[str] = []  # 当前文本缓冲区
    i: int = 0
    n: int = len(text)

    def _flush() -> None:
        """将缓冲区内容以当前栈顶样式追加为 StyledText 片段。"""
        if buf:
            content = "".join(buf)
            current_style = style_stack[-1]
            if current_style:
                segments.append(StyledText(content, current_style))
            else:
                segments.append(StyledText(content, None))
            buf.clear()

    while i < n:
        ch: str = text[i]

        if ch == '[' and i + 1 < n:
            # 尝试解析标记：找到对应的 ]
            close_bracket: int = text.find(']', i + 1)
            if close_bracket == -1:
                # 未闭合的 '['，当作普通文本
                buf.append(ch)
                i += 1
                continue

            tag_content: str = text[i + 1:close_bracket]

            # ── 处理闭合标记 [/] ──
            if tag_content == '/':
                if len(style_stack) > 1:
                    _flush()
                    style_stack.pop()
                else:
                    # 栈底不能弹出，当作普通文本输出
                    buf.append(ch)
                    i += 1
                    continue
                i = close_bracket + 1
                continue

            # ── 处理无参数标记 [bold] / [dim] / [italic] ──
            tag_lower: str = tag_content.strip().lower()
            if tag_lower in _STYLE_TAGS:
                _flush()
                attr: str = _STYLE_TAGS[tag_lower]
                new_style: Style = _apply_style_attr(style_stack[-1], attr)
                style_stack.append(new_style)
                i = close_bracket + 1
                continue

            # ── 处理带参数标记 [color=N] / [fg=N] / [bg=N] ──
            if '=' in tag_content:
                tag_name: str
                _: str
                tag_value: str
                tag_name, _, tag_value = tag_content.partition('=')
                tag_name = tag_name.strip().lower()
                tag_value_stripped: str = tag_value.strip()
                if tag_name in _PARAM_TAGS and _is_int_str(tag_value_stripped):
                    _flush()
                    color_val: int = max(
                        _COLOR_MIN,
                        min(_COLOR_MAX, int(tag_value_stripped)),
                    )
                    new_style = _apply_color_attr(style_stack[-1], tag_name, color_val)
                    style_stack.append(new_style)
                    i = close_bracket + 1
                    continue

            # 未知标记 / 非法格式，直接输出为普通文本（保留原样）
            buf.append(ch)
            i += 1
        else:
            buf.append(ch)
            i += 1

    # 刷新剩余文本缓冲区
    _flush()

    return segments


# ═══════════════════════════════════════════════════════════
# render_markup — 便捷渲染函数
# ═══════════════════════════════════════════════════════════


def render_markup(text: str) -> str:
    """解析行内标记并直接渲染为 ANSI 字符串。

    便捷函数，等价于调用 :func:`parse_markup` 后遍历调用
    :meth:`StyledText.render` 拼接结果。

    Args:
        text: 包含行内标记的文本。

    Returns:
        渲染后的 ANSI 字符串。无标记时返回原文本（零开销）。
    """
    segments: list[StyledText] = parse_markup(text)
    if not segments:
        return text
    return "".join(seg.render() for seg in segments)


# ═══════════════════════════════════════════════════════════
# 内部辅助函数
# ═══════════════════════════════════════════════════════════


def _apply_style_attr(current: Style, attr: str) -> Style:
    """对当前样式应用无参数样式属性（bold / dim / italic）。

    返回新 Style 实例，不修改原实例。

    Args:
        current: 当前样式。
        attr: 属性名（"bold" / "dim" / "italic"）。

    Returns:
        应用后的新 Style 实例。
    """
    kwargs: dict[str, bool] = {attr: True}
    return Style(
        fg=current.fg,
        bg=current.bg,
        bold=kwargs.get("bold", current.bold),
        italic=kwargs.get("italic", current.italic),
        dim=kwargs.get("dim", current.dim),
        underline=current.underline,
    )


def _apply_color_attr(current: Style, tag_name: str, color_val: int) -> Style:
    """对当前样式应用颜色属性（color / fg / bg）。

    返回新 Style 实例，不修改原实例。

    Args:
        current: 当前样式。
        tag_name: 标记名（"color" / "fg" / "bg"）。
        color_val: 256 色号，已 clamp 到 [0, 255]。

    Returns:
        应用后的新 Style 实例。
    """
    if tag_name in ("color", "fg"):
        return Style(
            fg=color_val,
            bg=current.bg,
            bold=current.bold,
            italic=current.italic,
            dim=current.dim,
            underline=current.underline,
        )
    # bg
    return Style(
        fg=current.fg,
        bg=color_val,
        bold=current.bold,
        italic=current.italic,
        dim=current.dim,
        underline=current.underline,
    )
