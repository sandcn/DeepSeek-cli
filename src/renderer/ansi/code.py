"""代码块 — pygments 词法高亮 → 256 色 Style 行（无 Rich）。

复用：
  - ``renderer._rendering._code.get_lexer``（pygments lexer 缓存）
  - ``renderer._utils.get_code_style``（monokai 色板，去背景/文本样式）

映射：pygments token → Style（仅前景色，经 rgb_to_256 降级到 256 色体系）。
失败时降级为纯文本（dim）。
"""

from __future__ import annotations

import logging

from .style import Style
from .style import rgb_to_256
from .helpers import AnsiLine

_logger = logging.getLogger(__name__)

# 围栏/标题栏/降级样式
_STYLE_FENCE = Style(fg=242, dim=True, italic=True)
_STYLE_DIM = Style(fg=244)
_STYLE_TITLE = Style(fg=110, bold=True)
_STYLE_HIGHLIGHT_BG = Style(fg=221)

_CODE_THEME = "monokai"


def _hex_to_256(hex_color: str) -> int | None:
    """#RRGGBB → 256 色号（None 表示无前景色）。"""
    if not hex_color or not hex_color.startswith("#"):
        return None
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        return None
    return rgb_to_256(r, g, b)


def _highlight_line(line: str, lexer, pyg_style) -> AnsiLine:
    """单行代码词法高亮 → AnsiLine。

    pygments 2.20：样式存于 ``Style.styles``（token → '#RRGGBB' 字符串），
    无 ``get_style_for_token``。解析 hex → 256 色号。
    """
    aline = AnsiLine()
    try:
        for ttype, value in lexer.get_tokens(line):
            if not value:
                continue
            val = value.rstrip("\n")
            if not val:
                continue
            # ★ 修复（review 方向）：pygments token 为层级类型（如
            #   Comment.Single），styles 表常只定义基类型（Comment）——
            #   直接 get(ttype) 取不到样式（子类型无着色）。沿 parent 链
            #   向上查找最近定义的样式。
            fg = None
            t = ttype
            while t is not None:
                style_str = pyg_style.styles.get(t, "")
                fg = _hex_to_256(style_str)
                if fg is not None:
                    break
                t = getattr(t, "parent", None)
            aline.append(val, Style(fg=fg) if fg is not None else None)
        return aline
    except Exception:
        _logger.debug("代码高亮失败，降级纯文本", exc_info=True)
        return AnsiLine.of(line, _STYLE_DIM)


def render_code_block(
    source: str,
    lang: str = "",
    theme: str = _CODE_THEME,
    highlight_lines: list[int] | None = None,
    title: str = "",
) -> list[AnsiLine]:
    """渲染代码块（含标题栏与围栏）为 AnsiLine 列表。

    Args:
        source: 源码（多行，\\n 分隔）。
        lang: 语言名（可空）。
        theme: pygments 主题名。
        highlight_lines: 高亮行号（1-based）。
        title: 代码块标题（文件名等）。

    Returns:
        渲染后的行列表。
    """
    out: list[AnsiLine] = []
    from src.renderer._rendering._code import get_lexer
    from src.renderer._utils import get_code_style

    # 标题栏
    if title:
        label = f"┌─ {title}"
        out.append(AnsiLine.of(label, _STYLE_TITLE))

    # 打开围栏
    lang_tag = lang if lang and lang != "text" else ""
    fence = f"```{lang_tag}"
    fence_line = AnsiLine.of(fence, _STYLE_FENCE)
    if lang_tag:
        fence_line.append(f" [{lang}]", Style(fg=45, bold=True))
    out.append(fence_line)

    lexer = get_lexer(lang) if lang and lang != "text" else None
    hl = set(highlight_lines or [])
    if lexer is not None:
        # get_code_style 返回 pygments 样式类；_highlight_line 经 styles dict 取色
        pyg_style = get_code_style(theme)
        for idx, src_line in enumerate(source.split("\n"), start=1):
            aline = _highlight_line(src_line, lexer, pyg_style)
            if idx in hl:
                aline = _apply_highlight(aline)
            out.append(aline)
    else:
        # 无词法分析器：纯文本（dim）
        for idx, src_line in enumerate(source.split("\n"), start=1):
            aline = AnsiLine.of(src_line, _STYLE_DIM)
            if idx in hl:
                aline = _apply_highlight(aline)
            out.append(aline)

    # 关闭围栏
    out.append(AnsiLine.of("```", _STYLE_FENCE))
    return out


def _apply_highlight(line: AnsiLine) -> AnsiLine:
    """高亮行：叠加金色前缀标记。"""
    from .helpers import Run
    runs = [Run("\u25b8 ", _STYLE_HIGHLIGHT_BG)] + list(line.runs)
    return AnsiLine(runs)


def render_inline_code(text: str) -> AnsiLine:
    """内联代码 → AnsiLine（亮绿 + 粗体）。"""
    return AnsiLine.of(f" {text} ", Style(fg=46, bold=True))


__all__ = ["render_code_block", "render_inline_code"]
