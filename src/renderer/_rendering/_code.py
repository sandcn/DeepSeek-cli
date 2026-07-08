"""_rendering._code — 代码块渲染：围栏/高亮/词法分析器。"""

from __future__ import annotations

import functools
import logging
import threading

_logger = logging.getLogger(__name__)

from rich.text import Text
from rich.style import Style
from rich.syntax import Syntax

from .._utils import cjk_display_width, get_code_style


# ═══════════════════════════════════════════════════════════
# 代码块标题栏
# ═══════════════════════════════════════════════════════════

def render_code_title_bar(title: str, lang: str, output_width: int) -> Text:
    """渲染代码块标题栏（┌─ 文件名 ────────────────┐）。"""
    prefix = "┌─ " + title + " ─"
    remaining = output_width - cjk_display_width(prefix) - 1
    if remaining < 3:
        remaining = 3
    title_bar = prefix + "─" * remaining + "┐"
    return Text(title_bar, style=Style(color="bright_black", dim=True))


# ═══════════════════════════════════════════════════════════
# 代码块围栏打开/关闭标记
# ═══════════════════════════════════════════════════════════

def render_code_fence_open(lang: str, indented: bool = False,
                           attrs: str = "") -> Text:
    """渲染代码块打开标记（```lang 或 📄 code），语言名附加 [lang] 徽章。"""
    if indented:
        fence_text = "📄 " + (lang if lang and lang != "text" else "code")
        return Text(fence_text, style=Style(color="bright_black", dim=True, italic=True))
    lang_tag = lang if lang and lang != "text" else ""
    fence_parts = [f"```{lang_tag}"]
    if attrs:
        fence_parts.append(f" {attrs.lstrip()}")
    fence_text = "".join(fence_parts)
    t = Text(fence_text, style=Style(dim=True, italic=True))
    if lang and lang != "text":
        t.append(f" [{lang}]", style=Style(bold=True, color="bright_cyan"))
    return t


def render_code_fence_close(indented: bool = False) -> Text:
    """渲染代码块关闭标记（``` 或 📄）。

    注意：indented=True 时需与 render_code_fence_open(indented=True)
    样式一致（bright_black, dim, italic），否则开闭标记视觉不对称。
    """
    if indented:
        fence_text = "📄"
        return Text(fence_text, style=Style(color="bright_black", dim=True, italic=True))
    fence_text = "```"
    return Text(fence_text, style=Style(dim=True, italic=True))


def render_code_block_syntax(source: str, lang: str, code_theme: str,
                              highlight_lines: list[int] | None = None) -> Text | Syntax:
    """使用 Rich Syntax 整块高亮代码。失败时降级为纯文本。"""
    try:
        return Syntax(
            source,
            lang,
            theme=get_code_style(code_theme),
            line_numbers=False,
            highlight_lines=set(highlight_lines or []),
            word_wrap=False,
            background_color="default",
        )
    except Exception:
        _logger.warning("代码块 Syntax 渲染失败，降级为纯文本: lang=%s", lang, exc_info=True)
        return Text(source, style=Style(dim=True))


# ═══════════════════════════════════════════════════════════
# 代码行语法高亮
# ═══════════════════════════════════════════════════════════

@functools.lru_cache(maxsize=256)
def _build_highlight_style(color, bold: bool, italic: bool, underline: bool, strike: bool) -> Style:
    """构建带缓存的语法高亮 Style（消除 highlight_line 每次调用的 Style 对象分配）。"""
    return Style(
        color=color,
        bgcolor=None,
        bold=bold,
        italic=italic,
        underline=underline,
        strike=strike,
    )


def highlight_line(line: str, lexer, theme) -> Text:
    """对单行代码进行语法高亮，返回 Rich Text。"""
    if not line:
        return Text()
    try:
        code_text = Text()
        for ttype, value in lexer.get_tokens(line):
            if not value:
                continue
            val = value.rstrip('\n')
            if not val:
                continue
            style = theme.get_style_for_token(ttype)
            cached = _build_highlight_style(
                style.color,
                bool(style.bold),
                bool(style.italic),
                bool(style.underline),
                bool(style.strike),
            )
            code_text.append(val, style=cached)
        return code_text
    except Exception:
        return Text(line)


# ═══════════════════════════════════════════════════════════
# 共享词法分析器缓存
# ═══════════════════════════════════════════════════════════

_LEXER_CACHE: dict[str, object] = {}
"""全局词法分析器缓存。键为语言名，值为 Pygments Lexer 实例。"""

_lexer_lock = threading.Lock()
"""保护 _LEXER_CACHE 写入的锁（读取无需持锁，Python GIL 保证 dict 基本操作安全）。"""


def get_lexer(lang: str) -> object:
    """获取/缓存 Pygments 词法分析器。

    全局共享缓存，所有渲染路径统一调用此函数。

    Args:
        lang: 语言名称

    Returns:
        Pygments Lexer 实例。失败时返回 None。
    """
    from pygments.lexers import get_lexer_by_name

    if lang not in _LEXER_CACHE:
        with _lexer_lock:
            # 双重检查：锁内再次判断，防止并发重复创建
            if lang not in _LEXER_CACHE:
                try:
                    _LEXER_CACHE[lang] = get_lexer_by_name(lang, stripnl=False)
                except Exception:
                    _logger.warning("词法分析器获取失败: lang=%s", lang, exc_info=True)
                    return None
    return _LEXER_CACHE[lang]


# ═══════════════════════════════════════════════════════════
# 样式常量
# ═══════════════════════════════════════════════════════════

_STYLE_CODE_INFO = Style(dim=True, italic=True)
"""代码块围栏样式常量。"""

_STYLE_CODE_LINE_DIM = Style(dim=True)
"""代码行降级样式常量。"""

_STYLE_INLINE_CODE = Style(color="bright_green", bgcolor="grey15", bold=True)
"""内联代码样式常量：亮绿色文字 + 暗灰背景 + 粗体。"""


# ═══════════════════════════════════════════════════════════
# Diff 差异高亮
# ═══════════════════════════════════════════════════════════

def render_diff_line(line: str) -> Text:
    """渲染 diff 差异行：+绿 -红 @@青 其余默认 dim 样式。"""
    if not line:
        return Text("")
    stripped = line.lstrip()
    if stripped.startswith('@@'):
        return Text(line, style=Style(color="cyan", bold=True))
    first = line[0] if line else ''
    if first == '+':
        return Text(line, style=Style(color="green"))
    elif first == '-':
        return Text(line, style=Style(color="red"))
    else:
        return Text(line, style=Style(dim=True, color="bright_black"))


def render_inline_code_styled(text: str) -> Text:
    """渲染带背景色的内联代码。

    使用亮绿色文字 + 暗灰背景色（复用 _STYLE_INLINE_CODE 常量）。

    Args:
        text: 内联代码内容

    Returns:
        Rich Text 含背景色样式
    """
    return Text(
        f" {text} ",
        style=_STYLE_INLINE_CODE,
    )
