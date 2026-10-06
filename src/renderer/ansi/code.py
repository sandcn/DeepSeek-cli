"""代码块 — pygments 词法高亮 → 256 色 Style 行（无 Rich）。

复用：
  - ``renderer._rendering._code.get_lexer``（pygments lexer 缓存）
  - ``renderer._utils.get_code_style``（monokai 色板，去背景/文本样式）

映射：pygments token → Style（仅前景色，经 rgb_to_256 降级到 256 色体系）。
失败时降级为纯文本（dim）。

结构拆分（供流式预览增量复用）：
  - ``highlight_code_lines`` — 逐行高亮（无围栏/标题），流式预览按行缓存、
    仅渲染新增行；
  - ``render_fence_line`` / ``render_close_fence_line`` / ``render_title_line``
    — 围栏与标题行构造（整块渲染与预览共用，避免两处实现漂移）；
  - ``render_code_block`` — 组装整块（围栏 + 行 + 围栏），支持
    ``continuation``（被强制刷出的续段：不重复打开围栏/标题，仅补关闭围栏）。
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
_STYLE_OMITTED = Style(fg=238)

_CODE_THEME = "monokai"

#: hex 颜色 → 256 色号缓存（主题颜色集合有限，避免重复解析 hex + 搜色板）
_HEX_256_CACHE: dict = {}
#: fg 色号 → Style 对象缓存（同色 Style 共享，避免每 token 重建 frozen dataclass）
_FG_STYLE_CACHE: dict = {}


def _hex_to_256(hex_color: str) -> int | None:
    """#RRGGBB → 256 色号（None 表示无前景色，带结果缓存）。"""
    if not hex_color or not hex_color.startswith("#"):
        return None
    if hex_color in _HEX_256_CACHE:
        return _HEX_256_CACHE[hex_color]
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
    except (ValueError, IndexError):
        _HEX_256_CACHE[hex_color] = None
        return None
    result = rgb_to_256(r, g, b)
    _HEX_256_CACHE[hex_color] = result
    return result


def _fg_style(fg: int | None) -> "Style | None":
    """256 色号 → Style（带缓存；None 表示无前景色）。"""
    if fg is None:
        return None
    style = _FG_STYLE_CACHE.get(fg)
    if style is None:
        style = Style(fg=fg)
        _FG_STYLE_CACHE[fg] = style
    return style


def _highlight_line(line: str, lexer, pyg_style) -> AnsiLine:
    """单行代码词法高亮 → AnsiLine。

    pygments 2.20：样式存于 ``Style.styles``（token → '#RRGGBB' 字符串），
    无 ``get_style_for_token``。解析 hex → 256 色号（带缓存）。
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
            aline.append(val, _fg_style(fg))
        return aline
    except Exception:
        _logger.debug("代码高亮失败，降级纯文本", exc_info=True)
        return AnsiLine.of(line, _STYLE_DIM)


def highlight_code_lines(
    lines: list[str],
    lang: str = "",
    theme: str = _CODE_THEME,
    highlight_lines: list[int] | None = None,
    start_index: int = 1,
) -> list[AnsiLine]:
    """逐行高亮代码（不含围栏/标题），返回 AnsiLine 列表。

    Args:
        lines: 源码行列表（不含换行符）。
        lang: 语言名（空/``text`` 时降级为纯文本 dim）。
        theme: pygments 主题名。
        highlight_lines: 需高亮的行号（1-based，相对 ``start_index``）。
        start_index: 首行对应的逻辑行号（供流式预览增量渲染时续接行号）。

    Returns:
        与 ``lines`` 等长的 AnsiLine 列表。
    """
    from src.renderer._rendering._code import get_lexer
    from src.renderer._utils import get_code_style

    if not lines:
        return []
    lexer = get_lexer(lang) if lang and lang != "text" else None
    hl = set(highlight_lines or [])
    pyg_style = get_code_style(theme) if lexer is not None else None
    out: list[AnsiLine] = []
    for offset, src_line in enumerate(lines):
        idx = start_index + offset
        if lexer is not None:
            aline = _highlight_line(src_line, lexer, pyg_style)
        else:
            aline = AnsiLine.of(src_line, _STYLE_DIM)
        if idx in hl:
            aline = _apply_highlight(aline)
        out.append(aline)
    return out


def render_fence_line(lang: str = "") -> AnsiLine:
    """代码块打开围栏行（``\u0060\u0060\u0060lang [lang]``）。"""
    lang_tag = lang if lang and lang != "text" else ""
    line = AnsiLine.of(f"```{lang_tag}", _STYLE_FENCE)
    if lang_tag:
        line.append(f" [{lang}]", Style(fg=45, bold=True))
    return line


def render_close_fence_line() -> AnsiLine:
    """代码块关闭围栏行。"""
    return AnsiLine.of("```", _STYLE_FENCE)


def render_title_line(title: str) -> AnsiLine:
    """代码块标题行（``┌─ title``）。"""
    return AnsiLine.of(f"┌─ {title}", _STYLE_TITLE)


def render_omitted_line(omitted: int) -> AnsiLine:
    """预览截断提示行（超长块只预览最近若干行时的可见说明）。"""
    return AnsiLine.of(f"\u2026 前 {omitted} 行省略（本块结束后完整显示）",
                       _STYLE_OMITTED)


def render_code_block(
    source: str,
    lang: str = "",
    theme: str = _CODE_THEME,
    highlight_lines: list[int] | None = None,
    title: str = "",
    closed: bool = True,
    continuation: bool = False,
) -> list[AnsiLine]:
    """渲染代码块（含标题栏与围栏）为 AnsiLine 列表。

    Args:
        source: 源码（多行，\\n 分隔）。
        lang: 语言名（可空）。
        theme: pygments 主题名。
        highlight_lines: 高亮行号（1-based）。
        title: 代码块标题（文件名等）。
        closed: 代码块是否已闭合。流式预览未闭合块时传 False——不渲染
            伪造的关闭围栏（```）。
        continuation: 是否为「被强制刷出的续段」——True 时不重复渲染标题栏
            与打开围栏（逻辑上仍是同一个代码块，仅因缓冲上限分段输出）。

    Returns:
        渲染后的行列表。
    """
    out: list[AnsiLine] = []
    if title and not continuation:
        out.append(render_title_line(title))
    if not continuation:
        out.append(render_fence_line(lang))
    lines = source.split("\n") if source else []
    out.extend(highlight_code_lines(lines, lang, theme, highlight_lines))
    if closed:
        out.append(render_close_fence_line())
    return out


def _apply_highlight(line: AnsiLine) -> AnsiLine:
    """高亮行：叠加金色前缀标记。"""
    from .helpers import Run
    runs = [Run("\u25b8 ", _STYLE_HIGHLIGHT_BG)] + list(line.runs)
    return AnsiLine(runs)


def render_inline_code(text: str) -> AnsiLine:
    """内联代码 → AnsiLine（亮绿 + 粗体）。"""
    return AnsiLine.of(f" {text} ", Style(fg=46, bold=True))


__all__ = [
    "highlight_code_lines",
    "render_fence_line",
    "render_close_fence_line",
    "render_title_line",
    "render_omitted_line",
    "render_code_block",
    "render_inline_code",
]
